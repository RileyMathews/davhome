# pyright: reportGeneralTypeIssues=false, reportAttributeAccessIssue=false

import hashlib
import re
from datetime import datetime, timezone as datetime_timezone
from urllib.parse import quote, urlparse
from xml.etree import ElementTree as ET

from django.http import HttpResponse, HttpResponseNotAllowed
from django.shortcuts import redirect
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.utils.http import http_date

from calendars.models import Calendar
from calendars.permissions import can_view_calendar

from .auth import get_dav_user, unauthorized_response
from .resolver import (
    get_calendar_for_user,
    get_calendar_for_write_user,
    get_calendar_object_for_user,
    get_principal,
)
from .xml import (
    NS_CALDAV,
    NS_CS,
    NS_DAV,
    multistatus_document,
    parse_requested_properties,
    qname,
    response_with_props,
)


@csrf_exempt
def well_known_caldav(request):
    return redirect("/dav/", permanent=False)


def _etag_for_calendar(calendar):
    return f'"{int(calendar.updated_at.timestamp())}"'


def _etag_for_object(obj):
    return obj.etag


def _generate_strong_etag(payload):
    digest = hashlib.sha256(payload).hexdigest()
    return f'"{digest}"'


def _extract_uid(ical_text):
    match = re.search(r"^UID:(.+)$", ical_text, flags=re.MULTILINE)
    if match is None:
        return None
    return match.group(1).strip()


def _validate_ical_payload(payload):
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return None, "Calendar payload must be UTF-8 text."

    if "BEGIN:VCALENDAR" not in text or "END:VCALENDAR" not in text:
        return None, "Calendar payload must contain VCALENDAR boundaries."

    uid = _extract_uid(text)
    if uid is None:
        return None, "Calendar payload must contain a UID property."

    return {"text": text, "uid": uid}, None


def _validate_generic_payload(payload):
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return None, "Generic DAV payload must be UTF-8 text."

    return {"text": text, "uid": None}, None


def _if_match_values(header):
    return [value.strip() for value in header.split(",") if value.strip()]


def _precondition_failed_for_write(request, existing_obj):
    if_none_match = request.headers.get("If-None-Match")
    if if_none_match == "*" and existing_obj is not None:
        return True

    if_match = request.headers.get("If-Match")
    if if_match:
        if existing_obj is None:
            return True
        allowed = _if_match_values(if_match)
        if "*" not in allowed and existing_obj.etag not in allowed:
            return True

    return False


def _collection_marker(path):
    trimmed = path.strip("/")
    if not trimmed:
        return ""
    return f"{trimmed}/"


def _split_filename_path(filename):
    clean = filename.strip("/")
    if not clean:
        return "", ""
    parts = [part for part in clean.split("/") if part]
    parent = "/".join(parts[:-1])
    leaf = parts[-1]
    return parent, leaf


def _collection_exists(calendar, path):
    marker = _collection_marker(path)
    if not marker:
        return True
    return calendar.calendar_objects.filter(filename=marker).exists()


def _is_ical_resource(filename, content_type):
    if filename.lower().endswith(".ics"):
        return True
    if content_type and "text/calendar" in content_type.lower():
        return True
    return False


def _parse_xml_body(payload):
    try:
        return ET.fromstring(payload)
    except ET.ParseError:
        return None


def _requested_props_from_report(root):
    prop = root.find(qname(NS_DAV, "prop"))
    if prop is None:
        return None
    return [child.tag for child in list(prop)]


def _normalize_href_path(href):
    parsed = urlparse(href)
    path = parsed.path if parsed.scheme else href
    if not path.startswith("/"):
        path = f"/{path}"
    return path


def _parse_ical_datetime(value):
    if not value:
        return None
    raw = value.strip()
    try:
        if re.fullmatch(r"\d{8}", raw):
            return datetime.strptime(raw, "%Y%m%d").replace(
                tzinfo=datetime_timezone.utc
            )
        if raw.endswith("Z") and re.fullmatch(r"\d{8}T\d{6}Z", raw):
            return datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(
                tzinfo=datetime_timezone.utc
            )
        if re.fullmatch(r"\d{8}T\d{6}", raw):
            return datetime.strptime(raw, "%Y%m%dT%H%M%S").replace(
                tzinfo=datetime_timezone.utc
            )
    except ValueError:
        return None
    return None


