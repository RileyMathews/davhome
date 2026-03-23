from __future__ import annotations

# pyright: reportAttributeAccessIssue=false

from xml.etree import ElementTree as ET
from urllib.parse import quote
from typing import Any, cast

from calendars.models import Calendar
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.utils import timezone
from django.utils.http import http_date
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from .base import DavView
from dav.core import paths as core_paths
from dav.core import payloads as core_payloads
from dav.core import propmap as core_propmap
from dav.core import write_ops as core_write_ops
from dav.resolver import (
    get_calendar_for_user,
    get_calendar_object_for_user,
    get_calendar_for_write_user,
    get_principal,
)
from dav.views.helpers.calendar_mutation_payloads import (
    _calendar_collection_proppatch_plan,
    _mkcalendar_props_from_payload,
)
from dav.views.helpers.copy_move import copy_or_move_calendar_object
from dav.views.helpers.ical import _dedupe_duplicate_alarms
from dav.views.helpers.identity import (
    _calendar_home_href_for_user,
    _principal_href_for_user,
)
from dav.views.helpers.parsing import _parse_xml_body
from dav.common import (
    _caldav_error_response,
    _collection_exists,
    _conditional_not_modified,
    _create_calendar_change,
    _dav_error_response,
    _dav_common_headers,
    _etag_for_calendar,
    _etag_for_object,
    _generate_strong_etag,
    _home_etag_and_timestamp,
    _latest_sync_revision,
    _log_dav_create,
    _parse_propfind_payload,
    _proppatch_multistatus_response,
    _sync_token_for_calendar,
    _visible_calendars_for_home,
    _xml_response,
)
from dav.reports.handlers import _build_prop_map_for_object, _handle_report
from dav.xml import NS_CALDAV, NS_DAV, multistatus_document, qname, response_with_props


_ROOT_ALLOWED_METHODS = ["OPTIONS", "PROPFIND", "GET", "HEAD"]
_PRINCIPAL_ALLOWED_METHODS = ["OPTIONS", "PROPFIND", "GET", "HEAD"]
_CALENDAR_HOME_ALLOWED_METHODS = ["OPTIONS", "PROPFIND", "GET", "HEAD", "REPORT"]
_CALENDAR_COLLECTION_ALLOWED_METHODS = [
    "OPTIONS",
    "PROPFIND",
    "GET",
    "HEAD",
    "REPORT",
    "MKCALENDAR",
    "MKCOL",
    "PROPPATCH",
    "DELETE",
]
_CALENDAR_OBJECT_ALLOWED_METHODS = [
    "OPTIONS",
    "PROPFIND",
    "PROPPATCH",
    "GET",
    "HEAD",
    "PUT",
    "DELETE",
    "MKCOL",
    "MKCALENDAR",
    "COPY",
    "MOVE",
]


@method_decorator(csrf_exempt, name="dispatch")
class PrincipalView(DavView):
    allowed_methods = _PRINCIPAL_ALLOWED_METHODS

    def options(self, request, *args, **kwargs):
        response = HttpResponse(status=204)
        response["Allow"] = ", ".join(_PRINCIPAL_ALLOWED_METHODS)
        return _dav_common_headers(response)

    def _resolve_principal(self, request, username):
        user = cast(User, request.user)

        principal = get_principal(username)
        if principal is None:
            return user, None, HttpResponse(status=404)

        if principal != user:
            return user, principal, HttpResponse(status=403)

        return user, principal, None

    def get(self, request, username, *args, **kwargs):
        user, principal, error_response = self._resolve_principal(request, username)
        if error_response is not None:
            return error_response

        response = HttpResponse(
            b"Principal",
            content_type="text/plain; charset=utf-8",
        )
        return _dav_common_headers(response)

    def head(self, request, username, *args, **kwargs):
        user, principal, error_response = self._resolve_principal(request, username)
        if error_response is not None:
            return error_response

        response = HttpResponse(status=200)
        return _dav_common_headers(response)

    def propfind(self, request, username, *args, **kwargs):
        user, principal, error_response = self._resolve_principal(request, username)
        if error_response is not None:
            return error_response

        user = cast(User, user)
        principal = cast(User, principal)

        parsed, parse_error = _parse_propfind_payload(request)
        if parse_error is not None:
            return parse_error
        if parsed is None:
            return HttpResponse(status=400)

        requested = parsed["requested"] if parsed["mode"] == "prop" else None
        principal_map = core_propmap.build_principal_prop_map(
            user,
            principal,
            _principal_href_for_user,
            _calendar_home_href_for_user,
        )
        responses = [
            self.selected_props_response(
                f"/dav/principals/{principal.username}/",
                principal_map,
                requested,
            )
        ]
        return _xml_response(207, multistatus_document(responses))
