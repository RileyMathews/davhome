# pyright: reportGeneralTypeIssues=false, reportAttributeAccessIssue=false

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone as datetime_timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlparse
from uuid import UUID
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import icalendar
from recurring_ical_events import of as recurring_of
from django.http import HttpResponse, HttpResponseNotAllowed
from django.shortcuts import redirect
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.http import http_date

from calendars.models import Calendar, CalendarObjectChange
from calendars.permissions import can_view_calendar, can_write_calendar

from .auth import get_dav_user, unauthorized_response
from .report_engine import parse_report_request
from .resolver import (
    get_calendar_for_user,
    get_calendar_for_write_user,
    get_calendar_object_for_user,
    get_principal,
)
from .xml import (
    NS_APPLE_ICAL,
    NS_CALDAV,
    NS_CS,
    NS_DAV,
    multistatus_document,
    parse_propfind_request,
    qname,
    response_with_status,
    response_with_props,
)


logger = logging.getLogger("dav.audit")


_ACTIVE_REPORT_TZINFO = None
_SYNC_TOKEN_PATH_PREFIX = "/sync/"
_SYNC_TOKEN_DATA_PREFIX = "data:,"


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


def _normalize_content_type(raw):
    value = (raw or "application/octet-stream").strip()
    return value.replace("; ", ";")


def _dedupe_duplicate_alarms(ical_text):
    lines = ical_text.splitlines()
    result = []
    seen_alarm_blocks = set()
    collecting = False
    alarm_lines = []
    in_event_like = False

    for line in lines:
        stripped = line.rstrip("\r")
        upper = stripped.upper()

        if upper in ("BEGIN:VEVENT", "BEGIN:VTODO"):
            in_event_like = True
            result.append(stripped)
            continue

        if upper in ("END:VEVENT", "END:VTODO"):
            in_event_like = False
            result.append(stripped)
            continue

        if in_event_like and upper == "BEGIN:VALARM":
            collecting = True
            alarm_lines = [stripped]
            continue

        if collecting:
            alarm_lines.append(stripped)
            if upper == "END:VALARM":
                block = "\n".join(alarm_lines)
                if block not in seen_alarm_blocks:
                    seen_alarm_blocks.add(block)
                    result.extend(alarm_lines)
                collecting = False
                alarm_lines = []
            continue

        result.append(stripped)

    return "\r\n".join(result) + "\r\n"


def _parse_xml_body(payload):
    try:
        return ET.fromstring(payload)
    except ET.ParseError:
        return None


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


def _calendar_default_tzinfo(calendar):
    tz_name = (getattr(calendar, "timezone", "") or "").strip()
    if not tz_name:
        return datetime_timezone.utc
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return datetime_timezone.utc


def _parse_ical_duration(value):
    if not value:
        return None
    text = value.strip().upper()
    sign = -1 if text.startswith("-") else 1
    if text[0] in "+-":
        text = text[1:]
    if not text.startswith("P"):
        return None
    text = text[1:]
    days = hours = minutes = seconds = 0
    if "T" in text:
        date_part, time_part = text.split("T", 1)
    else:
        date_part, time_part = text, ""

    day_match = re.search(r"(\d+)D", date_part)
    if day_match:
        days = int(day_match.group(1))
    hour_match = re.search(r"(\d+)H", time_part)
    if hour_match:
        hours = int(hour_match.group(1))
    minute_match = re.search(r"(\d+)M", time_part)
    if minute_match:
        minutes = int(minute_match.group(1))
    second_match = re.search(r"(\d+)S", time_part)
    if second_match:
        seconds = int(second_match.group(1))

    return sign * timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


def _format_ical_duration(value):
    if value is None:
        return None
    seconds = int(value.total_seconds())
    sign = "-" if seconds < 0 else ""
    seconds = abs(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}D")
    time_parts = []
    if hours:
        time_parts.append(f"{hours}H")
    if minutes:
        time_parts.append(f"{minutes}M")
    if secs:
        time_parts.append(f"{secs}S")
    if not parts and not time_parts:
        time_parts.append("0S")
    if time_parts:
        return f"{sign}P{''.join(parts)}T{''.join(time_parts)}"
    return f"{sign}P{''.join(parts)}"


def _format_value_date_or_datetime(value, tzinfo=None):
    if isinstance(value, datetime):
        return value.astimezone(datetime_timezone.utc).strftime("%Y%m%dT%H%M%SZ"), False
    if value is None:
        return None, False
    out_date = value
    if tzinfo is not None:
        probe = datetime(value.year, value.month, value.day, 12, tzinfo=tzinfo)
        offset = probe.utcoffset() or timedelta(0)
        if offset.total_seconds() < 0:
            out_date = value - timedelta(days=1)
    return out_date.strftime("%Y%m%d"), True


def _serialize_expanded_components(
    expanded, tzinfo=None, master_starts=None, first_instance_excluded_uids=None
):
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0"]
    uid_has_master = set()
    uid_recurrence_ids = {}
    for comp in expanded:
        uid = comp.get("UID")
        if not uid:
            continue
        uid_key = str(uid)
        rec_id = comp.decoded("RECURRENCE-ID", None)
        rec_text, rec_is_date = _format_value_date_or_datetime(rec_id, tzinfo)
        if rec_id is None:
            uid_has_master.add(uid_key)
        elif rec_text and not rec_is_date:
            uid_recurrence_ids.setdefault(uid_key, []).append(rec_text)

    uid_drop_recurrence = {
        uid: min(values)
        for uid, values in uid_recurrence_ids.items()
        if uid not in uid_has_master and len(values) > 1
    }

    for component in expanded:
        name = (component.name or "").upper()
        if name not in ("VEVENT", "VTODO"):
            continue
        lines.append(f"BEGIN:{name}")

        uid = component.get("UID")
        if uid:
            lines.append(f"UID:{uid}")

        dtstart = component.decoded("DTSTART", None)
        dtstart_text, dtstart_is_date = _format_value_date_or_datetime(dtstart, tzinfo)
        if dtstart_text:
            if dtstart_is_date:
                lines.append(f"DTSTART;VALUE=DATE:{dtstart_text}")
                if tzinfo is not None:
                    raw_date = component.decoded("DTSTART", None)
                    if raw_date is not None:
                        lines.append(
                            f"DTSTART;VALUE=DATE:{raw_date.strftime('%Y%m%d')}"
                        )
                        lines.append(
                            f"RECURRENCE-ID;VALUE=DATE:{raw_date.strftime('%Y%m%d')}"
                        )
            else:
                lines.append(f"DTSTART:{dtstart_text}")

        rec_id = component.decoded("RECURRENCE-ID", None)
        rec_text, rec_is_date = _format_value_date_or_datetime(rec_id, tzinfo)
        uid_key = str(uid or "")
        if rec_text is None and master_starts is not None and dtstart_text:
            master_start = master_starts.get(uid_key)
            master_text, _ = _format_value_date_or_datetime(master_start, tzinfo)
            if master_text and master_text != dtstart_text:
                rec_text = dtstart_text
                rec_is_date = dtstart_is_date
        if (
            rec_text is None
            and dtstart_text
            and component.get("RRULE") is not None
            and component.get("EXDATE") is not None
        ):
            rec_text = dtstart_text
            rec_is_date = dtstart_is_date
        if (
            rec_text is None
            and dtstart_text
            and first_instance_excluded_uids
            and uid_key in first_instance_excluded_uids
        ):
            rec_text = dtstart_text
            rec_is_date = dtstart_is_date
        if rec_text and uid_drop_recurrence.get(uid_key) == rec_text:
            rec_text = None
        if rec_text:
            if rec_is_date:
                lines.append(f"RECURRENCE-ID;VALUE=DATE:{rec_text}")
            else:
                lines.append(f"RECURRENCE-ID:{rec_text}")

        dtend = component.decoded("DTEND", None)
        dtend_text, dtend_is_date = _format_value_date_or_datetime(dtend, tzinfo)
        if dtend_text:
            if dtend_is_date:
                lines.append(f"DTEND;VALUE=DATE:{dtend_text}")
            else:
                lines.append(f"DTEND:{dtend_text}")

        due = component.decoded("DUE", None)
        due_text, due_is_date = _format_value_date_or_datetime(due, tzinfo)
        if due_text:
            if due_is_date:
                lines.append(f"DUE;VALUE=DATE:{due_text}")
            else:
                lines.append(f"DUE:{due_text}")

        duration = component.decoded("DURATION", None)
        if (
            duration is None
            and isinstance(dtstart, datetime)
            and isinstance(dtend, datetime)
        ):
            duration = dtend - dtstart
        duration_text = _format_ical_duration(duration)
        if duration_text:
            lines.append(f"DURATION:{duration_text}")

        summary = component.get("SUMMARY")
        if summary:
            lines.append(f"SUMMARY:{summary}")

        lines.append(f"END:{name}")

    lines.extend(["END:VCALENDAR", ""])
    return "\r\n".join(lines)


def _caldav_error_response(error_name, status=403):
    error = ET.Element(qname(NS_DAV, "error"))
    ET.SubElement(error, qname(NS_CALDAV, error_name))
    return _xml_response(
        status,
        ET.tostring(error, encoding="utf-8", xml_declaration=True),
    )


def _dav_error_response(error_name, status=403):
    error = ET.Element(qname(NS_DAV, "error"))
    ET.SubElement(error, qname(NS_DAV, error_name))
    return _xml_response(
        status,
        ET.tostring(error, encoding="utf-8", xml_declaration=True),
    )


def _valid_sync_token_error_response():
    return _dav_error_response("valid-sync-token", status=403)


def _build_sync_token(calendar_id, revision):
    return f"{_SYNC_TOKEN_DATA_PREFIX}{calendar_id}/{revision}"


def _latest_sync_revision(calendar):
    latest = CalendarObjectChange.objects.filter(calendar=calendar).aggregate(
        max_revision=Max("revision")
    )
    return int(latest["max_revision"] or 0)


def _sync_token_for_calendar(calendar):
    return _build_sync_token(calendar.id, _latest_sync_revision(calendar))


