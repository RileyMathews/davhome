from __future__ import annotations

# pyright: reportAttributeAccessIssue=false

import hashlib
from typing import cast

from calendars.models import Calendar
from django.contrib.auth.models import User
from django.db.models import Q
from django.http import HttpResponse
from django.utils.http import http_date
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from .base import DavView
from dav.core import propmap as core_propmap
from dav.core import props as core_props
from dav.common import (
    _conditional_not_modified,
    _dav_common_headers,
    _parse_propfind_payload,
    _sync_token_for_calendar,
    _xml_response,
)
from dav.reports.handlers import _handle_report
from dav.views.helpers.identity import _principal_href_for_user
from dav.xml import multistatus_document, response_with_props


_CALENDAR_HOME_ALLOWED_METHODS = ["OPTIONS", "PROPFIND", "GET", "HEAD", "REPORT"]


@method_decorator(csrf_exempt, name="dispatch")
class CalendarHomeView(DavView):
    allowed_methods = _CALENDAR_HOME_ALLOWED_METHODS

    def get(self, request, username, *args, **kwargs):
        if not isinstance(username, str):
            return HttpResponse(status=404)

        user = cast(User, request.user)
        owner = User.objects.filter(username=username).first()
        if owner is None:
            return HttpResponse(status=404)

        calendars = list(
            Calendar.objects.filter(owner=owner)
            .filter(
                Q(owner=user)
                | Q(
                    shares__user=user,
                    shares__accepted_at__isnull=False,
                )
            )
            .distinct()
        )
        if calendars:
            parts = [
                f"{calendar.slug}:{int(calendar.updated_at.timestamp())}"
                for calendar in calendars
            ]
            parts.sort()
            home_etag = (
                f'"{hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()}"'
            )
            home_timestamp = max(
                calendar.updated_at.timestamp() for calendar in calendars
            )
        else:
            home_etag = '"home-empty"'
            home_timestamp = owner.date_joined.timestamp()

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

    def head(self, request, username, *args, **kwargs):
        if not isinstance(username, str):
            return HttpResponse(status=404)

        user = cast(User, request.user)
        owner = User.objects.filter(username=username).first()
        if owner is None:
            return HttpResponse(status=404)

        calendars = list(
            Calendar.objects.filter(owner=owner)
            .filter(
                Q(owner=user)
                | Q(
                    shares__user=user,
                    shares__accepted_at__isnull=False,
                )
            )
            .distinct()
        )
        if calendars:
            parts = [
                f"{calendar.slug}:{int(calendar.updated_at.timestamp())}"
                for calendar in calendars
            ]
            parts.sort()
            home_etag = (
                f'"{hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()}"'
            )
            home_timestamp = max(
                calendar.updated_at.timestamp() for calendar in calendars
            )
        else:
            home_etag = '"home-empty"'
            home_timestamp = owner.date_joined.timestamp()

        if _conditional_not_modified(request, home_etag, home_timestamp):
            response = HttpResponse(status=304)
            response["ETag"] = home_etag
            response["Last-Modified"] = http_date(home_timestamp)
            return _dav_common_headers(response)

        response = HttpResponse(status=200)
        response["ETag"] = home_etag
        response["Last-Modified"] = http_date(home_timestamp)
        return _dav_common_headers(response)

    def report(self, request, username, *args, **kwargs):
        if not isinstance(username, str):
            return HttpResponse(status=404)

        user = cast(User, request.user)
        owner = User.objects.filter(username=username).first()
        if owner is None:
            return HttpResponse(status=404)

        calendars = list(
            Calendar.objects.filter(owner=owner)
            .filter(
                Q(owner=user)
                | Q(
                    shares__user=user,
                    shares__accepted_at__isnull=False,
                )
            )
            .distinct()
        )
        return _handle_report(calendars, request, allow_sync_collection=False)

    def propfind(self, request, username, *args, **kwargs):
        if not isinstance(username, str):
            return HttpResponse(status=404)

        user = cast(User, request.user)
        owner = User.objects.filter(username=username).first()
        if owner is None:
            return HttpResponse(status=404)

        calendars = list(
            Calendar.objects.filter(owner=owner)
            .filter(
                Q(owner=user)
                | Q(
                    shares__user=user,
                    shares__accepted_at__isnull=False,
                )
            )
            .distinct()
        )

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
