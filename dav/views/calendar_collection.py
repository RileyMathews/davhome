from __future__ import annotations

from typing import cast

from calendars.models import Calendar
from django.contrib.auth.models import User
from django.http import HttpRequest, HttpResponse
from django.utils.http import http_date
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from .base import DavView, current_user
from dav.core import propmap as core_propmap
from dav.core import props as core_props
from dav.resolver import (
    get_calendar_for_user,
    get_principal,
)
from dav.views.helpers.calendar_mutation_payloads import (
    _calendar_collection_proppatch_plan,
    _mkcalendar_props_from_payload,
)
from dav.views.helpers.identity import (
    _principal_href_for_user,
)
from dav.views.helpers.parsing import _parse_xml_body
from dav.common import (
    _caldav_error_response,
    _conditional_not_modified,
    _dav_error_response,
    _dav_common_headers,
    _etag_for_calendar,
    _log_dav_create,
    _parse_propfind_payload,
    _proppatch_multistatus_response,
    _sync_token_for_calendar,
    _xml_response,
)
from dav.reports.handlers import _build_prop_map_for_object, _handle_report
from dav.xml import NS_CALDAV, NS_DAV, multistatus_document, qname, response_with_props


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

@method_decorator(csrf_exempt, name="dispatch")
class CalendarCollectionView(DavView):
    allowed_methods = _CALENDAR_COLLECTION_ALLOWED_METHODS

    def _resolve_owner(
        self, request: HttpRequest, username: str
    ) -> tuple[User, User] | HttpResponse:
        user = current_user(request)
        owner = get_principal(username)
        if owner is None:
            return HttpResponse(status=404)

        return user, cast(User, owner)

    def _resolve_calendar(
        self,
        request: HttpRequest,
        username: str,
        slug: str,
        *,
        allow_report_fallback: bool = False,
    ) -> Calendar | HttpResponse:
        user = current_user(request)
        calendar = get_calendar_for_user(user, username, slug)
        if calendar is None and allow_report_fallback:
            owner = get_principal(username)
            report_root = _parse_xml_body(request.body)
            if (
                owner is not None
                and report_root is not None
                and report_root.tag == qname(NS_CALDAV, "free-busy-query")
            ):
                calendar = Calendar.objects.filter(owner=owner, slug=slug).first()
        if calendar is None:
            return HttpResponse(status=404)
        return calendar

    def mkcol(self, request, *args, **kwargs):
        if request.body:
            return HttpResponse(status=415)
        return self.mkcalendar(request, *args, **kwargs)

    def mkcalendar(
        self,
        request: HttpRequest,
        username: str,
        slug: str,
        *args: object,
        **kwargs: object,
    ) -> HttpResponse:
        owner_state = self._resolve_owner(request, username)
        if isinstance(owner_state, HttpResponse):
            return owner_state

        user, owner = owner_state

        if owner != user:
            return HttpResponse(status=403)

        existing = Calendar.objects.filter(owner=owner, slug=slug).first()
        if existing is not None:
            return _dav_error_response("resource-must-be-null")

        properties, bad_props, property_error = _mkcalendar_props_from_payload(
            request.body,
            _caldav_error_response,
        )
        if property_error is not None:
            return property_error
        if properties is None:
            return HttpResponse(status=400)
        if bad_props:
            return _proppatch_multistatus_response(
                f"/dav/calendars/{username}/{slug}/",
                [],
                bad_props,
            )

        calendar = Calendar.objects.create(
            owner=owner,
            slug=slug,
            name=(properties.get("display_name") or slug),
            description=(properties.get("description") or ""),
            timezone=(properties.get("timezone") or "UTC"),
            color=(properties.get("color") or ""),
            sort_order=properties.get("sort_order"),
            component_kind=(
                properties.get("component_kind") or Calendar.COMPONENT_VEVENT
            ),
        )
        response = HttpResponse(status=201)
        response["Location"] = f"/dav/calendars/{username}/{calendar.slug}/"
        _log_dav_create(
            "dav_create_calendar",
            request,
            actor_username=getattr(user, "username", ""),
            owner_username=username,
            slug=slug,
            status=201,
            location=response["Location"],
            calendar_id=str(calendar.id),
        )
        return _dav_common_headers(response)

    def delete(
        self,
        request: HttpRequest,
        username: str,
        slug: str,
        *args: object,
        **kwargs: object,
    ) -> HttpResponse:
        owner_state = self._resolve_owner(request, username)
        if isinstance(owner_state, HttpResponse):
            return owner_state

        calendar = self._resolve_calendar(request, username, slug)
        if isinstance(calendar, HttpResponse):
            return calendar

        user, owner = owner_state

        if owner != user:
            return HttpResponse(status=403)
        calendar.delete()
        response = HttpResponse(status=204)
        return _dav_common_headers(response)

    def proppatch(
        self,
        request: HttpRequest,
        username: str,
        slug: str,
        *args: object,
        **kwargs: object,
    ) -> HttpResponse:
        owner_state = self._resolve_owner(request, username)
        if isinstance(owner_state, HttpResponse):
            return owner_state

        calendar = self._resolve_calendar(request, username, slug)
        if isinstance(calendar, HttpResponse):
            return calendar

        user, owner = owner_state

        if owner != user:
            return HttpResponse(status=403)

        root = _parse_xml_body(request.body)
        if root is None or root.tag != qname(NS_DAV, "propertyupdate"):
            return HttpResponse(status=400)

        pending_values, update_fields, ok_tags, bad_tags = (
            _calendar_collection_proppatch_plan(
                root,
                calendar.slug,
                {
                    "name": calendar.name,
                    "description": calendar.description,
                    "timezone": calendar.timezone,
                    "color": calendar.color,
                    "sort_order": calendar.sort_order,
                },
            )
        )

        if update_fields:
            for key, value in pending_values.items():
                setattr(calendar, key, value)
            update_fields.add("updated_at")
            calendar.save(update_fields=list(update_fields))

        return _proppatch_multistatus_response(
            f"/dav/calendars/{username}/{calendar.slug}/",
            ok_tags,
            bad_tags,
        )

    def get(
        self,
        request: HttpRequest,
        username: str,
        slug: str,
        *args: object,
        **kwargs: object,
    ) -> HttpResponse:
        calendar = self._resolve_calendar(request, username, slug)
        if isinstance(calendar, HttpResponse):
            return calendar

        calendar_etag = _etag_for_calendar(calendar)
        calendar_timestamp = calendar.updated_at.timestamp()
        if _conditional_not_modified(request, calendar_etag, calendar_timestamp):
            response = HttpResponse(status=304)
            response["ETag"] = calendar_etag
            response["Last-Modified"] = http_date(calendar_timestamp)
            return _dav_common_headers(response)

        response = HttpResponse(
            f"Calendar {calendar.name}".encode("utf-8"),
            content_type="text/plain; charset=utf-8",
        )
        response["ETag"] = calendar_etag
        response["Last-Modified"] = http_date(calendar_timestamp)
        return _dav_common_headers(response)

    def head(
        self,
        request: HttpRequest,
        username: str,
        slug: str,
        *args: object,
        **kwargs: object,
    ) -> HttpResponse:
        calendar = self._resolve_calendar(request, username, slug)
        if isinstance(calendar, HttpResponse):
            return calendar

        calendar_etag = _etag_for_calendar(calendar)
        calendar_timestamp = calendar.updated_at.timestamp()
        if _conditional_not_modified(request, calendar_etag, calendar_timestamp):
            response = HttpResponse(status=304)
            response["ETag"] = calendar_etag
            response["Last-Modified"] = http_date(calendar_timestamp)
            return _dav_common_headers(response)

        response = HttpResponse(status=200)
        response["ETag"] = calendar_etag
        response["Last-Modified"] = http_date(calendar_timestamp)
        return _dav_common_headers(response)

    def report(
        self,
        request: HttpRequest,
        username: str,
        slug: str,
        *args: object,
        **kwargs: object,
    ) -> HttpResponse:
        calendar = self._resolve_calendar(
            request,
            username,
            slug,
            allow_report_fallback=True,
        )
        if isinstance(calendar, HttpResponse):
            return calendar

        return _handle_report([calendar], request, allow_sync_collection=True)

    def propfind(
        self,
        request: HttpRequest,
        username: str,
        slug: str,
        *args: object,
        **kwargs: object,
    ) -> HttpResponse:
        user = current_user(request)
        calendar = self._resolve_calendar(request, username, slug)
        if isinstance(calendar, HttpResponse):
            return calendar

        propfind_etag = _etag_for_calendar(calendar)
        propfind_timestamp = calendar.updated_at.timestamp()
        if _conditional_not_modified(request, propfind_etag, propfind_timestamp):
            response = HttpResponse(status=304)
            response["ETag"] = propfind_etag
            response["Last-Modified"] = http_date(propfind_timestamp)
            return _dav_common_headers(response)

        parsed, parse_error = _parse_propfind_payload(request)
        if parse_error is not None:
            return parse_error
        if parsed is None:
            return HttpResponse(status=400)

        depth = request.headers.get("Depth", "infinity")
        requested = parsed["requested"] if parsed["mode"] == "prop" else None
        cal_map = core_propmap.build_calendar_collection_prop_map(
            calendar,
            user,
            _principal_href_for_user,
            _sync_token_for_calendar,
        )
        cal_ok, cal_missing = core_props.select_props(cal_map, requested)
        responses = [
            response_with_props(
                f"/dav/calendars/{username}/{calendar.slug}/",
                cal_ok,
                cal_missing,
            )
        ]

        if depth == "1":
            for obj in calendar.calendar_objects.all():
                obj_map = _build_prop_map_for_object(obj)
                obj_ok, obj_missing = core_props.select_props(obj_map, requested)
                href = f"/dav/calendars/{username}/{calendar.slug}/{obj.filename}"
                responses.append(response_with_props(href, obj_ok, obj_missing))

        return _xml_response(
            207,
            multistatus_document(responses),
            {
                "ETag": propfind_etag,
                "Last-Modified": http_date(propfind_timestamp),
            },
        )