def _first_ical_line_value(ical_text, key):
    pattern = rf"^{key}(?:;[^:]*)?:(.+)$"
    match = re.search(pattern, ical_text, flags=re.MULTILINE)
    if match is None:
        return None
    return match.group(1).strip()


def _parse_calendar_query_filter(root):
    filter_elem = root.find(qname(NS_CALDAV, "filter"))
    if filter_elem is None:
        return {"component": None, "start": None, "end": None}

    component = None
    start = None
    end = None

    for comp in filter_elem.findall(f".//{qname(NS_CALDAV, 'comp-filter')}"):
        name = (comp.get("name") or "").upper()
        if name and name != "VCALENDAR":
            component = name
        time_range = comp.find(qname(NS_CALDAV, "time-range"))
        if time_range is not None:
            start = _parse_ical_datetime(time_range.get("start"))
            end = _parse_ical_datetime(time_range.get("end"))

    return {"component": component, "start": start, "end": end}


def _object_matches_query(obj, query_filter):
    component = query_filter["component"]
    if component == "VEVENT" and "BEGIN:VEVENT" not in obj.ical_blob:
        return False
    if component == "VTODO" and "BEGIN:VTODO" not in obj.ical_blob:
        return False

    if query_filter["start"] is None and query_filter["end"] is None:
        return True

    event_start = _parse_ical_datetime(_first_ical_line_value(obj.ical_blob, "DTSTART"))
    event_end = _parse_ical_datetime(_first_ical_line_value(obj.ical_blob, "DTEND"))

    if event_start is None:
        return True
    if event_end is None:
        event_end = event_start

    if query_filter["start"] is not None and event_end < query_filter["start"]:
        return False
    if query_filter["end"] is not None and event_start > query_filter["end"]:
        return False

    return True


def _calendar_data_prop(ical_blob):
    elem = ET.Element(qname(NS_CALDAV, "calendar-data"))
    elem.text = ical_blob
    return elem


def _report_unknown_type():
    return HttpResponse(status=501)


def _dav_common_headers(response):
    response["DAV"] = "1, calendar-access"
    return response


def _not_allowed(allowed):
    response = HttpResponseNotAllowed(allowed)
    return _dav_common_headers(response)


def _xml_response(status, body):
    response = HttpResponse(
        body, status=status, content_type="application/xml; charset=utf-8"
    )
    return _dav_common_headers(response)


def _build_prop_map_for_root(user):
    def current_user_principal():
        elem = ET.Element(qname(NS_DAV, "current-user-principal"))
        href = ET.SubElement(elem, qname(NS_DAV, "href"))
        href.text = f"/dav/principals/{user.username}/"
        return elem

    return {
        qname(NS_DAV, "resourcetype"): lambda: ET.Element(
            qname(NS_DAV, "resourcetype")
        ),
        qname(NS_DAV, "displayname"): lambda: _text_prop(
            NS_DAV, "displayname", "davhome"
        ),
        qname(NS_DAV, "current-user-principal"): current_user_principal,
    }


def _text_prop(namespace, name, value):
    elem = ET.Element(qname(namespace, name))
    elem.text = value
    return elem


def _resourcetype_prop(*types):
    elem = ET.Element(qname(NS_DAV, "resourcetype"))
    for resource_type in types:
        ET.SubElement(elem, qname(*resource_type))
    return elem


def _supported_components_prop():
    elem = ET.Element(qname(NS_CALDAV, "supported-calendar-component-set"))
    ET.SubElement(elem, qname(NS_CALDAV, "comp"), name="VEVENT")
    return elem


