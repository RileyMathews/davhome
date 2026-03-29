from __future__ import annotations

from typing import cast

from django.contrib.auth.models import User
from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from .base import DavView
from dav.core import propmap as core_propmap
from dav.core import props as core_props
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
class DavRootView(DavView):
    allowed_methods = _ROOT_ALLOWED_METHODS

    def get(self, request, *args, **kwargs):
        cast(User, request.user)

        response = HttpResponse(
            b"DAV root",
            content_type="text/plain; charset=utf-8",
        )
        return _dav_common_headers(response)

    def head(self, request, *args, **kwargs):
        cast(User, request.user)

        response = HttpResponse(status=200)
        return _dav_common_headers(response)

    def propfind(self, request, *args, **kwargs):
        user = cast(User, request.user)

        parsed, parse_error = _parse_propfind_payload(request)
        if parse_error is not None:
            return parse_error

        prop_map = core_propmap.build_root_prop_map(user, _principal_href_for_user)

        depth = request.headers.get("Depth", "infinity")
        if parsed is None:
            return HttpResponse(status=400)

        requested = parsed["requested"] if parsed["mode"] == "prop" else None
        root_ok, root_missing = core_props.select_props(prop_map, requested)
        responses = [response_with_props("/dav/", root_ok, root_missing)]

        if depth == "1":
            principal_href = _principal_href_for_user(user)
            home_href = _calendar_home_href_for_user(user)

            principal_map = core_propmap.build_principal_prop_map(
                user,
                user,
                _principal_href_for_user,
                _calendar_home_href_for_user,
            )
            principal_ok, principal_missing = core_props.select_props(
                principal_map,
                requested,
            )
            responses.append(
                response_with_props(principal_href, principal_ok, principal_missing)
            )

            home_map = core_propmap.build_calendar_home_prop_map(
                user,
                user,
                _principal_href_for_user,
            )
            home_ok, home_missing = core_props.select_props(home_map, requested)
            responses.append(response_with_props(home_href, home_ok, home_missing))

        return _xml_response(207, multistatus_document(responses))
