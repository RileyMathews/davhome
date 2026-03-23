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
class CalendarCollectionView(DavView):
    allowed_methods = _CALENDAR_COLLECTION_ALLOWED_METHODS

    def _resolve_owner(self, username):
        if not isinstance(username, str):
            return None, None, HttpResponse(status=404)

        user = cast(User, self.request.user)
        owner = get_principal(username)
        if owner is None:
            return None, None, HttpResponse(status=404)

        return user, cast(User, owner), None

    def _resolve_calendar(
        self, request, username, slug, *, allow_report_fallback=False
    ):
        user = cast(User, request.user)
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
            return None, HttpResponse(status=404)
        return calendar, None

    def options(self, request, *args, **kwargs):
        response = HttpResponse(status=204)
        response["Allow"] = ", ".join(_CALENDAR_COLLECTION_ALLOWED_METHODS)
        return _dav_common_headers(response)

    def mkcol(self, request, *args, **kwargs):
        if request.body:
            return HttpResponse(status=415)
        return self.mkcalendar(request, *args, **kwargs)

    def mkcalendar(self, request, username, slug, *args, **kwargs):
        user, owner, error_response = self._resolve_owner(username)
        if error_response is not None:
            return error_response

        user = cast(User, user)
        owner = cast(User, owner)

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
        username = kwargs.get("username")
        slug = kwargs.get("slug")
        if not isinstance(username, str) or not isinstance(slug, str):
            return HttpResponse(status=404)

        user, owner, error_response = self._resolve_owner(username)
        if error_response is not None:
            return error_response

        calendar, calendar_error = self._resolve_calendar(request, username, slug)
        if calendar_error is not None:
            return calendar_error

        user = cast(User, user)
        owner = cast(User, owner)
        calendar = cast(Calendar, calendar)

        if owner != user:
            return HttpResponse(status=403)
        calendar.delete()
        response = HttpResponse(status=204)
        return _dav_common_headers(response)

    def proppatch(self, request, username, *args, **kwargs):
        slug = kwargs.get("slug")
        if not isinstance(slug, str):
            return HttpResponse(status=404)

        user, owner, error_response = self._resolve_owner(username)
        if error_response is not None:
            return error_response

        calendar, calendar_error = self._resolve_calendar(request, username, slug)
        if calendar_error is not None:
            return calendar_error

        user = cast(User, user)
        owner = cast(User, owner)
        calendar = cast(Calendar, calendar)

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
        username = kwargs.get("username")
        slug = kwargs.get("slug")
        if not isinstance(username, str) or not isinstance(slug, str):
            return HttpResponse(status=404)

        calendar, calendar_error = self._resolve_calendar(request, username, slug)
        if calendar_error is not None:
            return calendar_error

        calendar = cast(Calendar, calendar)
        calendar_etag = _etag_for_calendar(calendar)
        calendar_timestamp = calendar.updated_at.timestamp()
        not_modified = self.not_modified_response(
            request,
            etag=calendar_etag,
            timestamp=calendar_timestamp,
            conditional_not_modified=_conditional_not_modified,
        )
        if not_modified is not None:
            return not_modified

        response = HttpResponse(
            f"Calendar {calendar.name}".encode("utf-8"),
            content_type="text/plain; charset=utf-8",
        )
        self.apply_resource_state_headers(response, calendar_etag, calendar_timestamp)
        return self.apply_dav_headers(response)

    def head(self, request, *args, **kwargs):
        username = kwargs.get("username")
        slug = kwargs.get("slug")
        if not isinstance(username, str) or not isinstance(slug, str):
            return HttpResponse(status=404)

        calendar, calendar_error = self._resolve_calendar(request, username, slug)
        if calendar_error is not None:
            return calendar_error

        calendar = cast(Calendar, calendar)
        calendar_etag = _etag_for_calendar(calendar)
        calendar_timestamp = calendar.updated_at.timestamp()
        not_modified = self.not_modified_response(
            request,
            etag=calendar_etag,
            timestamp=calendar_timestamp,
            conditional_not_modified=_conditional_not_modified,
        )
        if not_modified is not None:
            return not_modified

        response = HttpResponse(status=200)
        self.apply_resource_state_headers(response, calendar_etag, calendar_timestamp)
        return self.apply_dav_headers(response)

    def report(self, request, *args, **kwargs):
        username = kwargs.get("username")
        slug = kwargs.get("slug")
        if not isinstance(username, str) or not isinstance(slug, str):
            return HttpResponse(status=404)

        calendar, calendar_error = self._resolve_calendar(
            request,
            username,
            slug,
            allow_report_fallback=True,
        )
        if calendar_error is not None:
            return calendar_error

        calendar = cast(Calendar, calendar)
        return _handle_report([calendar], request, allow_sync_collection=True)

    def propfind(self, request, username, *args, **kwargs):
        slug = kwargs.get("slug")
        if not isinstance(slug, str):
            return HttpResponse(status=404)

        user = cast(User, request.user)
        calendar, calendar_error = self._resolve_calendar(request, username, slug)
        if calendar_error is not None:
            return calendar_error

        calendar = cast(Calendar, calendar)

        propfind_etag = _etag_for_calendar(calendar)
        propfind_timestamp = calendar.updated_at.timestamp()
        not_modified = self.not_modified_response(
            request,
            etag=propfind_etag,
            timestamp=propfind_timestamp,
            conditional_not_modified=_conditional_not_modified,
        )
        if not_modified is not None:
            return not_modified

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
        responses = [
            self.selected_props_response(
                f"/dav/calendars/{username}/{calendar.slug}/",
                cal_map,
                requested,
            )
        ]

        if depth == "1":
            for obj in calendar.calendar_objects.all():
                obj_map = _build_prop_map_for_object(obj)
                href = f"/dav/calendars/{username}/{calendar.slug}/{obj.filename}"
                responses.append(self.selected_props_response(href, obj_map, requested))

        headers = {}
        self.apply_resource_state_headers(headers, propfind_etag, propfind_timestamp)
        return _xml_response(207, multistatus_document(responses), headers)