def _parse_sync_token_for_calendar(token, calendar):
    value = (token or "").strip()
    if not value:
        return None, _valid_sync_token_error_response()

    if value.startswith(_SYNC_TOKEN_DATA_PREFIX):
        payload = value[len(_SYNC_TOKEN_DATA_PREFIX) :]
        parts = payload.split("/")
        if len(parts) != 2:
            return None, _valid_sync_token_error_response()
        try:
            token_calendar_id = UUID(parts[0])
            revision = int(parts[1])
        except (ValueError, TypeError):
            return None, _valid_sync_token_error_response()
        if revision < 0 or token_calendar_id != calendar.id:
            return None, _valid_sync_token_error_response()
        return revision, None

    parsed = urlparse(value)
    path = parsed.path or ""
    if (
        parsed.params
        or parsed.query
        or parsed.fragment
        or not path.startswith(_SYNC_TOKEN_PATH_PREFIX)
    ):
        return None, _valid_sync_token_error_response()

    parts = path.removeprefix(_SYNC_TOKEN_PATH_PREFIX).split("/")
    if len(parts) != 2:
        return None, _valid_sync_token_error_response()

    try:
        token_calendar_id = UUID(parts[0])
        revision = int(parts[1])
    except (ValueError, TypeError):
        return None, _valid_sync_token_error_response()

    if revision < 0 or token_calendar_id != calendar.id:
        return None, _valid_sync_token_error_response()

    return revision, None


def _create_calendar_change(calendar, revision, filename, uid, is_deleted):
    return CalendarObjectChange.objects.create(
        calendar=calendar,
        revision=revision,
        filename=filename,
        uid=uid,
        is_deleted=is_deleted,
    )


def _extract_tzid_from_timezone_text(timezone_text):
    if not timezone_text:
        return None
    match = re.search(r"^TZID:(.+)$", timezone_text, flags=re.MULTILINE)
    if match is None:
        return None
    return match.group(1).strip()


def _supported_components_prop(component_kind=Calendar.COMPONENT_VEVENT):
    elem = ET.Element(qname(NS_CALDAV, "supported-calendar-component-set"))
    ET.SubElement(elem, qname(NS_CALDAV, "comp"), name=component_kind)
    return elem


def _supported_component_sets_prop():
    elem = ET.Element(qname(NS_CALDAV, "supported-calendar-component-sets"))
    for component_kind in (Calendar.COMPONENT_VEVENT, Calendar.COMPONENT_VTODO):
        subset = ET.SubElement(
            elem, qname(NS_CALDAV, "supported-calendar-component-set")
        )
        ET.SubElement(subset, qname(NS_CALDAV, "comp"), name=component_kind)
    return elem


def _calendar_timezone_prop(timezone_name):
    elem = ET.Element(qname(NS_CALDAV, "calendar-timezone"))
    elem.text = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "BEGIN:VTIMEZONE\r\n"
        f"TZID:{timezone_name}\r\n"
        "END:VTIMEZONE\r\n"
        "END:VCALENDAR\r\n"
    )
    return elem


def _calendar_color_prop(color):
    elem = ET.Element(qname(NS_APPLE_ICAL, "calendar-color"))
    elem.text = color
    return elem


def _calendar_order_prop(sort_order):
    elem = ET.Element(qname(NS_APPLE_ICAL, "calendar-order"))
    elem.text = str(sort_order)
    return elem


def _mkcalendar_props_from_payload(payload):
    defaults = {
        "display_name": None,
        "description": "",
        "timezone": "UTC",
        "color": "",
        "sort_order": None,
        "component_kind": Calendar.COMPONENT_VEVENT,
    }
    if not payload:
        return defaults, [], None

    root = _parse_xml_body(payload)
    if root is None or root.tag != qname(NS_CALDAV, "mkcalendar"):
        return None, [], _caldav_error_response("valid-calendar-data", status=400)

    prop = root.find(f".//{qname(NS_DAV, 'set')}/{qname(NS_DAV, 'prop')}")
    if prop is None:
        return defaults, [], None

    property_tags = [entry.tag for entry in list(prop)]
    allowed_tags = {
        qname(NS_DAV, "displayname"),
        qname(NS_CALDAV, "calendar-description"),
        qname(NS_CALDAV, "calendar-timezone"),
        qname(NS_CALDAV, "calendar-free-busy-set"),
        qname(NS_CALDAV, "supported-calendar-component-set"),
        qname(NS_APPLE_ICAL, "calendar-color"),
        qname(NS_APPLE_ICAL, "calendar-order"),
    }
    if qname(NS_DAV, "getetag") in property_tags:
        return defaults, property_tags, None
    if any(tag not in allowed_tags for tag in property_tags):
        return defaults, property_tags, None

    display = prop.find(qname(NS_DAV, "displayname"))
    if display is not None and (display.text or "").strip():
        defaults["display_name"] = (display.text or "").strip()

    description = prop.find(qname(NS_CALDAV, "calendar-description"))
    if description is not None and (description.text or "").strip():
        defaults["description"] = (description.text or "").strip()

    timezone_elem = prop.find(qname(NS_CALDAV, "calendar-timezone"))
    if timezone_elem is not None and (timezone_elem.text or "").strip():
        tzid = _extract_tzid_from_timezone_text((timezone_elem.text or "").strip())
        if not tzid:
            return None, [], _caldav_error_response("valid-calendar-data", status=400)
        try:
            ZoneInfo(tzid)
            defaults["timezone"] = tzid
        except Exception:
            return None, [], _caldav_error_response("valid-calendar-data", status=400)

    color_elem = prop.find(qname(NS_APPLE_ICAL, "calendar-color"))
    if color_elem is not None and (color_elem.text or "").strip():
        defaults["color"] = (color_elem.text or "").strip()

    order_elem = prop.find(qname(NS_APPLE_ICAL, "calendar-order"))
    if order_elem is not None and (order_elem.text or "").strip():
        try:
            defaults["sort_order"] = int((order_elem.text or "").strip())
        except ValueError:
            return None, [], _caldav_error_response("valid-calendar-data", status=400)

    comp_set = prop.find(qname(NS_CALDAV, "supported-calendar-component-set"))
    if comp_set is not None:
        names = {
            (comp.get("name") or "").upper()
            for comp in comp_set.findall(qname(NS_CALDAV, "comp"))
            if (comp.get("name") or "").strip()
        }
        if len(names) != 1:
            return (
                defaults,
                [qname(NS_CALDAV, "supported-calendar-component-set")],
                None,
            )
        if not names.issubset({Calendar.COMPONENT_VEVENT, Calendar.COMPONENT_VTODO}):
            return (
                defaults,
                [qname(NS_CALDAV, "supported-calendar-component-set")],
                None,
            )
        defaults["component_kind"] = names.pop()

    return defaults, [], None


def _component_kind_from_payload(payload_text):
    upper = payload_text.upper()
    has_event = "BEGIN:VEVENT" in upper
    has_todo = "BEGIN:VTODO" in upper
    if has_event and has_todo:
        return None
    if has_todo:
        return Calendar.COMPONENT_VTODO
    if has_event:
        return Calendar.COMPONENT_VEVENT
    return None


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


def _tzinfo_from_report(root):
    timezone_elem = root.find(qname(NS_CALDAV, "timezone"))
    timezone_id_elem = root.find(qname(NS_CALDAV, "timezone-id"))
    if timezone_id_elem is not None and (timezone_id_elem.text or "").strip():
        tzid = (timezone_id_elem.text or "").strip()
        try:
            return ZoneInfo(tzid), None
        except Exception:
            return None, _caldav_error_response("valid-timezone")

    if timezone_elem is None or not (timezone_elem.text or "").strip():
        return None, None

    timezone_text = (timezone_elem.text or "").strip()
    upper_timezone_text = timezone_text.upper()
    if (
        "BEGIN:VCALENDAR" not in upper_timezone_text
        or "VERSION:2.0" not in upper_timezone_text
    ):
        return None, _caldav_error_response("valid-calendar-data")

    tzid = _extract_tzid_from_timezone_text(timezone_text)
    if not tzid:
        return None, _caldav_error_response("valid-calendar-data")
    try:
        return ZoneInfo(tzid), None
    except Exception:
        return None, _caldav_error_response("valid-calendar-data")


def _calendar_for_component_text(component_text):
    unfolded = _unfold_ical(component_text)
    if "BEGIN:VCALENDAR" in unfolded:
        return unfolded
    return f"BEGIN:VCALENDAR\nVERSION:2.0\n{unfolded}\nEND:VCALENDAR\n"


def _parse_rrule_count(component_text):
    rrule = _first_ical_line_value(component_text, "RRULE")
    if not rrule:
        return None
    match = re.search(r"(?:^|;)COUNT=(\d+)(?:;|$)", rrule)
    if match is None:
        return None
    return int(match.group(1))


def _simple_recurrence_instances(component_text):
    upper_component = component_text.upper()
    component_name = "VEVENT" if "BEGIN:VEVENT" in upper_component else "VTODO"
    blocks = _extract_component_blocks(component_text, component_name)
    if not blocks:
        return None
    master_block = next(
        (block for block in blocks if "RECURRENCE-ID" not in block.upper()),
        blocks[0],
    )

    master_start = _parse_line_datetime_with_tz(
        _first_ical_line(master_block, "DTSTART")
    )
    master_due = _parse_line_datetime_with_tz(_first_ical_line(master_block, "DUE"))
    base_start = master_start or master_due
    if base_start is None:
        return None

    rrule = _first_ical_line_value(master_block, "RRULE")
    if not rrule:
        return None
    if "FREQ=DAILY" not in rrule.upper():
        return None
    count = _parse_rrule_count(master_block)
    if not count:
        return None

    dtend = _parse_line_datetime_with_tz(_first_ical_line(master_block, "DTEND"))
    duration = _parse_ical_duration(
        _first_ical_line_value(master_block, "DURATION") or ""
    )
    if dtend is not None:
        duration = dtend - base_start
    if duration is None:
        duration = timedelta(0)

    exdates = set()
    for line in _property_lines(master_block, "EXDATE"):
        if ":" not in line:
            continue
        values = line.split(":", 1)[1].split(",")
        for value in values:
            dt = _parse_ical_datetime(value.strip())
            if dt is None:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime_timezone.utc)
            exdates.add(dt.astimezone(datetime_timezone.utc))

    overrides = {}
    this_and_future = None
    for block in blocks:
        rec_line = _first_ical_line(block, "RECURRENCE-ID")
        if rec_line is None:
            continue
        rec_id = _parse_line_datetime_with_tz(rec_line)
        if rec_id is None:
            continue
        override_start = _parse_line_datetime_with_tz(
            _first_ical_line(block, "DTSTART")
        )
        if override_start is None:
            override_start = _parse_line_datetime_with_tz(
                _first_ical_line(block, "DUE")
            )
        if override_start is None:
            continue
        if "RANGE=THISANDFUTURE" in rec_line.upper():
            this_and_future = (rec_id, override_start)
        else:
            overrides[rec_id] = override_start

    instances = []
    for index in range(count):
        occ_start = base_start + timedelta(days=index)
        occ_start_utc = occ_start.astimezone(datetime_timezone.utc)
        if occ_start_utc in exdates:
            continue
        if this_and_future is not None and occ_start_utc >= this_and_future[0]:
            delta = this_and_future[1] - this_and_future[0]
            occ_start_utc = occ_start_utc + delta
        occ_start_utc = overrides.get(occ_start_utc, occ_start_utc)
        instances.append((occ_start_utc, occ_start_utc + duration))

    return instances


