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
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from dav.auth import get_dav_user, unauthorized_response
from .base import DavView
from dav.core import paths as core_paths
from dav.core import payloads as core_payloads
from dav.core import propmap as core_propmap
from dav.core import props as core_props
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
    _dav_username_for_guid,
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
    _not_allowed,
    _parse_propfind_payload,
    _proppatch_multistatus_response,
    _require_dav_user,
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
class CalendarHomeView(View):
    def dispatch(self, request, *args, **kwargs):
        if request.method == "OPTIONS":
            return self.options(request, *args, **kwargs)

        username = kwargs.get("username")
        if not isinstance(username, str):
            return HttpResponse(status=404)

        user, auth_response = _require_dav_user(request)
        if auth_response is not None:
            return auth_response

        owner = get_principal(username)
        if owner is None:
            return HttpResponse(status=404)

        user = cast(User, user)
        owner = cast(User, owner)

        home_etag, home_timestamp = _home_etag_and_timestamp(owner, user)

        if request.method == "GET":
            return self.get(
                request,
                *args,
                **kwargs,
                user=user,
                owner=owner,
                home_etag=home_etag,
                home_timestamp=home_timestamp,
            )
        if request.method == "HEAD":
            return self.head(
                request,
                *args,
                **kwargs,
                user=user,
                owner=owner,
                home_etag=home_etag,
                home_timestamp=home_timestamp,
            )

        calendars = _visible_calendars_for_home(owner, user)

        if request.method == "REPORT":
            return self.report(
                request,
                *args,
                **kwargs,
                calendars=calendars,
            )
        if request.method == "PROPFIND":
            return self.propfind(
                request,
                *args,
                **kwargs,
                user=user,
                owner=owner,
                calendars=calendars,
            )

        return self.http_method_not_allowed(request, *args, **kwargs)

    def http_method_not_allowed(self, request, *args, **kwargs):
        return _not_allowed(
            request,
            _CALENDAR_HOME_ALLOWED_METHODS,
            username=kwargs.get("username"),
        )

    def options(self, request, *args, **kwargs):
        response = HttpResponse(status=204)
        response["Allow"] = ", ".join(_CALENDAR_HOME_ALLOWED_METHODS)
        return _dav_common_headers(response)

    def get(self, request, *args, **kwargs):
        home_etag = cast(str, kwargs.get("home_etag"))
        home_timestamp = cast(float, kwargs.get("home_timestamp"))
        if _conditional_not_modified(request, home_etag, home_timestamp):
            response = HttpResponse(status=304)
            response["ETag"] = home_etag
            response["Last-Modified"] = http_date(home_timestamp)
            return _dav_common_headers(response)

        response = HttpResponse(
            b"Calendar home",
            content_type="text/plain; charset=utf-8",
        )
        response["ETag"] = home_etag
        response["Last-Modified"] = http_date(home_timestamp)
        return _dav_common_headers(response)

    def head(self, request, *args, **kwargs):
        home_etag = cast(str, kwargs.get("home_etag"))
        home_timestamp = cast(float, kwargs.get("home_timestamp"))
        if _conditional_not_modified(request, home_etag, home_timestamp):
            response = HttpResponse(status=304)
            response["ETag"] = home_etag
            response["Last-Modified"] = http_date(home_timestamp)
            return _dav_common_headers(response)

        response = HttpResponse(status=200)
        response["ETag"] = home_etag
        response["Last-Modified"] = http_date(home_timestamp)
        return _dav_common_headers(response)

    def report(self, request, *args, **kwargs):
        calendars = cast(list, kwargs.get("calendars"))
        return _handle_report(calendars, request, allow_sync_collection=False)

    def propfind(self, request, *args, **kwargs):
        user = cast(User, kwargs.get("user"))
        owner = cast(User, kwargs.get("owner"))
        calendars = cast(list, kwargs.get("calendars"))

        parsed, parse_error = _parse_propfind_payload(request)
        if parse_error is not None:
            return parse_error
        if parsed is None:
            return HttpResponse(status=400)

        depth = request.headers.get("Depth", "infinity")
        requested = parsed["requested"] if parsed["mode"] == "prop" else None
        home_map = core_propmap.build_calendar_home_prop_map(
            owner,
            user,
            _principal_href_for_user,
        )
        home_ok, home_missing = core_props.select_props(home_map, requested)
        responses = [
            response_with_props(
                f"/dav/calendars/{owner.username}/",
                home_ok,
                home_missing,
            )
        ]

        if depth == "1":
            for calendar in calendars:
                cal_map = core_propmap.build_calendar_collection_prop_map(
                    calendar,
                    user,
                    _principal_href_for_user,
                    _sync_token_for_calendar,
                )
                cal_ok, cal_missing = core_props.select_props(cal_map, requested)
                href = f"/dav/calendars/{owner.username}/{calendar.slug}/"
                responses.append(response_with_props(href, cal_ok, cal_missing))

        return _xml_response(207, multistatus_document(responses))