def _select_props(prop_map, requested_tags):
    if requested_tags is None:
        return [builder() for builder in prop_map.values()], []

    ok = []
    missing = []
    for tag in requested_tags:
        builder = prop_map.get(tag)
        if builder is None:
            missing.append(ET.Element(tag))
        else:
            ok.append(builder())
    return ok, missing


def _require_dav_user(request):
    user = get_dav_user(request)
    if user is None:
        return None, unauthorized_response()
    return user, None


@csrf_exempt
def dav_root(request):
    if request.method == "OPTIONS":
        response = HttpResponse(status=204)
        response["Allow"] = "OPTIONS, PROPFIND, GET, HEAD"
        return _dav_common_headers(response)

    user, auth_response = _require_dav_user(request)
    if auth_response is not None:
        return auth_response

    if request.method in ("GET", "HEAD"):
        if request.method == "HEAD":
            response = HttpResponse(status=200)
        else:
            response = HttpResponse(
                "DAV root", content_type="text/plain; charset=utf-8"
            )
        return _dav_common_headers(response)

    if request.method != "PROPFIND":
        return _not_allowed(["OPTIONS", "PROPFIND", "GET", "HEAD"])

    depth = request.headers.get("Depth", "0")
    if depth not in ("0", "1"):
        return HttpResponse(status=400)

    requested = parse_requested_properties(request.body)
    prop_map = _build_prop_map_for_root(user)
    root_ok, root_missing = _select_props(prop_map, requested)
    responses = [response_with_props("/dav/", root_ok, root_missing)]

    if depth == "1":
        principal_href = f"/dav/principals/{user.username}/"
        home_href = f"/dav/calendars/{user.username}/"

        principal_map = _build_prop_map_for_principal(user, user)
        principal_ok, principal_missing = _select_props(principal_map, requested)
        responses.append(
            response_with_props(principal_href, principal_ok, principal_missing)
        )

        home_map = _build_prop_map_for_calendar_home(user)
        home_ok, home_missing = _select_props(home_map, requested)
        responses.append(response_with_props(home_href, home_ok, home_missing))

    return _xml_response(207, multistatus_document(responses))


def _build_prop_map_for_principal(auth_user, principal_user):
    def current_user_principal():
        elem = ET.Element(qname(NS_DAV, "current-user-principal"))
        href = ET.SubElement(elem, qname(NS_DAV, "href"))
        href.text = f"/dav/principals/{auth_user.username}/"
        return elem

    def calendar_home_set():
        elem = ET.Element(qname(NS_CALDAV, "calendar-home-set"))
        href = ET.SubElement(elem, qname(NS_DAV, "href"))
        href.text = f"/dav/calendars/{principal_user.username}/"
        return elem

    return {
        qname(NS_DAV, "resourcetype"): lambda: _resourcetype_prop(
            (NS_DAV, "principal")
        ),
        qname(NS_DAV, "displayname"): lambda: _text_prop(
            NS_DAV,
            "displayname",
            principal_user.username,
        ),
        qname(NS_DAV, "current-user-principal"): current_user_principal,
        qname(NS_CALDAV, "calendar-home-set"): calendar_home_set,
    }


@csrf_exempt
def principal_view(request, username):
    if request.method == "OPTIONS":
        response = HttpResponse(status=204)
        response["Allow"] = "OPTIONS, PROPFIND, GET, HEAD"
        return _dav_common_headers(response)

    user, auth_response = _require_dav_user(request)
    if auth_response is not None:
        return auth_response

    principal = get_principal(username)
    if principal is None:
        return HttpResponse(status=404)

    if principal != user:
        return HttpResponse(status=403)

    if request.method in ("GET", "HEAD"):
        if request.method == "HEAD":
            response = HttpResponse(status=200)
        else:
            response = HttpResponse(
                "Principal", content_type="text/plain; charset=utf-8"
            )
        return _dav_common_headers(response)

    if request.method != "PROPFIND":
        return _not_allowed(["OPTIONS", "PROPFIND", "GET", "HEAD"])

    depth = request.headers.get("Depth", "0")
    if depth not in ("0", "1"):
        return HttpResponse(status=400)

    requested = parse_requested_properties(request.body)
    principal_map = _build_prop_map_for_principal(user, principal)
    ok, missing = _select_props(principal_map, requested)
    responses = [
        response_with_props(f"/dav/principals/{principal.username}/", ok, missing)
    ]

    return _xml_response(207, multistatus_document(responses))


