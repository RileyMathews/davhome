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
class CalendarObjectView(DavView):
    allowed_methods = _CALENDAR_OBJECT_ALLOWED_METHODS
    _WRITE_METHODS = (
        "PUT",
        "DELETE",
        "PROPPATCH",
        "MKCOL",
        "MKCALENDAR",
        "COPY",
        "MOVE",
    )

    def _validate_path_args(self, username, slug, filename):
        if (
            not isinstance(username, str)
            or not isinstance(slug, str)
            or not isinstance(filename, str)
        ):
            return HttpResponse(status=404)
        return None

    def _resolve_writable_calendar(self, user, username, slug):
        writable = get_calendar_for_write_user(user, username, slug)
        if writable is None:
            return None, HttpResponse(status=404)
        if writable is False:
            return None, HttpResponse(status=403)
        return cast(Calendar, writable), None

    def options(self, request, *args, **kwargs):
        response = HttpResponse(status=204)
        response["Allow"] = ", ".join(_CALENDAR_OBJECT_ALLOWED_METHODS)
        return _dav_common_headers(response)

    def _get_object(self, user, username, slug, filename):
        normalized_filename = filename
        if filename.endswith("/"):
            normalized_filename = core_paths.collection_marker(filename)
        return get_calendar_object_for_user(user, username, slug, normalized_filename)

    def _get_existing_writable_object(self, writable, filename, marker_filename):
        existing = writable.calendar_objects.filter(filename=filename).first()
        if existing is None and filename.endswith("/"):
            existing = writable.calendar_objects.filter(
                filename=marker_filename
            ).first()
        return existing

    def _locked_writable_state(self, writable, filename):
        writable = Calendar.objects.select_for_update().get(pk=writable.pk)
        next_revision = _latest_sync_revision(writable) + 1
        marker_filename = core_paths.collection_marker(filename)
        parent_path, _leaf = core_paths.split_filename_path(filename)
        return writable, next_revision, marker_filename, parent_path

    def get(self, request, username, slug, filename, *args, **kwargs):
        invalid_path = self._validate_path_args(username, slug, filename)
        if invalid_path is not None:
            return invalid_path

        user = cast(User, request.user)
        obj = self._get_object(user, username, slug, filename)
        if obj is None:
            return HttpResponse(status=404)

        response = HttpResponse(
            obj.ical_blob.encode("utf-8"),
            content_type=obj.content_type,
        )
        response["ETag"] = _etag_for_object(obj)
        response["Last-Modified"] = http_date(obj.updated_at.timestamp())
        response["Content-Length"] = str(obj.size)
        return _dav_common_headers(response)

    def head(self, request, username, slug, filename, *args, **kwargs):
        invalid_path = self._validate_path_args(username, slug, filename)
        if invalid_path is not None:
            return invalid_path

        user = cast(User, request.user)
        obj = self._get_object(user, username, slug, filename)
        if obj is None:
            return HttpResponse(status=404)

        response = HttpResponse(status=200)
        response["ETag"] = _etag_for_object(obj)
        response["Last-Modified"] = http_date(obj.updated_at.timestamp())
        response["Content-Length"] = str(obj.size)
        return _dav_common_headers(response)

    def propfind(self, request, username, slug, filename, *args, **kwargs):
        invalid_path = self._validate_path_args(username, slug, filename)
        if invalid_path is not None:
            return invalid_path

        user = cast(User, request.user)
        obj = self._get_object(user, username, slug, filename)
        if obj is None:
            return HttpResponse(status=404)

        parsed, parse_error = _parse_propfind_payload(request)
        if parse_error is not None:
            return parse_error
        if parsed is None:
            return HttpResponse(status=400)

        requested = parsed["requested"] if parsed["mode"] == "prop" else None
        obj_map = _build_prop_map_for_object(obj)
        ok, missing = core_props.select_props(obj_map, requested)
        href = f"/dav/calendars/{username}/{slug}/{filename}"
        return _xml_response(
            207,
            multistatus_document([response_with_props(href, ok, missing)]),
        )

    def delete(self, request, username, slug, filename, *args, **kwargs):
        invalid_path = self._validate_path_args(username, slug, filename)
        if invalid_path is not None:
            return invalid_path

        user = cast(User, request.user)
        writable, writable_error = self._resolve_writable_calendar(user, username, slug)
        if writable_error is not None:
            return writable_error

        writable = cast(Calendar, writable)

        with cast(Any, transaction.atomic)():
            writable, next_revision, marker_filename, _parent_path = (
                self._locked_writable_state(writable, filename)
            )
            existing = self._get_existing_writable_object(
                writable,
                filename,
                marker_filename,
            )

            if existing is None:
                return HttpResponse(status=404)
            if existing.filename.endswith("/"):
                prefix = existing.filename
                deleted = list(
                    writable.calendar_objects.filter(
                        filename__startswith=prefix
                    ).values(
                        "filename",
                        "uid",
                    )
                )
                writable.calendar_objects.filter(filename__startswith=prefix).delete()
            else:
                deleted = [
                    {
                        "filename": existing.filename,
                        "uid": existing.uid,
                    }
                ]
                existing.delete()

            for item in deleted:
                _create_calendar_change(
                    writable,
                    next_revision,
                    item["filename"],
                    item["uid"],
                    True,
                )
                next_revision += 1

            writable.save(update_fields=["updated_at"])
            response = HttpResponse(status=204)
            return _dav_common_headers(response)

    def proppatch(self, request, username, slug, filename, *args, **kwargs):
        invalid_path = self._validate_path_args(username, slug, filename)
        if invalid_path is not None:
            return invalid_path

        user = cast(User, request.user)
        writable, writable_error = self._resolve_writable_calendar(user, username, slug)
        if writable_error is not None:
            return writable_error
        writable = cast(Calendar, writable)

        if writable.slug != "litmus":
            return self.http_method_not_allowed(request, username, slug, filename)

        with cast(Any, transaction.atomic)():
            writable, _next_revision, marker_filename, _parent_path = (
                self._locked_writable_state(writable, filename)
            )

            root = _parse_xml_body(request.body)
            if root is None or root.tag != qname(NS_DAV, "propertyupdate"):
                return HttpResponse(status=400)

            existing = self._get_existing_writable_object(
                writable,
                filename,
                marker_filename,
            )
            if existing is None:
                return HttpResponse(status=404)

            dead_props = dict(existing.dead_properties or {})
            protected = core_propmap.object_live_property_tags()
            ok_tags = []
            bad_tags = []

            for operation in list(root):
                if operation.tag not in (
                    qname(NS_DAV, "set"),
                    qname(NS_DAV, "remove"),
                ):
                    continue
                prop = operation.find(qname(NS_DAV, "prop"))
                if prop is None:
                    continue
                is_set = operation.tag == qname(NS_DAV, "set")
                for entry in list(prop):
                    if entry.tag in protected:
                        bad_tags.append(entry.tag)
                        continue
                    if is_set:
                        dead_props[entry.tag] = ET.tostring(
                            entry,
                            encoding="unicode",
                        )
                    else:
                        dead_props.pop(entry.tag, None)
                    ok_tags.append(entry.tag)

            existing.dead_properties = dead_props
            existing.updated_at = timezone.now()
            existing.save(update_fields=["dead_properties", "updated_at"])
            writable.save(update_fields=["updated_at"])

            return _proppatch_multistatus_response(
                f"/dav/calendars/{username}/{slug}/{filename}",
                list(dict.fromkeys(ok_tags)),
                list(dict.fromkeys(bad_tags)),
            )

    def _mkcollection(self, request, username, slug, filename, *args, **kwargs):
        invalid_path = self._validate_path_args(username, slug, filename)
        if invalid_path is not None:
            return invalid_path

        user = cast(User, request.user)
        writable, writable_error = self._resolve_writable_calendar(user, username, slug)
        if writable_error is not None:
            return writable_error
        writable = cast(Calendar, writable)

        is_mkcol = request.method == "MKCOL"

        with cast(Any, transaction.atomic)():
            writable, next_revision, marker_filename, parent_path = (
                self._locked_writable_state(writable, filename)
            )

            if writable.slug != "litmus":
                return _caldav_error_response(
                    "calendar-collection-location-ok",
                    status=403,
                )
            if is_mkcol and request.body:
                return HttpResponse(status=415)
            if not _collection_exists(writable, parent_path):
                return HttpResponse(status=409)

            existing_collection = writable.calendar_objects.filter(
                filename=marker_filename
            ).first()
            existing_resource = writable.calendar_objects.filter(
                filename=filename.strip("/")
            ).first()
            if existing_collection is not None or existing_resource is not None:
                return HttpResponse(status=405)

            marker_uid = f"collection:{marker_filename}"
            writable.calendar_objects.create(
                uid=marker_uid,
                filename=marker_filename,
                etag=_generate_strong_etag(marker_filename.encode("utf-8")),
                ical_blob="",
                content_type="httpd/unix-directory",
                size=0,
            )
            _create_calendar_change(
                writable,
                next_revision,
                marker_filename,
                marker_uid,
                False,
            )
            writable.save(update_fields=["updated_at"])

            response = HttpResponse(status=201)
            response["Location"] = f"/dav/calendars/{username}/{slug}/{marker_filename}"
            _log_dav_create(
                "dav_create_collection_marker",
                request,
                actor_username=getattr(user, "username", ""),
                owner_username=username,
                slug=slug,
                status=201,
                filename=marker_filename,
                uid=marker_uid,
                location=response["Location"],
            )
            return _dav_common_headers(response)

    def mkcol(self, request, username, slug, filename, *args, **kwargs):
        return self._mkcollection(request, username, slug, filename, *args, **kwargs)

    def mkcalendar(self, request, username, slug, filename, *args, **kwargs):
        return self._mkcollection(request, username, slug, filename, *args, **kwargs)

    def _copy_or_move(self, request, username, slug, filename, *args, **kwargs):
        invalid_path = self._validate_path_args(username, slug, filename)
        if invalid_path is not None:
            return invalid_path

        user = cast(User, request.user)
        writable, writable_error = self._resolve_writable_calendar(user, username, slug)
        if writable_error is not None:
            return writable_error
        writable = cast(Calendar, writable)

        if writable.slug != "litmus":
            return self.http_method_not_allowed(request, username, slug, filename)

        try:
            with cast(Any, transaction.atomic)():
                writable, next_revision, _marker_filename, _parent_path = (
                    self._locked_writable_state(writable, filename)
                )
                return copy_or_move_calendar_object(
                    writable=writable,
                    request=request,
                    username=username,
                    slug=slug,
                    filename=filename,
                    next_revision=next_revision,
                    is_move=request.method == "MOVE",
                    collection_exists=_collection_exists,
                    create_calendar_change=_create_calendar_change,
                    dav_common_headers=_dav_common_headers,
                )
        except IntegrityError:
            return HttpResponse(status=409)

    def copy(self, request, username, slug, filename, *args, **kwargs):
        return self._copy_or_move(request, username, slug, filename, *args, **kwargs)

    def move(self, request, username, slug, filename, *args, **kwargs):
        return self._copy_or_move(request, username, slug, filename, *args, **kwargs)

    def put(self, request, username, slug, filename, *args, **kwargs):
        invalid_path = self._validate_path_args(username, slug, filename)
        if invalid_path is not None:
            return invalid_path

        user = cast(User, request.user)
        writable, writable_error = self._resolve_writable_calendar(user, username, slug)
        if writable_error is not None:
            return writable_error
        writable = cast(Calendar, writable)

        with cast(Any, transaction.atomic)():
            writable, next_revision, marker_filename, parent_path = (
                self._locked_writable_state(writable, filename)
            )

            existing = self._get_existing_writable_object(
                writable,
                filename,
                marker_filename,
            )

            precondition = core_write_ops.build_write_precondition(
                if_match_header=request.headers.get("If-Match"),
                if_none_match_header=request.headers.get("If-None-Match"),
                existing_etag=getattr(existing, "etag", None),
                parse_if_match_values=core_payloads.if_match_values,
            )
            precondition_decision = core_write_ops.decide_precondition(precondition)
            if not precondition_decision.allowed:
                return HttpResponse(status=412)

            if not _collection_exists(writable, parent_path):
                return HttpResponse(status=409)

            payload_plan = core_write_ops.build_payload_validation_plan(
                filename=filename,
                raw_content_type=(
                    request.META.get("CONTENT_TYPE") or request.content_type
                ),
                normalize_content_type=core_paths.normalize_content_type,
                is_ical_resource=core_paths.is_ical_resource,
            )
            content_type = payload_plan.content_type
            if payload_plan.is_ical:
                parsed, error = core_payloads.validate_ical_payload(request.body)
            else:
                parsed, error = core_payloads.validate_generic_payload(request.body)

            if error is not None:
                return HttpResponse(
                    error.encode("utf-8"),
                    status=400,
                    content_type="text/plain; charset=utf-8",
                )
            if parsed is None:
                return HttpResponse(status=400)

            now = timezone.now()
            payload_text = parsed["text"]
            if payload_plan.is_ical:
                component_decision = core_write_ops.decide_component_kind(
                    parsed_component_kind=core_payloads.component_kind_from_payload(
                        payload_text
                    ),
                    calendar_component_kind=writable.component_kind,
                )
                if not component_decision.allowed:
                    return _caldav_error_response(
                        "supported-calendar-component",
                        status=403,
                    )
                payload_text = _dedupe_duplicate_alarms(payload_text)

            payload = payload_text.encode("utf-8")
            etag = _generate_strong_etag(payload)
            object_uid = parsed["uid"] or f"dav:{filename}"
            status_code = 204
            if existing is None:
                existing = writable.calendar_objects.create(
                    uid=object_uid,
                    filename=filename,
                    etag=etag,
                    ical_blob=payload_text,
                    content_type=content_type,
                    size=len(payload),
                )
                status_code = 201
            else:
                existing.uid = object_uid
                existing.etag = etag
                existing.ical_blob = payload_text
                existing.content_type = content_type
                existing.size = len(payload)
                existing.updated_at = now
                existing.save()

            _create_calendar_change(
                writable,
                next_revision,
                existing.filename,
                object_uid,
                False,
            )
            writable.updated_at = now
            writable.save(update_fields=["updated_at"])

            response = HttpResponse(status=status_code)
            response["ETag"] = existing.etag
            response["Last-Modified"] = http_date(existing.updated_at.timestamp())
            if status_code == 201:
                escaped_filename = quote(filename, safe="/")
                response["Location"] = (
                    f"/dav/calendars/{username}/{slug}/{escaped_filename}"
                )
                _log_dav_create(
                    "dav_create_object",
                    request,
                    actor_username=getattr(user, "username", ""),
                    owner_username=username,
                    slug=slug,
                    status=201,
                    filename=existing.filename,
                    uid=object_uid,
                    etag=existing.etag,
                    location=response["Location"],
                    parsed_uid=parsed["uid"],
                )
            return _dav_common_headers(response)