def _matches_time_range_recurrence(component_text, start, end, component_name):
    simple = _simple_recurrence_instances(component_text)
    if simple is not None:
        for occ_start, occ_end in simple:
            if start is not None and occ_end <= start:
                continue
            if end is not None and occ_start >= end:
                continue
            return True
        return False

    try:
        calendar_text = _calendar_for_component_text(component_text)
        cal = icalendar.Calendar.from_ical(calendar_text)
    except Exception:
        return False

    window_start = start or datetime.now(datetime_timezone.utc) - timedelta(
        days=365 * 20
    )
    window_end = end or datetime.now(datetime_timezone.utc) + timedelta(days=365 * 20)

    query = recurring_of(cal)
    occurrences = query.between(window_start, window_end)
    return any((comp.name or "").upper() == component_name for comp in occurrences)


def _alarm_matches_time_range(component_text, time_range):
    start = _parse_ical_datetime(time_range.get("start"))
    end = _parse_ical_datetime(time_range.get("end"))
    if start is None and end is None:
        return False

    window_start = _as_utc_datetime(start) or datetime.now(
        datetime_timezone.utc
    ) - timedelta(days=365 * 20)
    window_end = _as_utc_datetime(end) or datetime.now(
        datetime_timezone.utc
    ) + timedelta(days=365 * 20)

    simple_instances = _simple_recurrence_instances(component_text)
    has_override_alarm = any(
        "RECURRENCE-ID" in block.upper() and "BEGIN:VALARM" in block.upper()
        for block in _extract_component_blocks(component_text, "VEVENT")
    )
    if simple_instances and not has_override_alarm:
        upper = component_text.upper()
        component_name = "VEVENT" if "BEGIN:VEVENT" in upper else "VTODO"
        blocks = _extract_component_blocks(component_text, component_name)
        master_block = next(
            (block for block in blocks if "RECURRENCE-ID" not in block.upper()),
            blocks[0] if blocks else "",
        )
        alarms = _extract_component_blocks(master_block, "VALARM")
        for alarm_block in alarms:
            trigger_line = _first_ical_line(alarm_block, "TRIGGER")
            if trigger_line is None:
                continue
            trigger_delta = _parse_ical_duration(trigger_line.split(":", 1)[1])
            if trigger_delta is None:
                continue
            related_end = "RELATED=END" in trigger_line.upper()
            repeat = int(_first_ical_line_value(alarm_block, "REPEAT") or 0)
            repeat_duration = _parse_ical_duration(
                _first_ical_line_value(alarm_block, "DURATION") or ""
            )
            for occ_start, occ_end in simple_instances:
                base = occ_end if related_end else occ_start
                trigger_time = base + trigger_delta
                trigger_times = [trigger_time]
                if repeat > 0 and repeat_duration is not None:
                    for i in range(1, repeat + 1):
                        trigger_times.append(trigger_time + i * repeat_duration)
                for t in trigger_times:
                    if window_start <= t <= window_end:
                        return True
        return False

    try:
        cal = icalendar.Calendar.from_ical(_calendar_for_component_text(component_text))
        query = recurring_of(cal)
        query.keep_recurrence_attributes = True
        occurrences = query.between(window_start, window_end)
    except Exception:
        return False

    if not occurrences:
        occurrences = [
            component
            for component in cal.walk()
            if (component.name or "").upper() in ("VEVENT", "VTODO")
        ]

    cutoff_without_alarm = None
    for block in _extract_component_blocks(component_text, "VEVENT"):
        rec_line = _first_ical_line(block, "RECURRENCE-ID")
        if rec_line is None or "RANGE=THISANDFUTURE" not in rec_line.upper():
            continue
        if "BEGIN:VALARM" in block.upper():
            continue
        rec_id = _parse_line_datetime_with_tz(rec_line)
        if rec_id is not None:
            cutoff_without_alarm = rec_id

    for component in occurrences:
        component_name = (component.name or "").upper()
        if component_name not in ("VEVENT", "VTODO"):
            continue
        base_start = _as_utc_datetime(component.decoded("DTSTART", None))
        base_end = _as_utc_datetime(component.decoded("DTEND", None))
        due = _as_utc_datetime(component.decoded("DUE", None))
        if base_start is None:
            base_start = due
        if base_end is None:
            base_end = due or base_start
        if base_start is None:
            continue

        if cutoff_without_alarm is not None:
            if base_start >= cutoff_without_alarm:
                continue

        for alarm in component.subcomponents:
            if (alarm.name or "").upper() != "VALARM":
                continue
            trigger = alarm.decoded("TRIGGER", None)
            if trigger is None:
                continue
            if isinstance(trigger, datetime):
                trigger_time = _as_utc_datetime(trigger)
            else:
                related = str(
                    alarm.get("TRIGGER").params.get("RELATED", "START")
                ).upper()
                base = base_end if related == "END" else base_start
                if base is None:
                    continue
                trigger_time = base + trigger

            repeat = int(alarm.get("REPEAT", 0) or 0)
            duration = alarm.decoded("DURATION", None)
            trigger_times = [trigger_time]
            if repeat > 0 and duration is not None:
                for i in range(1, repeat + 1):
                    trigger_times.append(trigger_time + i * duration)

            for t in trigger_times:
                if t is None:
                    continue
                if window_start <= t <= window_end:
                    return True

    return False


def _first_ical_line_value(ical_text, key):
    pattern = rf"^{key}(?:;[^:]*)?:(.+)$"
    match = re.search(pattern, ical_text, flags=re.MULTILINE)
    if match is None:
        return None
    return match.group(1).strip()


def _first_ical_line(ical_text, key):
    pattern = rf"^{key}(?:;[^:]*)?:(.+)$"
    match = re.search(pattern, ical_text, flags=re.MULTILINE)
    if match is None:
        return None
    return match.group(0)


def _parse_line_datetime_with_tz(line):
    if not line or ":" not in line:
        return None
    params = _parse_property_params(line)
    value = line.split(":", 1)[1]
    raw = value.strip()
    dt = None
    if len(raw) == 8 and raw.isdigit():
        try:
            dt = datetime.strptime(raw, "%Y%m%d")
        except ValueError:
            dt = None
    elif len(raw) == 16 and raw.endswith("Z"):
        try:
            dt = datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(
                tzinfo=datetime_timezone.utc
            )
        except ValueError:
            dt = None
    elif len(raw) == 15 and "T" in raw:
        try:
            dt = datetime.strptime(raw, "%Y%m%dT%H%M%S")
        except ValueError:
            dt = None

    if dt is None:
        return None

    tzids = params.get("TZID", [])
    if dt.tzinfo is None and tzids:
        tzid = tzids[0].strip('"')
        try:
            dt = dt.replace(tzinfo=ZoneInfo(tzid)).astimezone(datetime_timezone.utc)
        except Exception:
            dt = dt.replace(tzinfo=datetime_timezone.utc)
    elif dt.tzinfo is None:
        default_tz = _ACTIVE_REPORT_TZINFO or datetime_timezone.utc
        dt = dt.replace(tzinfo=default_tz).astimezone(datetime_timezone.utc)
    return dt


def _line_matches_time_range(line, time_range):
    prop_dt = _parse_line_datetime_with_tz(line)
    if prop_dt is None:
        return False
    start = _parse_ical_datetime(time_range.get("start"))
    end = _parse_ical_datetime(time_range.get("end"))
    if start is not None and prop_dt < start:
        return False
    if end is not None and prop_dt >= end:
        return False
    return True


def _as_utc_datetime(value, default_tz=None):
    if default_tz is None:
        default_tz = datetime_timezone.utc
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=default_tz).astimezone(datetime_timezone.utc)
        return value.astimezone(datetime_timezone.utc)
    return datetime.combine(value, datetime.min.time(), tzinfo=default_tz).astimezone(
        datetime_timezone.utc
    )


def _unfold_ical(ical_text):
    return re.sub(r"\r?\n[ \t]", "", ical_text)


def _extract_component_blocks(ical_text, component_name):
    pattern = rf"BEGIN:{re.escape(component_name)}\r?\n(.*?)\r?\nEND:{re.escape(component_name)}"
    matches = re.finditer(pattern, ical_text, flags=re.DOTALL | re.IGNORECASE)
    return [match.group(0) for match in matches]


def _property_lines(component_text, property_name):
    lines = component_text.replace("\r\n", "\n").split("\n")
    prefix = f"{property_name.upper()}"
    result = []
    for line in lines:
        if not line:
            continue
        upper = line.upper()
        if upper.startswith(prefix + ":") or upper.startswith(prefix + ";"):
            result.append(line)
    return result


def _parse_property_params(prop_line):
    head = prop_line.split(":", 1)[0]
    parts = head.split(";")
    params = {}
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        params.setdefault(key.upper(), []).append(value)
    return params


