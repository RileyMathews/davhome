import hashlib
import logging
from xml.etree import ElementTree as ET

from django.http import HttpResponse
from django.db.models import Max
from django.utils.http import http_date

from calendars.models import Calendar
from calendars.models import CalendarObjectChange
from calendars.permissions import can_view_calendar

from .core import davxml as core_davxml
from .core import paths as core_paths
from .core import payloads as core_payloads
from .xml import NS_CALDAV, NS_DAV, multistatus_document, parse_propfind_request, qname
from dav.views.helpers.sync_tokens import (
    _build_sync_token,
    _parse_sync_token_for_calendar as _parse_sync_token_for_calendar_impl,
    _sync_token_revision_from_parts,
)


logger = logging.getLogger("dav.audit")


def _etag_for_calendar(calendar):
    return f'"{int(calendar.updated_at.timestamp())}"'


def _etag_for_object(obj):
    return obj.etag


def _generate_strong_etag(payload):
    digest = hashlib.sha256(payload).hexdigest()
    return f'"{digest}"'


def _collection_exists(calendar, path):
    marker = core_paths.collection_marker(path)
    if not marker:
        return True
    return calendar.calendar_objects.filter(filename=marker).exists()


def _xml_response(status, body, headers=None):
    response = HttpResponse(
        body,
        status=status,
        content_type="application/xml; charset=utf-8",
    )
    for key, value in (headers or {}).items():
        response[key] = value
    return _dav_common_headers(response)


def _caldav_error_response(error_name, status=403):
    return core_davxml.caldav_error_response(
        _xml_response,
        qname,
        NS_DAV,
        NS_CALDAV,
        error_name,
        status=status,
    )


def _dav_error_response(error_name, status=403):
    return core_davxml.dav_error_response(
        _xml_response,
        qname,
        NS_DAV,
        error_name,
        status=status,
    )


def _valid_sync_token_error_response():
    return core_davxml.valid_sync_token_error_response(_xml_response, qname, NS_DAV)


def _latest_sync_revision(calendar):
    latest = CalendarObjectChange.objects.filter(calendar=calendar).aggregate(
        max_revision=Max("revision")
    )
    return int(latest["max_revision"] or 0)


def _sync_token_for_calendar(calendar):
    return _build_sync_token(calendar.id, _latest_sync_revision(calendar))


def _parse_sync_token_for_calendar(token, calendar):
    return _parse_sync_token_for_calendar_impl(
        token,
        calendar,
        _valid_sync_token_error_response,
    )


def _create_calendar_change(calendar, revision, filename, uid, is_deleted):
    return CalendarObjectChange.objects.create(
        calendar=calendar,
        revision=revision,
        filename=filename,
        uid=uid,
        is_deleted=is_deleted,
    )


def _proppatch_multistatus_response(path, ok_props, bad_props):
    response = ET.Element(qname(NS_DAV, "response"))
    href = ET.SubElement(response, qname(NS_DAV, "href"))
    href.text = path

    if ok_props:
        ok_stat = ET.SubElement(response, qname(NS_DAV, "propstat"))
        ok_prop = ET.SubElement(ok_stat, qname(NS_DAV, "prop"))
        for tag in ok_props:
            ET.SubElement(ok_prop, tag)
        status = ET.SubElement(ok_stat, qname(NS_DAV, "status"))
        status.text = "HTTP/1.1 200 OK"

    if bad_props:
        bad_stat = ET.SubElement(response, qname(NS_DAV, "propstat"))
        bad_prop = ET.SubElement(bad_stat, qname(NS_DAV, "prop"))
        for tag in bad_props:
            ET.SubElement(bad_prop, tag)
        status = ET.SubElement(bad_stat, qname(NS_DAV, "status"))
        status.text = "HTTP/1.1 403 Forbidden"

    return _xml_response(207, multistatus_document([response]))


def _dav_common_headers(response):
    response["DAV"] = "1, calendar-access, calendar-query-extended"
    return response


def _remote_ip(forwarded_header, remote_addr):
    forwarded = (forwarded_header or "").strip()
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return (remote_addr or "").strip()


def _client_ip(request):
    return _remote_ip(
        request.headers.get("X-Forwarded-For"),
        request.META.get("REMOTE_ADDR"),
    )


def _log_dav_create(
    event,
    request,
    actor_username,
    owner_username,
    slug,
    status,
    **extra,
):
    logger.info(
        "%s actor=%s owner=%s slug=%s method=%s path=%s status=%s user_agent=%r content_type=%r depth=%r if_none_match=%r if_match=%r remote_ip=%r extra=%r",
        event,
        actor_username,
        owner_username,
        slug,
        request.method,
        request.path,
        status,
        request.headers.get("User-Agent"),
        request.META.get("CONTENT_TYPE") or request.content_type,
        request.headers.get("Depth"),
        request.headers.get("If-None-Match"),
        request.headers.get("If-Match"),
        _client_ip(request),
        extra,
    )


def _conditional_not_modified(request, etag, timestamp):
    if core_davxml.if_none_match_matches(
        request.headers.get("If-None-Match"),
        core_payloads.if_match_values,
        etag,
    ):
        return True
    if core_davxml.if_modified_since_not_modified(
        request.headers.get("If-Modified-Since"),
        timestamp,
    ):
        return True
    return False


def _parse_propfind_payload(request):
    parsed = parse_propfind_request(request.body)
    if "error" in parsed:
        return None, HttpResponse(status=400)

    depth = request.headers.get("Depth", "infinity")
    if depth == "infinity":
        return None, core_davxml.propfind_finite_depth_error(
            _xml_response,
            qname,
            NS_DAV,
        )
    if depth not in ("0", "1"):
        return None, HttpResponse(status=400)

    return parsed, None


def _visible_calendars_for_home(owner, user):
    calendars = Calendar.objects.filter(owner=owner)
    return [calendar for calendar in calendars if can_view_calendar(calendar, user)]


def _home_etag_and_timestamp(owner, user):
    calendars = _visible_calendars_for_home(owner, user)
    if not calendars:
        ts = owner.date_joined.timestamp()
        return '"home-empty"', ts

    parts = [
        f"{calendar.slug}:{int(calendar.updated_at.timestamp())}"
        for calendar in calendars
    ]
    parts.sort()
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    ts = max(calendar.updated_at.timestamp() for calendar in calendars)
    return f'"{digest}"', ts