def _build_prop_map_for_calendar_home(owner):
    return {
        qname(NS_DAV, "resourcetype"): lambda: _resourcetype_prop(
            (NS_DAV, "collection")
        ),
        qname(NS_DAV, "displayname"): lambda: _text_prop(
            NS_DAV,
            "displayname",
            f"{owner.username} calendars",
        ),
    }


@csrf_exempt
def calendar_home_view(request, username):
    allowed = ["OPTIONS", "PROPFIND", "GET", "HEAD", "REPORT"]
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

    if request.method in ("GET", "HEAD"):
        if request.method == "HEAD":
            response = HttpResponse(status=200)
        else:
            response = HttpResponse(
                "Calendar home", content_type="text/plain; charset=utf-8"
            )
        return _dav_common_headers(response)

    calendars = _visible_calendars_for_home(owner, user)

    if request.method == "REPORT":
        return _handle_report(calendars, request)

    if request.method != "PROPFIND":
        return _not_allowed(allowed)

    depth = request.headers.get("Depth", "0")
    if depth not in ("0", "1"):
        return HttpResponse(status=400)

    requested = parse_requested_properties(request.body)
    home_map = _build_prop_map_for_calendar_home(owner)
    home_ok, home_missing = _select_props(home_map, requested)
    responses = [
        response_with_props(f"/dav/calendars/{owner.username}/", home_ok, home_missing)
    ]

    if depth == "1":
        for calendar in calendars:
            cal_map = _build_prop_map_for_calendar_collection(calendar)
            cal_ok, cal_missing = _select_props(cal_map, requested)
            href = f"/dav/calendars/{owner.username}/{calendar.slug}/"
            responses.append(response_with_props(href, cal_ok, cal_missing))

    return _xml_response(207, multistatus_document(responses))


def _build_prop_map_for_calendar_collection(calendar):
    return {
        qname(NS_DAV, "resourcetype"): lambda: _resourcetype_prop(
            (NS_DAV, "collection"),
            (NS_CALDAV, "calendar"),
        ),
        qname(NS_DAV, "displayname"): lambda: _text_prop(
            NS_DAV, "displayname", calendar.name
        ),
        qname(NS_CS, "getctag"): lambda: _text_prop(
            NS_CS,
            "getctag",
            str(int(calendar.updated_at.timestamp())),
        ),
        qname(
            NS_CALDAV, "supported-calendar-component-set"
        ): _supported_components_prop,
        qname(NS_DAV, "getetag"): lambda: _text_prop(
            NS_DAV, "getetag", _etag_for_calendar(calendar)
        ),
    }


def _visible_calendars_for_home(owner, user):
    calendars = Calendar.objects.filter(owner=owner)  # type: ignore[attr-defined]
    return [calendar for calendar in calendars if can_view_calendar(calendar, user)]


def _responses_for_multiget(calendars, requested, hrefs):
    responses = []
    by_path = {}
    for calendar in calendars:
        for obj in calendar.calendar_objects.all():
            href = f"/dav/calendars/{calendar.owner.username}/{calendar.slug}/{obj.filename}"
            by_path[href] = obj

    for href in hrefs:
        normalized = _normalize_href_path(href)
        obj = by_path.get(normalized)
        if obj is None:
            responses.append(
                response_with_props(
                    normalized, [], [ET.Element(qname(NS_DAV, "getetag"))]
                )
            )
            continue

        obj_map = _build_prop_map_for_object(obj)
        ok, missing = _select_props(obj_map, requested)
        responses.append(response_with_props(normalized, ok, missing))

    return responses