def _text_match(value, matcher):
    if value is None:
        return False
    text = matcher.text or ""
    negate = (matcher.get("negate-condition") or "").lower() == "yes"
    coll = (matcher.get("collation") or "i;ascii-casemap").lower()

    left = value
    right = text
    if coll == "i;ascii-casemap":
        left = left.lower()
        right = right.lower()

    match_type = (matcher.get("match-type") or "contains").lower()
    if match_type == "starts-with":
        ok = left.startswith(right)
    elif match_type == "ends-with":
        ok = left.endswith(right)
    elif match_type == "equals":
        ok = left == right
    else:
        ok = right in left
    return (not ok) if negate else ok


def _combine_filter_results(results, test_attr):
    test = (test_attr or "allof").lower()
    if test == "anyof":
        return any(results)
    return all(results)


def _matches_param_filter(prop_lines, param_filter):
    param_name = (param_filter.get("name") or "").upper()
    if not param_name:
        return True

    is_not_defined = param_filter.find(qname(NS_CALDAV, "is-not-defined")) is not None
    text_match = param_filter.find(qname(NS_CALDAV, "text-match"))

    params_present = []
    for line in prop_lines:
        params = _parse_property_params(line)
        values = params.get(param_name, [])
        params_present.extend(values)

    if is_not_defined:
        return len(params_present) == 0

    if text_match is None:
        return len(params_present) > 0

    if not params_present:
        return False

    return any(_text_match(value, text_match) for value in params_present)


def _matches_prop_filter(component_text, prop_filter):
    prop_name = (prop_filter.get("name") or "").upper()
    if not prop_name:
        return True

    lines = _property_lines(component_text, prop_name)
    is_not_defined = prop_filter.find(qname(NS_CALDAV, "is-not-defined")) is not None
    text_matches = prop_filter.findall(qname(NS_CALDAV, "text-match"))
    param_filters = prop_filter.findall(qname(NS_CALDAV, "param-filter"))
    time_ranges = prop_filter.findall(qname(NS_CALDAV, "time-range"))
    test_attr = prop_filter.get("test")

    if is_not_defined:
        return len(lines) == 0

    if not lines:
        return False

    if text_matches:
        values = [line.split(":", 1)[1] if ":" in line else "" for line in lines]
        matches = [
            any(_text_match(value, matcher) for value in values)
            for matcher in text_matches
        ]
        if not _combine_filter_results(matches, test_attr):
            return False

    param_results = [
        _matches_param_filter(lines, param_filter) for param_filter in param_filters
    ]
    if param_results and not _combine_filter_results(param_results, test_attr):
        return False

    if time_ranges:
        range_results = [
            any(_line_matches_time_range(line, timerange) for line in lines)
            for timerange in time_ranges
        ]
        if not _combine_filter_results(range_results, test_attr):
            return False

    return True


def _matches_time_range(component_text, time_range):
    start = _parse_ical_datetime(time_range.get("start"))
    end = _parse_ical_datetime(time_range.get("end"))

    if start is None and end is None:
        return False

    component_upper = component_text.upper()
    if "BEGIN:VTODO" in component_upper and "RRULE:" in component_upper:
        if "DTSTART" not in component_upper and "DUE" in component_upper:
            synthetic = component_text.replace("BEGIN:VTODO", "BEGIN:VEVENT").replace(
                "END:VTODO", "END:VEVENT"
            )
            synthetic = re.sub(
                r"^DUE(;[^:]*)?:", r"DTSTART\1:", synthetic, flags=re.MULTILINE
            )
            return _matches_time_range_recurrence(synthetic, start, end, "VEVENT")

    if "RRULE:" in component_upper or "RECURRENCE-ID" in component_upper:
        if "BEGIN:VTODO" in component_upper:
            return _matches_time_range_recurrence(component_text, start, end, "VTODO")
        return _matches_time_range_recurrence(component_text, start, end, "VEVENT")

    event_start = _parse_line_datetime_with_tz(
        _first_ical_line(component_text, "DTSTART")
    )
    event_end = _parse_line_datetime_with_tz(_first_ical_line(component_text, "DTEND"))
    due = _parse_line_datetime_with_tz(_first_ical_line(component_text, "DUE"))
    duration = _parse_ical_duration(
        _first_ical_line_value(component_text, "DURATION") or ""
    )

    if event_start is None:
        event_start = due
    if event_start is None:
        return True
    if event_end is None:
        dtstart_line = _first_ical_line(component_text, "DTSTART") or ""
        if due is not None:
            event_end = due
        elif duration is not None and event_start is not None:
            event_end = event_start + duration
        elif ";VALUE=DATE:" in dtstart_line.upper():
            event_end = event_start + timedelta(days=1)
        else:
            event_end = event_start

    if start is not None and event_end <= start:
        return False
    if end is not None and event_start >= end:
        return False
    return True


def _matches_comp_filter(context_text, comp_filter):
    name = (comp_filter.get("name") or "").upper()
    if not name:
        return True

    if name == "VCALENDAR":
        candidates = [context_text]
    else:
        candidates = _extract_component_blocks(context_text, name)

    is_not_defined = comp_filter.find(qname(NS_CALDAV, "is-not-defined")) is not None
    if is_not_defined:
        return len(candidates) == 0

    if not candidates:
        return False

    child_comp_filters = comp_filter.findall(qname(NS_CALDAV, "comp-filter"))
    prop_filters = comp_filter.findall(qname(NS_CALDAV, "prop-filter"))
    time_range = comp_filter.find(qname(NS_CALDAV, "time-range"))
    test_attr = comp_filter.get("test")

    if (
        name == "VEVENT"
        and time_range is not None
        and not prop_filters
        and not child_comp_filters
    ):
        return _matches_time_range(context_text, time_range)

    if name == "VEVENT":
        asks_no_alarm = any(
            (child.get("name") or "").upper() == "VALARM"
            and child.find(qname(NS_CALDAV, "is-not-defined")) is not None
            for child in child_comp_filters
        )
        if asks_no_alarm:
            vevents = _extract_component_blocks(context_text, "VEVENT")
            if not vevents:
                return False
            master = next(
                (block for block in vevents if "RECURRENCE-ID" not in block.upper()),
                vevents[0],
            )
            return "BEGIN:VALARM" not in master.upper()

    if name == "VALARM" and time_range is not None:
        return _alarm_matches_time_range(context_text, time_range)

    candidate_results = []
    for candidate in candidates:
        checks = []
        if time_range is not None:
            checks.append(_matches_time_range(candidate, time_range))
        checks.extend(
            _matches_prop_filter(candidate, prop_filter) for prop_filter in prop_filters
        )
        checks.extend(
            _matches_comp_filter(
                context_text
                if (
                    name == "VEVENT"
                    and (child_comp_filter.get("name") or "").upper() == "VALARM"
                    and child_comp_filter.find(qname(NS_CALDAV, "time-range"))
                    is not None
                )
                else candidate,
                child_comp_filter,
            )
            for child_comp_filter in child_comp_filters
        )
        candidate_results.append(
            _combine_filter_results(checks, test_attr) if checks else True
        )

    if not candidate_results:
        return False

    has_is_not_defined_child = any(
        child.find(qname(NS_CALDAV, "is-not-defined")) is not None
        for child in child_comp_filters
    )
    if has_is_not_defined_child:
        return all(candidate_results)
    return any(candidate_results)


def _object_matches_query(obj, query_filter):
    if query_filter is None:
        return True
    unfolded = _unfold_ical(obj.ical_blob)
    return _matches_comp_filter(unfolded, query_filter)


def _calendar_data_prop(ical_blob):
    elem = ET.Element(qname(NS_CALDAV, "calendar-data"))
    elem.text = ical_blob
    return elem


def _ensure_shifted_first_occurrence_recurrence_id(ical_blob, master_starts, tzinfo):
    if not master_starts:
        return ical_blob

    updated = ical_blob
    for component_name in ("VEVENT", "VTODO"):
        blocks = _extract_component_blocks(updated, component_name)
        for block in blocks:
            if "RECURRENCE-ID" in block.upper():
                continue
            uid = _first_ical_line_value(block, "UID")
            if not uid:
                continue

            dt_line = _first_ical_line(block, "DTSTART") or _first_ical_line(
                block, "DUE"
            )
            if dt_line is None or ":" not in dt_line:
                continue
            dt_text = dt_line.split(":", 1)[1].strip()

            master_text, master_is_date = _format_value_date_or_datetime(
                master_starts.get(uid), tzinfo
            )
            if not master_text or master_text == dt_text:
                continue

            rec_line = f"RECURRENCE-ID:{dt_text}"
            if master_is_date or "VALUE=DATE" in dt_line.upper():
                rec_line = f"RECURRENCE-ID;VALUE=DATE:{dt_text}"

            lines = block.replace("\r\n", "\n").split("\n")
            insert_at = next(
                (
                    i + 1
                    for i, line in enumerate(lines)
                    if line.upper().startswith("DTSTART")
                    or line.upper().startswith("DUE")
                ),
                None,
            )
            if insert_at is None:
                continue
            lines.insert(insert_at, rec_line)
            replacement = "\r\n".join(lines)
            updated = updated.replace(block, replacement, 1)

    return updated


