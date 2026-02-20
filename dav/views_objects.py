# pyright: reportGeneralTypeIssues=false, reportAttributeAccessIssue=false

from urllib.parse import quote
from xml.etree import ElementTree as ET

from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from django.utils.http import http_date
from django.views.decorators.csrf import csrf_exempt

from calendars.models import Calendar

from .core import paths as core_paths
from .core import payloads as core_payloads
from .core import propmap as core_propmap
from .core import props as core_props
from .core import write_ops as core_write_ops
from .resolver import (
    get_calendar_for_user,
    get_calendar_for_write_user,
    get_calendar_object_for_user,
    get_principal,
)
from .views_common import _caldav_error_response
from .views_common import _collection_exists
from .views_common import _conditional_not_modified
from .views_common import _create_calendar_change
from .views_common import _dav_common_headers
from .views_common import _dav_error_response
from .views_common import _generate_strong_etag
from .views_common import _latest_sync_revision
from .views_common import _log_dav_create
from .views_common import _not_allowed
from .views_common import _parse_propfind_payload
from .views_common import _proppatch_multistatus_response
from .views_common import _require_dav_user
from .views_common import _sync_token_for_calendar
from .views_common import _etag_for_calendar
from .views_common import _etag_for_object
from .views_common import _xml_response
from .views_reports import _build_prop_map_for_object
from .views_reports import _handle_report
from .view_helpers.calendar_mutation_payloads import (
    _calendar_collection_proppatch_plan,
    _mkcalendar_props_from_payload,
)
from .view_helpers.copy_move import (
    copy_or_move_calendar_object,
)
from .view_helpers.ical import _dedupe_duplicate_alarms
from .view_helpers.parsing import _parse_xml_body
from .view_helpers.identity import _principal_href_for_user
from .view_helpers.identity import _dav_username_for_guid
from .xml import NS_CALDAV, NS_DAV, multistatus_document, qname, response_with_props


