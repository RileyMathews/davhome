from __future__ import annotations

from typing import cast

from django.contrib.auth.models import User
from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from .base import DavView
from dav.core import propmap as core_propmap
from dav.core import props as core_props
from dav.resolver import (
    get_principal,
)
from dav.views.helpers.identity import (
    _calendar_home_href_for_user,
    _principal_href_for_user,
)
from dav.common import (
    _dav_common_headers,
    _parse_propfind_payload,
    _xml_response,
)
from dav.xml import multistatus_document, response_with_props


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
        ok, missing = core_props.select_props(principal_map, requested)
        responses = [
            response_with_props(f"/dav/principals/{principal.username}/", ok, missing)
        ]
        return _xml_response(207, multistatus_document(responses))