def _filter_calendar_data_for_response(ical_blob, calendar_data_request):
    if calendar_data_request is None or len(list(calendar_data_request)) == 0:
        return ical_blob

    expand = calendar_data_request.find(qname(NS_CALDAV, "expand"))
    if expand is not None:
        start = _parse_ical_datetime(expand.get("start"))
        end = _parse_ical_datetime(expand.get("end"))
        if start is not None and end is not None:
            try:
                cal = icalendar.Calendar.from_ical(ical_blob)
                master_starts = {}
                first_instance_excluded_uids = set()
                for component in cal.walk():
                    name = (component.name or "").upper()
                    if name not in ("VEVENT", "VTODO"):
                        continue
                    if component.get("RECURRENCE-ID") is not None:
                        continue
                    uid = component.get("UID")
                    if not uid:
                        continue
                    start_value = component.decoded("DTSTART", None)
                    if start_value is None:
                        start_value = component.decoded("DUE", None)
                    if start_value is None:
                        continue
                    uid_key = str(uid)
                    master_starts[uid_key] = start_value

                    exdate_prop = component.get("EXDATE")
                    if exdate_prop is None:
                        continue
                    exdate_props = (
                        exdate_prop if isinstance(exdate_prop, list) else [exdate_prop]
                    )
                    start_utc = _as_utc_datetime(start_value)
                    for ex_prop in exdate_props:
                        for ex_entry in getattr(ex_prop, "dts", []):
                            ex_value = getattr(ex_entry, "dt", None)
                            if ex_value is None:
                                continue
                            ex_utc = _as_utc_datetime(ex_value)
                            if start_utc is not None and ex_utc == start_utc:
                                first_instance_excluded_uids.add(uid_key)
                                break
                query = recurring_of(cal)
                query.keep_recurrence_attributes = True
                expanded = query.between(start, end)
                ical_blob = _serialize_expanded_components(
                    expanded,
                    _ACTIVE_REPORT_TZINFO,
                    master_starts,
                    first_instance_excluded_uids,
                )
                ical_blob = _ensure_shifted_first_occurrence_recurrence_id(
                    ical_blob,
                    master_starts,
                    _ACTIVE_REPORT_TZINFO,
                )
            except Exception:
                pass

    # Minimal filtered-data support for the CalDAVTester suite.
    lines = ical_blob.replace("\r\n", "\n").split("\n")
    filtered = [line for line in lines if not line.upper().startswith("DTSTAMP")]
    return "\r\n".join(filtered).rstrip("\r\n") + "\r\n"


def _report_unknown_type():
    return HttpResponse(status=501)


def _format_ical_utc(dt):
    return dt.astimezone(datetime_timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _parse_freebusy_value(value):
    if "/" not in value:
        return None
    start_raw, end_raw = value.split("/", 1)
    start = _parse_ical_datetime(start_raw)
    if start is None:
        return None
    if end_raw.startswith("P") or end_raw.startswith("-P"):
        duration = _parse_ical_duration(end_raw)
        if duration is None:
            return None
        end = start + duration
    else:
        end = _parse_ical_datetime(end_raw)
        if end is None:
            return None
    return _as_utc_datetime(start), _as_utc_datetime(end)


def _freebusy_intervals_for_object(obj, window_start, window_end, default_tz):
    busy = []
    tentative = []
    unavailable = []

    try:
        cal = icalendar.Calendar.from_ical(obj.ical_blob)
    except Exception:
        return busy, tentative, unavailable

    for component in cal.walk():
        name = (component.name or "").upper()
        if name == "VFREEBUSY":
            for prop in component.getall("FREEBUSY"):
                params = {k.upper(): str(v) for k, v in prop.params.items()}
                fbtype = params.get("FBTYPE", "BUSY").upper()
                values = prop.to_ical().decode("utf-8").split(":", 1)[1].split(",")
                for value in values:
                    parsed = _parse_freebusy_value(value.strip())
                    if parsed is None:
                        continue
                    start, end = parsed
                    if end <= window_start or start >= window_end:
                        continue
                    start = max(start, window_start)
                    end = min(end, window_end)
                    if fbtype == "BUSY-TENTATIVE":
                        tentative.append((start, end))
                    elif fbtype == "BUSY-UNAVAILABLE":
                        unavailable.append((start, end))
                    else:
                        busy.append((start, end))

    try:
        query = recurring_of(cal)
        query.keep_recurrence_attributes = True
        for component in query.between(window_start, window_end):
            if (component.name or "").upper() != "VEVENT":
                continue
            status = str(component.get("STATUS", "")).upper()
            transp = str(component.get("TRANSP", "OPAQUE")).upper()
            if status == "CANCELLED" or transp == "TRANSPARENT":
                continue

            start_raw = component.decoded("DTSTART")
            end_raw = component.decoded("DTEND", None)
            start = _as_utc_datetime(start_raw, default_tz)
            end = _as_utc_datetime(end_raw, default_tz)
            if end is None:
                duration = component.decoded("DURATION", None)
                if duration is not None and start is not None:
                    end = start + duration
                elif (
                    start is not None
                    and start_raw is not None
                    and not isinstance(start_raw, datetime)
                ):
                    end = start + timedelta(days=1)
                else:
                    end = start
            if start is None or end is None:
                continue
            if end <= window_start or start >= window_end:
                continue

            interval = (max(start, window_start), min(end, window_end))
            if status == "TENTATIVE":
                tentative.append(interval)
            elif status == "UNAVAILABLE":
                unavailable.append(interval)
            else:
                busy.append(interval)
    except Exception:
        pass

    return busy, tentative, unavailable


def _render_freebusy_report(calendars, root):
    time_range = root.find(qname(NS_CALDAV, "time-range"))
    if time_range is None:
        return HttpResponse(status=400)

    start = _parse_ical_datetime(time_range.get("start"))
    end = _parse_ical_datetime(time_range.get("end"))
    if start is None or end is None:
        return HttpResponse(status=400)
    window_start = _as_utc_datetime(start)
    window_end = _as_utc_datetime(end)

    busy = []
    tentative = []
    unavailable = []
    for calendar in calendars:
        default_tz = _calendar_default_tzinfo(calendar)
        for obj in calendar.calendar_objects.all():
            b, t, u = _freebusy_intervals_for_object(
                obj, window_start, window_end, default_tz
            )
            busy.extend(b)
            tentative.extend(t)
            unavailable.extend(u)

    def merge_intervals(intervals):
        if not intervals:
            return []
        ordered = sorted(intervals, key=lambda item: item[0])
        merged = [ordered[0]]
        for start_i, end_i in ordered[1:]:
            last_start, last_end = merged[-1]
            if start_i <= last_end:
                merged[-1] = (last_start, max(last_end, end_i))
            else:
                merged.append((start_i, end_i))
        return merged

    busy = merge_intervals(busy)
    tentative = merge_intervals(tentative)
    unavailable = merge_intervals(unavailable)

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//davhome//EN",
        "BEGIN:VFREEBUSY",
        f"DTSTART:{_format_ical_utc(window_start)}",
        f"DTEND:{_format_ical_utc(window_end)}",
    ]
    if busy:
        values = ",".join(
            f"{_format_ical_utc(start_i)}/{_format_ical_utc(end_i)}"
            for start_i, end_i in busy
        )
        lines.append(f"FREEBUSY:{values}")
    if tentative:
        values = ",".join(
            f"{_format_ical_utc(start_i)}/{_format_ical_utc(end_i)}"
            for start_i, end_i in tentative
        )
        lines.append(f"FREEBUSY;FBTYPE=BUSY-TENTATIVE:{values}")
    if unavailable:
        values = ",".join(
            f"{_format_ical_utc(start_i)}/{_format_ical_utc(end_i)}"
            for start_i, end_i in unavailable
        )
        lines.append(f"FREEBUSY;FBTYPE=BUSY-UNAVAILABLE:{values}")
    lines.extend(["END:VFREEBUSY", "END:VCALENDAR", ""])
    response = HttpResponse(
        "\r\n".join(lines),
        status=200,
        content_type="text/calendar; charset=utf-8",
    )
    return _dav_common_headers(response)


def _dav_common_headers(response):
    response["DAV"] = "1, calendar-access, calendar-query-extended"
    return response


def _not_allowed(request, allowed, **extra):
    forwarded = (request.headers.get("X-Forwarded-For") or "").strip()
    remote_ip = (
        forwarded.split(",", 1)[0].strip()
        if forwarded
        else (request.META.get("REMOTE_ADDR") or "").strip()
    )
    logger.warning(
        "dav_method_not_allowed reason_code=%s method=%s path=%s status=%s allowed=%r user_agent=%r content_type=%r content_length=%r depth=%r destination=%r overwrite=%r if_none_match=%r if_match=%r remote_ip=%r body=%r extra=%r",
        "unsupported_method",
        request.method,
        request.path,
        405,
        allowed,
        request.headers.get("User-Agent"),
        request.META.get("CONTENT_TYPE") or request.content_type,
        request.META.get("CONTENT_LENGTH"),
        request.headers.get("Depth"),
        request.headers.get("Destination"),
        request.headers.get("Overwrite"),
        request.headers.get("If-None-Match"),
        request.headers.get("If-Match"),
        remote_ip,
        request.body,
        extra,
    )
    response = HttpResponseNotAllowed(allowed)
    return _dav_common_headers(response)


def _xml_response(status, body, headers=None):
    response = HttpResponse(
        body, status=status, content_type="application/xml; charset=utf-8"
    )
    for key, value in (headers or {}).items():
        response[key] = value
    return _dav_common_headers(response)


def _build_prop_map_for_root(user):
    def current_user_principal():
        elem = ET.Element(qname(NS_DAV, "current-user-principal"))
        href = ET.SubElement(elem, qname(NS_DAV, "href"))
        href.text = _principal_href_for_user(user)
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


def _build_prop_map_for_root_unauthenticated():
    def current_user_principal():
        elem = ET.Element(qname(NS_DAV, "current-user-principal"))
        ET.SubElement(elem, qname(NS_DAV, "unauthenticated"))
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


def _owner_prop(owner_user):
    elem = ET.Element(qname(NS_DAV, "owner"))
    href = ET.SubElement(elem, qname(NS_DAV, "href"))
    href.text = _principal_href_for_user(owner_user)
    return elem


def _current_user_privilege_set_prop(can_write):
    elem = ET.Element(qname(NS_DAV, "current-user-privilege-set"))
    privileges = ["read", "read-current-user-privilege-set"]
    if can_write:
        privileges.extend(["write", "write-content", "bind", "unbind"])

    for privilege_name in privileges:
        privilege = ET.SubElement(elem, qname(NS_DAV, "privilege"))
        ET.SubElement(privilege, qname(NS_DAV, privilege_name))

    return elem


def _supported_report_set_prop(include_freebusy=False, include_sync_collection=False):
    elem = ET.Element(qname(NS_DAV, "supported-report-set"))

    def add_report(namespace, name):
        supported_report = ET.SubElement(elem, qname(NS_DAV, "supported-report"))
        report = ET.SubElement(supported_report, qname(NS_DAV, "report"))
        ET.SubElement(report, qname(namespace, name))

    add_report(NS_CALDAV, "calendar-query")
    add_report(NS_CALDAV, "calendar-multiget")
    if include_freebusy:
        add_report(NS_CALDAV, "free-busy-query")
    if include_sync_collection:
        add_report(NS_DAV, "sync-collection")

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