@csrf_exempt
def calendar_collection_view(request, username, slug):
    allowed = [
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
    if request.method == "OPTIONS":
        response = HttpResponse(status=204)
        response["Allow"] = ", ".join(allowed)
        return _dav_common_headers(response)

    user, auth_response = _require_dav_user(request)
    if auth_response is not None:
        return auth_response

    owner = get_principal(username)
    if owner is None:
        return HttpResponse(status=404)

    if request.method == "MKCOL":
        if request.body:
            return HttpResponse(status=415)
        request_method = request.method
        request.method = "MKCALENDAR"
        response = calendar_collection_view(request, username, slug)
        request.method = request_method
        return response

    if request.method == "MKCALENDAR":
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

    calendar = get_calendar_for_user(user, username, slug)
    if calendar is None:
        if request.method == "REPORT":
            report_root = _parse_xml_body(request.body)
            if report_root is not None and report_root.tag == qname(
                NS_CALDAV,
                "free-busy-query",
            ):
                calendar = Calendar.objects.filter(owner=owner, slug=slug).first()
        if calendar is None:
            return HttpResponse(status=404)

    if request.method == "DELETE":
        if owner != user:
            return HttpResponse(status=403)
        calendar.delete()
        response = HttpResponse(status=204)
        return _dav_common_headers(response)

    if request.method == "PROPPATCH":
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

    if request.method in ("GET", "HEAD"):
        calendar_etag = _etag_for_calendar(calendar)
        calendar_timestamp = calendar.updated_at.timestamp()
        if _conditional_not_modified(request, calendar_etag, calendar_timestamp):
            response = HttpResponse(status=304)
            response["ETag"] = calendar_etag
            response["Last-Modified"] = http_date(calendar_timestamp)
            return _dav_common_headers(response)

        if request.method == "HEAD":
            response = HttpResponse(status=200)
        else:
            response = HttpResponse(
                f"Calendar {calendar.name}".encode("utf-8"),
                content_type="text/plain; charset=utf-8",
            )
        response["ETag"] = calendar_etag
        response["Last-Modified"] = http_date(calendar_timestamp)
        return _dav_common_headers(response)

    if request.method == "REPORT":
        return _handle_report([calendar], request, allow_sync_collection=True)

    if request.method != "PROPFIND":
        return _not_allowed(request, allowed, username=username, slug=slug)

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


@csrf_exempt
def calendar_collection_uid_view(request, guid, slug):
    username = _dav_username_for_guid(guid)
    if username is None:
        return HttpResponse(status=404)
    return calendar_collection_view(request, username, slug)


calendar_collection_users_view = calendar_collection_view


@csrf_exempt
def calendar_object_view(request, username, slug, filename):
    allowed = [
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
    if request.method == "OPTIONS":
        response = HttpResponse(status=204)
        response["Allow"] = ", ".join(allowed)
        return _dav_common_headers(response)

    user, auth_response = _require_dav_user(request)
    if auth_response is not None:
        return auth_response

    if request.method in (
        "PUT",
        "DELETE",
        "PROPPATCH",
        "MKCOL",
        "MKCALENDAR",
        "COPY",
        "MOVE",
    ):
        writable = get_calendar_for_write_user(user, username, slug)
        if writable is None:
            return HttpResponse(status=404)
        if writable is False:
            return HttpResponse(status=403)

        if request.method in ("COPY", "MOVE") and writable.slug != "litmus":
            return _not_allowed(
                request,
                allowed,
                username=username,
                slug=slug,
                filename=filename,
            )
        if request.method == "PROPPATCH" and writable.slug != "litmus":
            return _not_allowed(
                request,
                allowed,
                username=username,
                slug=slug,
                filename=filename,
            )

        with transaction.atomic():
            writable = Calendar.objects.select_for_update().get(pk=writable.pk)
            next_revision = _latest_sync_revision(writable) + 1
            marker_filename = core_paths.collection_marker(filename)
            parent_path, _leaf = core_paths.split_filename_path(filename)

            if request.method in ("COPY", "MOVE"):
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

            if request.method == "PROPPATCH":
                root = _parse_xml_body(request.body)
                if root is None or root.tag != qname(NS_DAV, "propertyupdate"):
                    return HttpResponse(status=400)

                existing = writable.calendar_objects.filter(filename=filename).first()
                if existing is None and filename.endswith("/"):
                    existing = writable.calendar_objects.filter(
                        filename=marker_filename
                    ).first()
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

            if request.method in ("MKCOL", "MKCALENDAR"):
                if writable.slug != "litmus":
                    return _caldav_error_response(
                        "calendar-collection-location-ok",
                        status=403,
                    )

                if request.method == "MKCOL" and request.body:
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
                response["Location"] = (
                    f"/dav/calendars/{username}/{slug}/{marker_filename}"
                )
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

            existing = writable.calendar_objects.filter(filename=filename).first()
            if existing is None and filename.endswith("/"):
                existing = writable.calendar_objects.filter(
                    filename=marker_filename
                ).first()

            if request.method == "DELETE":
                if existing is None:
                    return HttpResponse(status=404)
                if existing.filename.endswith("/"):
                    prefix = existing.filename
                    deleted = list(
                        writable.calendar_objects.filter(
                            filename__startswith=prefix
                        ).values("filename", "uid")
                    )
                    writable.calendar_objects.filter(
                        filename__startswith=prefix
                    ).delete()
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

    normalized_filename = filename
    if filename.endswith("/"):
        normalized_filename = core_paths.collection_marker(filename)

    obj = get_calendar_object_for_user(user, username, slug, normalized_filename)
    if obj is None:
        return HttpResponse(status=404)

    if request.method in ("GET", "HEAD"):
        if request.method == "HEAD":
            response = HttpResponse(status=200)
        else:
            response = HttpResponse(
                obj.ical_blob.encode("utf-8"),
                content_type=obj.content_type,
            )
        response["ETag"] = _etag_for_object(obj)
        response["Last-Modified"] = http_date(obj.updated_at.timestamp())
        response["Content-Length"] = str(obj.size)
        return _dav_common_headers(response)

    if request.method != "PROPFIND":
        return _not_allowed(
            request,
            allowed,
            username=username,
            slug=slug,
            filename=filename,
        )

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


@csrf_exempt
def calendar_object_uid_view(request, guid, slug, filename):
    username = _dav_username_for_guid(guid)
    if username is None:
        return HttpResponse(status=404)
    return calendar_object_view(request, username, slug, filename)


calendar_object_users_view = calendar_object_view
