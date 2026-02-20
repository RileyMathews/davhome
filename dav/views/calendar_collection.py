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
class CalendarCollectionView(View):
    def dispatch(self, request, *args, **kwargs):
        if request.method == "OPTIONS":
            return self.options(request, *args, **kwargs)

        username = kwargs.get("username")
        slug = kwargs.get("slug")
        if not isinstance(username, str) or not isinstance(slug, str):
            return HttpResponse(status=404)

        user, auth_response = _require_dav_user(request)
        if auth_response is not None:
            return auth_response

        owner = get_principal(username)
        if owner is None:
            return HttpResponse(status=404)

        if request.method == "MKCOL":
            return self.mkcol(request, *args, **kwargs)
        if request.method == "MKCALENDAR":
            return self.mkcalendar(
                request,
                *args,
                **kwargs,
                user=cast(User, user),
                owner=cast(User, owner),
            )

        calendar = get_calendar_for_user(user, username, slug)
        if calendar is None and request.method == "REPORT":
            report_root = _parse_xml_body(request.body)
            if report_root is not None and report_root.tag == qname(
                NS_CALDAV,
                "free-busy-query",
            ):
                calendar = Calendar.objects.filter(owner=owner, slug=slug).first()
        if calendar is None:
            return HttpResponse(status=404)

        if request.method == "DELETE":
            return self.delete(
                request,
                *args,
                **kwargs,
                user=cast(User, user),
                owner=cast(User, owner),
                calendar=calendar,
            )
        if request.method == "PROPPATCH":
            return self.proppatch(
                request,
                *args,
                **kwargs,
                user=cast(User, user),
                owner=cast(User, owner),
                calendar=calendar,
            )
        if request.method == "GET":
            return self.get(request, *args, **kwargs, calendar=calendar)
        if request.method == "HEAD":
            return self.head(request, *args, **kwargs, calendar=calendar)
        if request.method == "REPORT":
            return self.report(request, *args, **kwargs, calendar=calendar)
        if request.method == "PROPFIND":
            return self.propfind(
                request,
                *args,
                **kwargs,
                user=cast(User, user),
                calendar=calendar,
            )

        return self.http_method_not_allowed(request, *args, **kwargs)

    def options(self, request, *args, **kwargs):
        response = HttpResponse(status=204)
        response["Allow"] = ", ".join(_CALENDAR_COLLECTION_ALLOWED_METHODS)
        return _dav_common_headers(response)

    def mkcol(self, request, *args, **kwargs):
        if request.body:
            return HttpResponse(status=415)
        request_method = request.method
        request.method = "MKCALENDAR"
        response = self.dispatch(request, *args, **kwargs)
        request.method = request_method
        return response

    def mkcalendar(self, request, username, slug, *args, **kwargs):
        user = cast(User, kwargs.get("user"))
        owner = cast(User, kwargs.get("owner"))

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

    def delete(self, request, *args, **kwargs):
        user = cast(User, kwargs.get("user"))
        owner = cast(User, kwargs.get("owner"))
        calendar = cast(Calendar, kwargs.get("calendar"))

        if owner != user:
            return HttpResponse(status=403)
        calendar.delete()
        response = HttpResponse(status=204)
        return _dav_common_headers(response)

    def proppatch(self, request, username, *args, **kwargs):
        user = cast(User, kwargs.get("user"))
        owner = cast(User, kwargs.get("owner"))
        calendar = cast(Calendar, kwargs.get("calendar"))

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

    def get(self, request, *args, **kwargs):
        calendar = cast(Calendar, kwargs.get("calendar"))
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

    def head(self, request, *args, **kwargs):
        calendar = cast(Calendar, kwargs.get("calendar"))
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

    def report(self, request, *args, **kwargs):
        calendar = cast(Calendar, kwargs.get("calendar"))
        return _handle_report([calendar], request, allow_sync_collection=True)

    def propfind(self, request, username, *args, **kwargs):
        user = cast(User, kwargs.get("user"))
        calendar = cast(Calendar, kwargs.get("calendar"))

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