def _responses_for_calendar_query(calendars, requested, query_filter):
    responses = []
    for calendar in calendars:
        for obj in calendar.calendar_objects.all():
            if not _object_matches_query(obj, query_filter):
                continue
            obj_map = _build_prop_map_for_object(obj)
            ok, missing = _select_props(obj_map, requested)
            href = f"/dav/calendars/{calendar.owner.username}/{calendar.slug}/{obj.filename}"
            responses.append(response_with_props(href, ok, missing))
    return responses


def _handle_report(calendars, request):
    root = _parse_xml_body(request.body)
    if root is None:
        return HttpResponse(status=400)

    requested = _requested_props_from_report(root)

    if root.tag == qname(NS_CALDAV, "calendar-multiget"):
        hrefs = [elem.text or "" for elem in root.findall(qname(NS_DAV, "href"))]
        responses = _responses_for_multiget(calendars, requested, hrefs)
        return _xml_response(207, multistatus_document(responses))

    if root.tag == qname(NS_CALDAV, "calendar-query"):
        query_filter = _parse_calendar_query_filter(root)
        responses = _responses_for_calendar_query(calendars, requested, query_filter)
        return _xml_response(207, multistatus_document(responses))

    return _report_unknown_type()


@csrf_exempt
def calendar_collection_view(request, username, slug):
    allowed = [
        "OPTIONS",
        "PROPFIND",
        "GET",
        "HEAD",
        "REPORT",
        "MKCOL",
        "MKCALENDAR",
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

    if request.method in ("MKCOL", "MKCALENDAR"):
        if user != owner:
            return HttpResponse(status=403)

        if request.body:
            return HttpResponse(status=415)

        existing = Calendar.objects.filter(owner=owner, slug=slug).first()  # type: ignore[attr-defined]
        if existing is not None:
            return HttpResponse(status=405)

        now = timezone.now()
        calendar = Calendar.objects.create(  # type: ignore[attr-defined]
            owner=owner,
            slug=slug,
            name=slug,
            timezone="UTC",
            updated_at=now,
        )
        response = HttpResponse(status=201)
        response["Location"] = f"/dav/calendars/{username}/{calendar.slug}/"
        response["ETag"] = _etag_for_calendar(calendar)
        return _dav_common_headers(response)

    calendar = get_calendar_for_user(user, username, slug)
    if calendar is None:
        return HttpResponse(status=404)

    if request.method in ("GET", "HEAD"):
        if request.method == "HEAD":
            response = HttpResponse(status=200)
        else:
            response = HttpResponse(
                f"Calendar {calendar.name}",
                content_type="text/plain; charset=utf-8",
            )
        response["ETag"] = _etag_for_calendar(calendar)
        return _dav_common_headers(response)

    if request.method == "REPORT":
        return _handle_report([calendar], request)

    if request.method != "PROPFIND":
        return _not_allowed(allowed)

    depth = request.headers.get("Depth", "0")
    if depth not in ("0", "1"):
        return HttpResponse(status=400)

    requested = parse_requested_properties(request.body)
    cal_map = _build_prop_map_for_calendar_collection(calendar)
    cal_ok, cal_missing = _select_props(cal_map, requested)
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
            obj_ok, obj_missing = _select_props(obj_map, requested)
            href = f"/dav/calendars/{username}/{calendar.slug}/{obj.filename}"
            responses.append(response_with_props(href, obj_ok, obj_missing))

    return _xml_response(207, multistatus_document(responses))


def _build_prop_map_for_object(obj):
    return {
        qname(NS_DAV, "resourcetype"): lambda: ET.Element(
            qname(NS_DAV, "resourcetype")
        ),
        qname(NS_DAV, "getetag"): lambda: _text_prop(
            NS_DAV, "getetag", _etag_for_object(obj)
        ),
        qname(NS_DAV, "getcontenttype"): lambda: _text_prop(
            NS_DAV,
            "getcontenttype",
            obj.content_type,
        ),
        qname(NS_DAV, "getcontentlength"): lambda: _text_prop(
            NS_DAV,
            "getcontentlength",
            str(obj.size),
        ),
        qname(NS_DAV, "getlastmodified"): lambda: _text_prop(
            NS_DAV,
            "getlastmodified",
            http_date(obj.updated_at.timestamp()),
        ),
        qname(NS_CALDAV, "calendar-data"): lambda: _calendar_data_prop(obj.ical_blob),
    }


@csrf_exempt
def calendar_object_view(request, username, slug, filename):
    allowed = [
        "OPTIONS",
        "PROPFIND",
        "GET",
        "HEAD",
        "PUT",
        "DELETE",
        "MKCOL",
        "MKCALENDAR",
    ]
    if request.method == "OPTIONS":
        response = HttpResponse(status=204)
        response["Allow"] = ", ".join(allowed)
        return _dav_common_headers(response)

    user, auth_response = _require_dav_user(request)
    if auth_response is not None:
        return auth_response

    if request.method in ("PUT", "DELETE", "MKCOL", "MKCALENDAR"):
        writable = get_calendar_for_write_user(user, username, slug)
        if writable is None:
            return HttpResponse(status=404)
        if writable is False:
            return HttpResponse(status=403)

        marker_filename = _collection_marker(filename)
        parent_path, _leaf = _split_filename_path(filename)

        if request.method in ("MKCOL", "MKCALENDAR"):
            if request.body:
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
            writable.save(update_fields=["updated_at"])
            response = HttpResponse(status=201)
            response["Location"] = f"/dav/calendars/{username}/{slug}/{marker_filename}"
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
                writable.calendar_objects.filter(filename__startswith=prefix).delete()
            else:
                existing.delete()
            writable.save(update_fields=["updated_at"])
            response = HttpResponse(status=204)
            return _dav_common_headers(response)

        if _precondition_failed_for_write(request, existing):
            return HttpResponse(status=412)

        if not _collection_exists(writable, parent_path):
            return HttpResponse(status=409)

        content_type = request.content_type or "application/octet-stream"
        if _is_ical_resource(filename, content_type):
            parsed, error = _validate_ical_payload(request.body)
        else:
            parsed, error = _validate_generic_payload(request.body)

        if error is not None:
            return HttpResponse(
                error, status=400, content_type="text/plain; charset=utf-8"
            )
        if parsed is None:
            return HttpResponse(status=400)

        now = timezone.now()
        payload = request.body
        etag = _generate_strong_etag(payload)
        object_uid = parsed["uid"] or f"dav:{filename}"
        status_code = 204
        if existing is None:
            existing = writable.calendar_objects.create(
                uid=object_uid,
                filename=filename,
                etag=etag,
                ical_blob=parsed["text"],
                content_type=content_type,
                size=len(payload),
            )
            status_code = 201
        else:
            existing.uid = object_uid
            existing.etag = etag
            existing.ical_blob = parsed["text"]
            existing.content_type = content_type
            existing.size = len(payload)
            existing.updated_at = now
            existing.save()

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
        return _dav_common_headers(response)

    normalized_filename = filename
    if filename.endswith("/"):
        normalized_filename = _collection_marker(filename)

    obj = get_calendar_object_for_user(user, username, slug, normalized_filename)
    if obj is None:
        return HttpResponse(status=404)

    if request.method in ("GET", "HEAD"):
        if request.method == "HEAD":
            response = HttpResponse(status=200)
        else:
            response = HttpResponse(obj.ical_blob, content_type=obj.content_type)
        response["ETag"] = _etag_for_object(obj)
        response["Last-Modified"] = http_date(obj.updated_at.timestamp())
        response["Content-Length"] = str(obj.size)
        return _dav_common_headers(response)

    if request.method != "PROPFIND":
        return _not_allowed(allowed)

    requested = parse_requested_properties(request.body)
    obj_map = _build_prop_map_for_object(obj)
    ok, missing = _select_props(obj_map, requested)
    href = f"/dav/calendars/{username}/{slug}/{filename}"
    return _xml_response(
        207, multistatus_document([response_with_props(href, ok, missing)])
    )
