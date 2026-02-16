# pyright: reportGeneralTypeIssues=false, reportAttributeAccessIssue=false

import hashlib
import re
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
    if request.method == "OPTIONS":
        response = HttpResponse(status=204)
        response["Allow"] = "OPTIONS, PROPFIND, GET, HEAD"
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

    if request.method != "PROPFIND":
        return _not_allowed(["OPTIONS", "PROPFIND", "GET", "HEAD"])

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
        calendars = Calendar.objects.filter(owner=owner)  # type: ignore[attr-defined]
        for calendar in calendars:
            if not can_view_calendar(calendar, user):
                continue
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


@csrf_exempt
def calendar_collection_view(request, username, slug):
    allowed = ["OPTIONS", "PROPFIND", "GET", "HEAD", "MKCOL", "MKCALENDAR"]
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
    }


@csrf_exempt
def calendar_object_view(request, username, slug, filename):
    allowed = ["OPTIONS", "PROPFIND", "GET", "HEAD", "PUT", "DELETE"]
    if request.method == "OPTIONS":
        response = HttpResponse(status=204)
        response["Allow"] = ", ".join(allowed)
        return _dav_common_headers(response)

    user, auth_response = _require_dav_user(request)
    if auth_response is not None:
        return auth_response

    if request.method in ("PUT", "DELETE"):
        writable = get_calendar_for_write_user(user, username, slug)
        if writable is None:
            return HttpResponse(status=404)
        if writable is False:
            return HttpResponse(status=403)

        existing = writable.calendar_objects.filter(filename=filename).first()

        if request.method == "DELETE":
            if existing is None:
                return HttpResponse(status=404)
            existing.delete()
            writable.save(update_fields=["updated_at"])
            response = HttpResponse(status=204)
            return _dav_common_headers(response)

        if _precondition_failed_for_write(request, existing):
            return HttpResponse(status=412)

        parsed, error = _validate_ical_payload(request.body)
        if error is not None:
            return HttpResponse(
                error, status=400, content_type="text/plain; charset=utf-8"
            )
        if parsed is None:
            return HttpResponse(status=400)

        now = timezone.now()
        payload = request.body
        etag = _generate_strong_etag(payload)
        status_code = 204
        if existing is None:
            existing = writable.calendar_objects.create(
                uid=parsed["uid"],
                filename=filename,
                etag=etag,
                ical_blob=parsed["text"],
                content_type="text/calendar; charset=utf-8",
                size=len(payload),
            )
            status_code = 201
        else:
            existing.uid = parsed["uid"]
            existing.etag = etag
            existing.ical_blob = parsed["text"]
            existing.content_type = "text/calendar; charset=utf-8"
            existing.size = len(payload)
            existing.updated_at = now
            existing.save()

        writable.updated_at = now
        writable.save(update_fields=["updated_at"])

        response = HttpResponse(status=status_code)
        response["ETag"] = existing.etag
        response["Last-Modified"] = http_date(existing.updated_at.timestamp())
        response["Content-Length"] = str(existing.size)
        return _dav_common_headers(response)

    obj = get_calendar_object_for_user(user, username, slug, filename)
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
