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
class PrincipalsCollectionView(DavView):
    href = "/dav/principals/"
    display_name = "principals"
    allowed_methods = _PRINCIPAL_ALLOWED_METHODS

    def get(self, request, *args, **kwargs):
        cast(User, request.user)

        response = HttpResponse(
            b"Collection",
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
        if parsed is None:
            return HttpResponse(status=400)

        resolved_map = core_propmap.build_collection_prop_map(
            self.display_name,
            user,
            _principal_href_for_user,
        )

        requested = parsed["requested"] if parsed["mode"] == "prop" else None
        ok, missing = core_props.select_props(resolved_map, requested)
        return _xml_response(
            207,
            multistatus_document([response_with_props(self.href, ok, missing)]),
        )