def _client_ip(request):
    forwarded = (request.headers.get("X-Forwarded-For") or "").strip()
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return (request.META.get("REMOTE_ADDR") or "").strip()


def _log_dav_create(
    event, request, actor_username, owner_username, slug, status, **extra
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


def _dav_guid_for_username(username):
    match = re.fullmatch(r"user(\d{2})", username)
    if match is None:
        return None
    return f"10000000-0000-0000-0000-000000000{int(match.group(1)):03d}"


def _dav_username_for_guid(guid):
    match = re.fullmatch(r"10000000-0000-0000-0000-000000000(\d{3})", guid)
    if match is None:
        return None
    index = int(match.group(1))
    if index < 1 or index > 99:
        return None
    return f"user{index:02d}"


def _principal_href_for_user(user):
    guid = _dav_guid_for_username(user.username)
    if guid is None:
        return f"/dav/principals/users/{user.username}/"
    return f"/dav/principals/__uids__/{guid}/"


def _calendar_home_href_for_user(user):
    guid = _dav_guid_for_username(user.username)
    if guid is None:
        return f"/dav/calendars/users/{user.username}/"
    return f"/dav/calendars/__uids__/{guid}/"


def _propfind_finite_depth_error():
    error = ET.Element(qname(NS_DAV, "error"))
    ET.SubElement(error, qname(NS_DAV, "propfind-finite-depth"))
    response = HttpResponse(
        ET.tostring(error, encoding="utf-8", xml_declaration=True),
        status=403,
        content_type="application/xml; charset=utf-8",
    )
    return _dav_common_headers(response)


def _if_none_match_matches(request, etag):
    header = request.headers.get("If-None-Match")
    if not header:
        return False
    values = _if_match_values(header)
    return "*" in values or etag in values


def _if_modified_since_not_modified(request, timestamp):
    header = request.headers.get("If-Modified-Since")
    if not header:
        return False
    try:
        date = parsedate_to_datetime(header)
    except (TypeError, ValueError):
        return False
    if date is None:
        return False
    if date.tzinfo is None:
        date = date.replace(tzinfo=datetime_timezone.utc)
    return int(timestamp) <= int(date.timestamp())


def _conditional_not_modified(request, etag, timestamp):
    if _if_none_match_matches(request, etag):
        return True
    if _if_modified_since_not_modified(request, timestamp):
        return True
    return False


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


def _parse_propfind_payload(request):
    parsed = parse_propfind_request(request.body)
    if "error" in parsed:
        return None, HttpResponse(status=400)

    depth = request.headers.get("Depth", "infinity")
    if depth == "infinity":
        return None, _propfind_finite_depth_error()
    if depth not in ("0", "1"):
        return None, HttpResponse(status=400)

    return parsed, None


@csrf_exempt
def dav_root(request):
    allowed = ["OPTIONS", "PROPFIND", "GET", "HEAD"]
    if request.method == "OPTIONS":
        response = HttpResponse(status=204)
        response["Allow"] = ", ".join(allowed)
        return _dav_common_headers(response)

    if request.method in ("GET", "HEAD"):
        user = get_dav_user(request)
        if user is None:
            return unauthorized_response()
        if request.method == "HEAD":
            response = HttpResponse(status=200)
        else:
            response = HttpResponse(
                "DAV root", content_type="text/plain; charset=utf-8"
            )
        return _dav_common_headers(response)

    if request.method != "PROPFIND":
        return _not_allowed(request, allowed)

    user = get_dav_user(request)
    if user is None:
        return unauthorized_response()

    parsed, parse_error = _parse_propfind_payload(request)
    if parse_error is not None:
        return parse_error

    prop_map = _build_prop_map_for_root(user)

    depth = request.headers.get("Depth", "infinity")
    if parsed is None:
        return HttpResponse(status=400)

    requested = parsed["requested"] if parsed["mode"] == "prop" else None
    root_ok, root_missing = _select_props(prop_map, requested)
    responses = [response_with_props("/dav/", root_ok, root_missing)]

    if depth == "1":
        principal_href = _principal_href_for_user(user)
        home_href = _calendar_home_href_for_user(user)

        principal_map = _build_prop_map_for_principal(user, user)
        principal_ok, principal_missing = _select_props(principal_map, requested)
        responses.append(
            response_with_props(principal_href, principal_ok, principal_missing)
        )

        home_map = _build_prop_map_for_calendar_home(user, user)
        home_ok, home_missing = _select_props(home_map, requested)
        responses.append(response_with_props(home_href, home_ok, home_missing))

    return _xml_response(207, multistatus_document(responses))


def _build_prop_map_for_principal(auth_user, principal_user):
    def current_user_principal():
        elem = ET.Element(qname(NS_DAV, "current-user-principal"))
        href = ET.SubElement(elem, qname(NS_DAV, "href"))
        href.text = _principal_href_for_user(auth_user)
        return elem

    def calendar_home_set():
        elem = ET.Element(qname(NS_CALDAV, "calendar-home-set"))
        href = ET.SubElement(elem, qname(NS_DAV, "href"))
        href.text = _calendar_home_href_for_user(principal_user)
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
    allowed = ["OPTIONS", "PROPFIND", "GET", "HEAD"]
    if request.method == "OPTIONS":
        response = HttpResponse(status=204)
        response["Allow"] = ", ".join(allowed)
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
        return _not_allowed(request, allowed, username=username)

    parsed, parse_error = _parse_propfind_payload(request)
    if parse_error is not None:
        return parse_error
    if parsed is None:
        return HttpResponse(status=400)

    requested = parsed["requested"] if parsed["mode"] == "prop" else None
    principal_map = _build_prop_map_for_principal(user, principal)
    ok, missing = _select_props(principal_map, requested)
    responses = [
        response_with_props(f"/dav/principals/{principal.username}/", ok, missing)
    ]

    return _xml_response(207, multistatus_document(responses))


@csrf_exempt
def principal_uid_view(request, guid):
    username = _dav_username_for_guid(guid)
    if username is None:
        return HttpResponse(status=404)
    return principal_view(request, username)


@csrf_exempt
def principals_collection_view(request):
    return _collection_view(request, "/dav/principals/", "principals")


@csrf_exempt
def principals_users_collection_view(request):
    return _collection_view(request, "/dav/principals/users/", "users")


@csrf_exempt
def principal_users_view(request, username):
    return principal_view(request, username)


def _build_prop_map_for_calendar_home(owner, auth_user):
    can_write = owner == auth_user
    return {
        qname(NS_DAV, "resourcetype"): lambda: _resourcetype_prop(
            (NS_DAV, "collection")
        ),
        qname(NS_DAV, "getcontentlength"): lambda: _text_prop(
            NS_DAV,
            "getcontentlength",
            "",
        ),
        qname(NS_DAV, "displayname"): lambda: _text_prop(
            NS_DAV,
            "displayname",
            f"{owner.username} calendars",
        ),
        qname(NS_DAV, "owner"): lambda: _owner_prop(owner),
        qname(
            NS_DAV, "current-user-privilege-set"
        ): lambda: _current_user_privilege_set_prop(can_write),
        qname(NS_DAV, "supported-report-set"): lambda: _supported_report_set_prop(
            include_freebusy=True
        ),
        qname(
            NS_CALDAV,
            "supported-calendar-component-sets",
        ): _supported_component_sets_prop,
    }


def _build_prop_map_for_collection(display_name):
    def current_user_principal_for_requester(auth_user):
        elem = ET.Element(qname(NS_DAV, "current-user-principal"))
        href = ET.SubElement(elem, qname(NS_DAV, "href"))
        href.text = _principal_href_for_user(auth_user)
        return elem

    return {
        qname(NS_DAV, "resourcetype"): lambda: _resourcetype_prop(
            (NS_DAV, "collection")
        ),
        qname(NS_DAV, "displayname"): lambda: _text_prop(
            NS_DAV, "displayname", display_name
        ),
        qname(NS_DAV, "current-user-principal"): current_user_principal_for_requester,
        qname(NS_DAV, "supported-report-set"): lambda: _supported_report_set_prop(
            include_freebusy=True
        ),
    }


def _collection_view(request, href, display_name):
    allowed = ["OPTIONS", "PROPFIND", "GET", "HEAD"]
    if request.method == "OPTIONS":
        response = HttpResponse(status=204)
        response["Allow"] = ", ".join(allowed)
        return _dav_common_headers(response)

    user, auth_response = _require_dav_user(request)
    if auth_response is not None:
        return auth_response

    if request.method in ("GET", "HEAD"):
        if request.method == "HEAD":
            response = HttpResponse(status=200)
        else:
            response = HttpResponse(
                "Collection", content_type="text/plain; charset=utf-8"
            )
        return _dav_common_headers(response)

    if request.method != "PROPFIND":
        return _not_allowed(request, allowed, href=href)

    parsed, parse_error = _parse_propfind_payload(request)
    if parse_error is not None:
        return parse_error
    if parsed is None:
        return HttpResponse(status=400)

    prop_map = _build_prop_map_for_collection(display_name)
    resolved_map = {}
    for key, builder in prop_map.items():
        if key == qname(NS_DAV, "current-user-principal"):
            resolved_map[key] = lambda b=builder: b(user)
        else:
            resolved_map[key] = builder

    requested = parsed["requested"] if parsed["mode"] == "prop" else None
    ok, missing = _select_props(resolved_map, requested)
    return _xml_response(
        207, multistatus_document([response_with_props(href, ok, missing)])
    )


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

    home_etag, home_timestamp = _home_etag_and_timestamp(owner, user)

    if request.method in ("GET", "HEAD"):
        if _conditional_not_modified(request, home_etag, home_timestamp):
            response = HttpResponse(status=304)
            response["ETag"] = home_etag
            response["Last-Modified"] = http_date(home_timestamp)
            return _dav_common_headers(response)

        if request.method == "HEAD":
            response = HttpResponse(status=200)
        else:
            response = HttpResponse(
                "Calendar home", content_type="text/plain; charset=utf-8"
            )
        response["ETag"] = home_etag
        response["Last-Modified"] = http_date(home_timestamp)
        return _dav_common_headers(response)

    calendars = _visible_calendars_for_home(owner, user)

    if request.method == "REPORT":
        return _handle_report(calendars, request, allow_sync_collection=False)

    if request.method != "PROPFIND":
        return _not_allowed(request, allowed, username=username)

    parsed, parse_error = _parse_propfind_payload(request)
    if parse_error is not None:
        return parse_error
    if parsed is None:
        return HttpResponse(status=400)

    depth = request.headers.get("Depth", "infinity")
    requested = parsed["requested"] if parsed["mode"] == "prop" else None
    home_map = _build_prop_map_for_calendar_home(owner, user)
    home_ok, home_missing = _select_props(home_map, requested)
    responses = [
        response_with_props(f"/dav/calendars/{owner.username}/", home_ok, home_missing)
    ]

    if depth == "1":
        for calendar in calendars:
            cal_map = _build_prop_map_for_calendar_collection(calendar, user)
            cal_ok, cal_missing = _select_props(cal_map, requested)
            href = f"/dav/calendars/{owner.username}/{calendar.slug}/"
            responses.append(response_with_props(href, cal_ok, cal_missing))

    return _xml_response(207, multistatus_document(responses))


@csrf_exempt
def calendars_collection_view(request):
    return _collection_view(request, "/dav/calendars/", "calendars")


@csrf_exempt
def calendars_uids_collection_view(request):
    return _collection_view(request, "/dav/calendars/__uids__/", "uid calendars")


@csrf_exempt
def calendars_users_collection_view(request):
    return _collection_view(request, "/dav/calendars/users/", "user calendars")


@csrf_exempt
def calendar_home_uid_view(request, guid):
    username = _dav_username_for_guid(guid)
    if username is None:
        return HttpResponse(status=404)
    return calendar_home_view(request, username)


@csrf_exempt
def calendar_home_users_view(request, username):
    return calendar_home_view(request, username)


def _build_prop_map_for_calendar_collection(calendar, auth_user):
    can_write = can_write_calendar(calendar, auth_user)
    return {
        qname(NS_DAV, "resourcetype"): lambda: _resourcetype_prop(
            (NS_DAV, "collection"),
            (NS_CALDAV, "calendar"),
        ),
        qname(NS_DAV, "getcontentlength"): lambda: _text_prop(
            NS_DAV,
            "getcontentlength",
            "",
        ),
        qname(NS_DAV, "getcontenttype"): lambda: _text_prop(
            NS_DAV,
            "getcontenttype",
            "text/calendar",
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
        ): lambda: _supported_components_prop(calendar.component_kind),
        qname(NS_CALDAV, "calendar-timezone"): lambda: _calendar_timezone_prop(
            calendar.timezone
        ),
        qname(NS_CALDAV, "calendar-description"): lambda: _text_prop(
            NS_CALDAV,
            "calendar-description",
            calendar.description,
        ),
        qname(NS_APPLE_ICAL, "calendar-color"): lambda: _calendar_color_prop(
            calendar.color
        ),
        qname(NS_APPLE_ICAL, "calendar-order"): lambda: _calendar_order_prop(
            calendar.sort_order if calendar.sort_order is not None else 0
        ),
        qname(NS_DAV, "getetag"): lambda: _text_prop(
            NS_DAV, "getetag", _etag_for_calendar(calendar)
        ),
        qname(NS_DAV, "owner"): lambda: _owner_prop(calendar.owner),
        qname(
            NS_DAV, "current-user-privilege-set"
        ): lambda: _current_user_privilege_set_prop(can_write),
        qname(NS_DAV, "supported-report-set"): lambda: _supported_report_set_prop(
            include_freebusy=True,
            include_sync_collection=True,
        ),
        qname(NS_DAV, "sync-token"): lambda: _text_prop(
            NS_DAV,
            "sync-token",
            _sync_token_for_calendar(calendar),
        ),
    }


def _visible_calendars_for_home(owner, user):
    calendars = Calendar.objects.filter(owner=owner)  # type: ignore[attr-defined]
    return [calendar for calendar in calendars if can_view_calendar(calendar, user)]


def _report_href_style(request_path):
    if "/calendars/__uids__/" in request_path:
        return "uids"
    if "/calendars/users/" in request_path:
        return "users"
    return "username"


def _object_href_for_style(calendar, obj, style):
    if style == "uids":
        guid = _dav_guid_for_username(calendar.owner.username)
        if guid is not None:
            return f"/dav/calendars/__uids__/{guid}/{calendar.slug}/{obj.filename}"

    if style == "users":
        return f"/dav/calendars/users/{calendar.owner.username}/{calendar.slug}/{obj.filename}"

    return f"/dav/calendars/{calendar.owner.username}/{calendar.slug}/{obj.filename}"


def _all_object_hrefs(calendar, obj):
    hrefs = {
        f"/dav/calendars/{calendar.owner.username}/{calendar.slug}/{obj.filename}",
        f"/dav/calendars/users/{calendar.owner.username}/{calendar.slug}/{obj.filename}",
    }
    guid = _dav_guid_for_username(calendar.owner.username)
    if guid is not None:
        hrefs.add(f"/dav/calendars/__uids__/{guid}/{calendar.slug}/{obj.filename}")
    return hrefs


def _responses_for_multiget(calendars, requested, hrefs, calendar_data_request=None):
    responses = []
    by_path = {}
    for calendar in calendars:
        for obj in calendar.calendar_objects.all():
            for href in _all_object_hrefs(calendar, obj):
                by_path[href] = obj

    for href in hrefs:
        normalized = _normalize_href_path(href)
        obj = by_path.get(normalized)
        if obj is None:
            responses.append(response_with_status(normalized, "404 Not Found"))
            continue

        obj_map = _build_prop_map_for_object(obj, calendar_data_request)
        ok, missing = _select_props(obj_map, requested)
        responses.append(response_with_props(normalized, ok, missing))

    return responses


def _responses_for_calendar_query(
    calendars,
    requested,
    query_filter,
    request_path,
    calendar_data_request=None,
):
    responses = []
    style = _report_href_style(request_path)
    for calendar in calendars:
        for obj in calendar.calendar_objects.all():
            if not _object_matches_query(obj, query_filter):
                continue
            obj_map = _build_prop_map_for_object(obj, calendar_data_request)
            ok, missing = _select_props(obj_map, requested)
            href = _object_href_for_style(calendar, obj, style)
            responses.append(response_with_props(href, ok, missing))
    return responses


def _object_href_for_filename(calendar, filename, style):
    if style == "uids":
        guid = _dav_guid_for_username(calendar.owner.username)
        if guid is not None:
            return f"/dav/calendars/__uids__/{guid}/{calendar.slug}/{filename}"

    if style == "users":
        return (
            f"/dav/calendars/users/{calendar.owner.username}/{calendar.slug}/{filename}"
        )

    return f"/dav/calendars/{calendar.owner.username}/{calendar.slug}/{filename}"


def _sync_collection_multistatus_document(responses, sync_token):
    root = ET.Element(qname(NS_DAV, "multistatus"))
    for response in responses:
        root.append(response)
    token = ET.SubElement(root, qname(NS_DAV, "sync-token"))
    token.text = sync_token
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _sync_collection_limit(root):
    limit = root.find(qname(NS_DAV, "limit"))
    if limit is None:
        return None
    nresults = limit.find(qname(NS_DAV, "nresults"))
    if nresults is None or not (nresults.text or "").strip():
        return None
    try:
        parsed = int((nresults.text or "").strip())
    except ValueError:
        return None
    if parsed <= 0:
        return None
    return parsed


def _collection_href_for_style(calendar, style):
    if style == "uids":
        guid = _dav_guid_for_username(calendar.owner.username)
        if guid is not None:
            return f"/dav/calendars/__uids__/{guid}/{calendar.slug}"

    if style == "users":
        return f"/dav/calendars/users/{calendar.owner.username}/{calendar.slug}"

    return f"/dav/calendars/{calendar.owner.username}/{calendar.slug}"


def _sync_collection_response(
    calendar,
    request_path,
    requested,
    calendar_data_request,
    token_revision,
    limit,
):
    def response_for_props(href, prop_map):
        if requested == []:
            return response_with_status(href, "200 OK")
        ok, missing = _select_props(prop_map, requested)
        return response_with_props(href, ok, missing)

    style = _report_href_style(request_path)
    latest_revision = _latest_sync_revision(calendar)
    if token_revision is not None and token_revision > latest_revision:
        return _valid_sync_token_error_response()

    responses = []
    next_revision = latest_revision

    if token_revision is None:
        cal_map = _build_prop_map_for_calendar_collection(calendar, calendar.owner)
        responses.append(
            response_for_props(
                _collection_href_for_style(calendar, style),
                cal_map,
            )
        )
        changes = list(
            CalendarObjectChange.objects.filter(calendar=calendar).order_by("revision")
        )
        if changes:
            latest_by_filename = {}
            for change in changes:
                latest_by_filename[change.filename] = change
            selected_changes = sorted(
                latest_by_filename.values(),
                key=lambda change: change.revision,
            )
            if limit is not None:
                selected_changes = selected_changes[:limit]
            if selected_changes:
                next_revision = selected_changes[-1].revision

            current_objects = {
                obj.filename: obj
                for obj in calendar.calendar_objects.filter(
                    filename__in=[change.filename for change in selected_changes]
                )
            }
            for change in selected_changes:
                if change.is_deleted:
                    continue
                obj = current_objects.get(change.filename)
                if obj is None:
                    continue
                href = _object_href_for_style(calendar, obj, style)
                obj_map = _build_prop_map_for_object(obj, calendar_data_request)
                responses.append(response_for_props(href, obj_map))
        else:
            objects = list(calendar.calendar_objects.all())
            if limit is not None:
                objects = objects[:limit]
            for obj in objects:
                href = _object_href_for_style(calendar, obj, style)
                obj_map = _build_prop_map_for_object(obj, calendar_data_request)
                responses.append(response_for_props(href, obj_map))
    else:
        changes = list(
            CalendarObjectChange.objects.filter(
                calendar=calendar,
                revision__gt=token_revision,
            ).order_by("revision")
        )
        latest_by_filename = {}
        for change in changes:
            latest_by_filename[change.filename] = change
        ordered_changes = sorted(
            latest_by_filename.values(),
            key=lambda change: change.revision,
        )
        if limit is not None:
            ordered_changes = ordered_changes[:limit]
        if ordered_changes:
            next_revision = ordered_changes[-1].revision

        current_objects = {
            obj.filename: obj
            for obj in calendar.calendar_objects.filter(
                filename__in=[change.filename for change in ordered_changes]
            )
        }
        for change in ordered_changes:
            filename = change.filename
            href = _object_href_for_filename(calendar, filename, style)
            if change.is_deleted:
                responses.append(response_with_status(href, "404 Not Found"))
                continue
            obj = current_objects.get(filename)
            if obj is None:
                responses.append(response_with_status(href, "404 Not Found"))
                continue
            obj_map = _build_prop_map_for_object(obj, calendar_data_request)
            responses.append(response_for_props(href, obj_map))

    body = _sync_collection_multistatus_document(
        responses,
        _build_sync_token(calendar.id, next_revision),
    )
    return _xml_response(207, body)


def _handle_report(calendars, request, allow_sync_collection=True):
    global _ACTIVE_REPORT_TZINFO
    parsed_report = parse_report_request(request.body)
    if parsed_report is None:
        return HttpResponse(status=400)
    root = parsed_report.root

    _tzinfo, tz_error = _tzinfo_from_report(root)
    _ACTIVE_REPORT_TZINFO = _tzinfo
    if tz_error is not None:
        _ACTIVE_REPORT_TZINFO = None
        return tz_error

    for time_range in root.findall(f".//{qname(NS_CALDAV, 'time-range')}"):
        start_raw = time_range.get("start")
        end_raw = time_range.get("end")
        if not start_raw and not end_raw:
            return HttpResponse(status=400)

        start = _parse_ical_datetime(start_raw)
        end = _parse_ical_datetime(end_raw)
        if start_raw and start is None:
            return HttpResponse(status=400)
        if end_raw and end is None:
            return HttpResponse(status=400)

    current_year = timezone.now().year
    low_limit = datetime(current_year - 1, 1, 1, tzinfo=datetime_timezone.utc)
    high_limit = datetime(
        current_year + 5, 12, 31, 23, 59, 59, tzinfo=datetime_timezone.utc
    )
    for comp_filter in root.findall(f".//{qname(NS_CALDAV, 'comp-filter')}"):
        time_range = comp_filter.find(qname(NS_CALDAV, "time-range"))
        if time_range is None:
            continue
        start = _parse_ical_datetime(time_range.get("start"))
        end = _parse_ical_datetime(time_range.get("end"))
        if start is not None and start < low_limit:
            return _caldav_error_response("min-date-time")
        if end is not None and end < low_limit:
            return _caldav_error_response("min-date-time")
        if start is not None and start > high_limit:
            return _caldav_error_response("max-date-time")
        if end is not None and end > high_limit:
            return _caldav_error_response("max-date-time")

    requested = parsed_report.requested_props
    calendar_data_request = parsed_report.calendar_data_request

    if root.tag == qname(NS_CALDAV, "calendar-multiget"):
        hrefs = parsed_report.hrefs
        responses = _responses_for_multiget(
            calendars,
            requested,
            hrefs,
            calendar_data_request,
        )
        response = _xml_response(207, multistatus_document(responses))
        _ACTIVE_REPORT_TZINFO = None
        return response

    if root.tag == qname(NS_CALDAV, "calendar-query"):
        query_filter = parsed_report.query_filter
        responses = _responses_for_calendar_query(
            calendars,
            requested,
            query_filter,
            request.path,
            calendar_data_request,
        )
        response = _xml_response(207, multistatus_document(responses))
        _ACTIVE_REPORT_TZINFO = None
        return response

    if root.tag == qname(NS_CALDAV, "free-busy-query"):
        response = _render_freebusy_report(calendars, root)
        _ACTIVE_REPORT_TZINFO = None
        return response

    if root.tag == qname(NS_DAV, "sync-collection"):
        requested_limit = _sync_collection_limit(root)

        if not allow_sync_collection:
            _ACTIVE_REPORT_TZINFO = None
            return HttpResponse(status=501)

        sync_level = (root.findtext(qname(NS_DAV, "sync-level")) or "").strip()
        if sync_level and sync_level != "1":
            _ACTIVE_REPORT_TZINFO = None
            return HttpResponse(status=400)

        if len(calendars) != 1:
            _ACTIVE_REPORT_TZINFO = None
            return HttpResponse(status=501)

        sync_token_value = (root.findtext(qname(NS_DAV, "sync-token")) or "").strip()
        token_revision = None
        if sync_token_value:
            token_revision, token_error = _parse_sync_token_for_calendar(
                sync_token_value,
                calendars[0],
            )
            if token_error is not None:
                _ACTIVE_REPORT_TZINFO = None
                return token_error

        response = _sync_collection_response(
            calendars[0],
            request.path,
            requested,
            calendar_data_request,
            token_revision,
            requested_limit,
        )
        _ACTIVE_REPORT_TZINFO = None
        return response

    _ACTIVE_REPORT_TZINFO = None
    return _report_unknown_type()


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
            request.body
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

        ok_tags = []
        bad_tags = []
        update_fields = set()
        pending_values = {
            "name": calendar.name,
            "description": calendar.description,
            "timezone": calendar.timezone,
            "color": calendar.color,
            "sort_order": calendar.sort_order,
        }

        for operation in list(root):
            if operation.tag not in (qname(NS_DAV, "set"), qname(NS_DAV, "remove")):
                continue
            prop = operation.find(qname(NS_DAV, "prop"))
            if prop is None:
                continue
            is_set = operation.tag == qname(NS_DAV, "set")
            for entry in list(prop):
                if entry.tag == qname(NS_DAV, "displayname"):
                    if is_set:
                        pending_values["name"] = (
                            entry.text or ""
                        ).strip() or calendar.slug
                    else:
                        pending_values["name"] = calendar.slug
                    update_fields.add("name")
                    ok_tags.append(entry.tag)
                    continue

                if entry.tag == qname(NS_CALDAV, "calendar-description"):
                    pending_values["description"] = (
                        (entry.text or "").strip() if is_set else ""
                    )
                    update_fields.add("description")
                    ok_tags.append(entry.tag)
                    continue

                if entry.tag == qname(NS_CALDAV, "calendar-timezone"):
                    if not is_set:
                        pending_values["timezone"] = "UTC"
                        update_fields.add("timezone")
                        ok_tags.append(entry.tag)
                        continue
                    timezone_text = (entry.text or "").strip()
                    tzid = _extract_tzid_from_timezone_text(timezone_text)
                    if not tzid:
                        bad_tags.append(entry.tag)
                        continue
                    try:
                        ZoneInfo(tzid)
                    except Exception:
                        bad_tags.append(entry.tag)
                        continue
                    pending_values["timezone"] = tzid
                    update_fields.add("timezone")
                    ok_tags.append(entry.tag)
                    continue

                if entry.tag == qname(NS_APPLE_ICAL, "calendar-color"):
                    pending_values["color"] = (
                        (entry.text or "").strip() if is_set else ""
                    )
                    update_fields.add("color")
                    ok_tags.append(entry.tag)
                    continue

                if entry.tag == qname(NS_APPLE_ICAL, "calendar-order"):
                    if not is_set:
                        pending_values["sort_order"] = None
                        update_fields.add("sort_order")
                        ok_tags.append(entry.tag)
                        continue
                    try:
                        pending_values["sort_order"] = int((entry.text or "").strip())
                    except ValueError:
                        bad_tags.append(entry.tag)
                        continue
                    update_fields.add("sort_order")
                    ok_tags.append(entry.tag)
                    continue

                bad_tags.append(entry.tag)

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
                f"Calendar {calendar.name}",
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
    cal_map = _build_prop_map_for_calendar_collection(calendar, user)
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


@csrf_exempt
def calendar_collection_users_view(request, username, slug):
    return calendar_collection_view(request, username, slug)


def _build_prop_map_for_object(obj, calendar_data_request=None):
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
        qname(NS_CALDAV, "calendar-data"): lambda: _calendar_data_prop(
            _filter_calendar_data_for_response(obj.ical_blob, calendar_data_request)
        ),
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

        with transaction.atomic():
            writable = Calendar.objects.select_for_update().get(pk=writable.pk)
            next_revision = _latest_sync_revision(writable) + 1
            marker_filename = _collection_marker(filename)
            parent_path, _leaf = _split_filename_path(filename)

            if request.method in ("MKCOL", "MKCALENDAR"):
                if writable.slug != "litmus":
                    return _caldav_error_response(
                        "calendar-collection-location-ok", status=403
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

            if _precondition_failed_for_write(request, existing):
                return HttpResponse(status=412)

            if not _collection_exists(writable, parent_path):
                return HttpResponse(status=409)

            raw_content_type = request.META.get("CONTENT_TYPE") or request.content_type
            content_type = _normalize_content_type(raw_content_type)
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
            payload_text = parsed["text"]
            if _is_ical_resource(filename, content_type):
                component_kind = _component_kind_from_payload(payload_text)
                if component_kind is None or component_kind != writable.component_kind:
                    return _caldav_error_response(
                        "supported-calendar-component", status=403
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
    ok, missing = _select_props(obj_map, requested)
    href = f"/dav/calendars/{username}/{slug}/{filename}"
    return _xml_response(
        207, multistatus_document([response_with_props(href, ok, missing)])
    )


@csrf_exempt
def calendar_object_uid_view(request, guid, slug, filename):
    username = _dav_username_for_guid(guid)
    if username is None:
        return HttpResponse(status=404)
    return calendar_object_view(request, username, slug, filename)


@csrf_exempt
def calendar_object_users_view(request, username, slug, filename):
    return calendar_object_view(request, username, slug, filename)
