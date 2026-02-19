# pyright: reportGeneralTypeIssues=false, reportAttributeAccessIssue=false

import hashlib
import logging
import re
from datetime import datetime, timezone as datetime_timezone
from urllib.parse import quote, urlparse
from uuid import UUID
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

from django.http import HttpResponse, HttpResponseNotAllowed
from django.shortcuts import redirect
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.http import http_date

from calendars.models import Calendar, CalendarObjectChange
from calendars.permissions import can_view_calendar

from .core import filters as core_filters
from .core import calendar_data as core_calendar_data
from .core import davxml as core_davxml
from .core import freebusy as core_freebusy
from .core import paths as core_paths
from .core import payloads as core_payloads
from .core import propmap as core_propmap
from .core import props as core_props
from .core import query as core_query
from .core import recurrence as core_recurrence
from .core import report as core_report
from .core import report_dispatch as core_report_dispatch
from .core import sync as core_sync
from .core import time as core_time
from .core import write_ops as core_write_ops
from .shell import repository as shell_repository
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


def _collection_exists(calendar, path):
    marker = core_paths.collection_marker(path)
    if not marker:
        return True
    return calendar.calendar_objects.filter(filename=marker).exists()


def _copy_or_move_calendar_object(
    writable,
    request,
    username,
    slug,
    filename,
    next_revision,
    is_move,
):
    source = writable.calendar_objects.filter(filename=filename).first()
    if source is None and filename.endswith("/"):
        source = writable.calendar_objects.filter(
            filename=core_paths.collection_marker(filename)
        ).first()
    if source is None:
        return HttpResponse(status=404)

    destination = core_paths.destination_filename_from_header(
        request.headers.get("Destination"),
        username,
        slug,
    )
    if destination is None:
        return HttpResponse(status=400)

    source_is_collection = source.filename.endswith("/")
    source_marker = source.filename if source_is_collection else None
    destination_clean = destination.strip("/")
    if not destination_clean:
        return HttpResponse(status=403)

    if source_is_collection:
        destination_marker = core_paths.collection_marker(destination)
        destination_lookup = destination_marker
    else:
        destination_marker = None
        destination_lookup = destination_clean

    if source.filename == destination_lookup:
        return HttpResponse(status=204)

    destination_parent, _ = core_paths.split_filename_path(destination_lookup)
    if not _collection_exists(writable, destination_parent):
        return HttpResponse(status=409)

    overwrite = request.headers.get("Overwrite", "T").strip().upper() != "F"

    if source_is_collection:
        destination_entries_qs = writable.calendar_objects.filter(
            filename__startswith=destination_marker
        )
        destination_entries = list(destination_entries_qs.values("filename", "uid"))
    else:
        destination_obj = writable.calendar_objects.filter(
            filename=destination_lookup
        ).first()
        destination_entries = []
        if destination_obj is not None:
            destination_entries.append(
                {"filename": destination_obj.filename, "uid": destination_obj.uid}
            )

    if destination_entries and not overwrite:
        return HttpResponse(status=412)

    if source_is_collection:
        copy_depth = (request.headers.get("Depth") or "infinity").strip().lower()
        if not is_move and copy_depth == "0":
            source_entries = [source]
        else:
            source_entries = list(
                writable.calendar_objects.filter(filename__startswith=source_marker)
            )
    else:
        source_entries = [source]

    now = timezone.now()

    if destination_entries and overwrite:
        if source_is_collection:
            writable.calendar_objects.filter(
                filename__startswith=destination_marker
            ).delete()
        else:
            writable.calendar_objects.filter(filename=destination_lookup).delete()
        for item in destination_entries:
            _create_calendar_change(
                writable,
                next_revision,
                item["filename"],
                item["uid"],
                True,
            )
            next_revision += 1

    copied_filenames = []
    marker_value = source_marker or ""
    for entry in source_entries:
        if source_is_collection:
            suffix = entry.filename[len(marker_value) :]
            target_filename = f"{destination_marker}{suffix}"
        else:
            target_filename = destination_lookup

        target_uid = entry.uid
        if entry.uid.startswith("collection:"):
            target_uid = f"collection:{target_filename}"
        elif entry.uid.startswith("dav:"):
            target_uid = f"dav:{target_filename}"

        writable.calendar_objects.create(
            uid=target_uid,
            filename=target_filename,
            etag=entry.etag,
            ical_blob=entry.ical_blob,
            content_type=entry.content_type,
            size=entry.size,
            dead_properties=(entry.dead_properties or {}).copy(),
            updated_at=now,
        )
        _create_calendar_change(
            writable,
            next_revision,
            target_filename,
            target_uid,
            False,
        )
        next_revision += 1
        copied_filenames.append(target_filename)

    if is_move:
        for entry in source_entries:
            _create_calendar_change(
                writable,
                next_revision,
                entry.filename,
                entry.uid,
                True,
            )
            next_revision += 1
        if source_is_collection:
            writable.calendar_objects.filter(
                filename__startswith=source_marker
            ).delete()
        else:
            source.delete()

    writable.updated_at = now
    writable.save(update_fields=["updated_at"])

    status_code = 204 if destination_entries else 201
    response = HttpResponse(status=status_code)
    if copied_filenames:
        escaped_filename = quote(copied_filenames[0], safe="/")
        response["Location"] = f"/dav/calendars/{username}/{slug}/{escaped_filename}"
    return _dav_common_headers(response)


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


def _calendar_default_tzinfo(calendar):
    tz_name = (getattr(calendar, "timezone", "") or "").strip()
    if not tz_name:
        return datetime_timezone.utc
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return datetime_timezone.utc


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
        rec_text, rec_is_date = core_time.format_value_date_or_datetime(rec_id, tzinfo)
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
        dtstart_text, dtstart_is_date = core_time.format_value_date_or_datetime(
            dtstart,
            tzinfo,
        )
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
        rec_text, rec_is_date = core_time.format_value_date_or_datetime(rec_id, tzinfo)
        uid_key = str(uid or "")
        if rec_text is None and master_starts is not None and dtstart_text:
            master_start = master_starts.get(uid_key)
            master_text, _ = core_time.format_value_date_or_datetime(
                master_start, tzinfo
            )
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
        dtend_text, dtend_is_date = core_time.format_value_date_or_datetime(
            dtend, tzinfo
        )
        if dtend_text:
            if dtend_is_date:
                lines.append(f"DTEND;VALUE=DATE:{dtend_text}")
            else:
                lines.append(f"DTEND:{dtend_text}")

        due = component.decoded("DUE", None)
        due_text, due_is_date = core_time.format_value_date_or_datetime(due, tzinfo)
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
        duration_text = core_time.format_ical_duration(duration)
        if duration_text:
            lines.append(f"DURATION:{duration_text}")

        summary = component.get("SUMMARY")
        if summary:
            lines.append(f"SUMMARY:{summary}")

        lines.append(f"END:{name}")

    lines.extend(["END:VCALENDAR", ""])
    return "\r\n".join(lines)


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
        tzid = core_payloads.extract_tzid_from_timezone_text(
            (timezone_elem.text or "").strip()
        )
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

    tzid = core_payloads.extract_tzid_from_timezone_text(timezone_text)
    if not tzid:
        return None, _caldav_error_response("valid-calendar-data")
    try:
        return ZoneInfo(tzid), None
    except Exception:
        return None, _caldav_error_response("valid-calendar-data")


def _object_matches_query_with_active_tz(obj, query_filter):
    def parse_line_datetime_with_tz(line):
        return core_recurrence.parse_line_datetime_with_tz(
            line,
            active_report_tzinfo=_ACTIVE_REPORT_TZINFO,
        )

    def line_matches_time_range(line, time_range):
        return core_recurrence.line_matches_time_range(
            line,
            time_range,
            active_report_tzinfo=_ACTIVE_REPORT_TZINFO,
        )

    def matches_prop_filter(component_text, prop_filter):
        return core_filters.matches_prop_filter(
            component_text,
            prop_filter,
            line_matches_time_range,
        )

    def matches_time_range_recurrence(component_text, start, end, component_name):
        return core_recurrence.matches_time_range_recurrence(
            component_text,
            start,
            end,
            component_name,
            active_report_tzinfo=_ACTIVE_REPORT_TZINFO,
        )

    def matches_time_range(component_text, time_range):
        return core_query.matches_time_range(
            component_text,
            time_range,
            core_time.parse_ical_datetime,
            matches_time_range_recurrence,
            parse_line_datetime_with_tz,
            core_time.first_ical_line,
            core_time.parse_ical_duration,
            core_time.first_ical_line_value,
        )

    def alarm_matches_time_range(component_text, time_range):
        return core_recurrence.alarm_matches_time_range(
            component_text,
            time_range,
            active_report_tzinfo=_ACTIVE_REPORT_TZINFO,
        )

    def matches_comp_filter(context_text, comp_filter):
        return core_query.matches_comp_filter(
            context_text,
            comp_filter,
            core_recurrence.extract_component_blocks,
            matches_time_range,
            matches_prop_filter,
            alarm_matches_time_range,
            core_filters.combine_filter_results,
        )

    return core_query.object_matches_query(
        obj.ical_blob,
        query_filter,
        core_time.unfold_ical,
        matches_comp_filter,
    )


def _calendar_data_prop(ical_blob):
    elem = ET.Element(qname(NS_CALDAV, "calendar-data"))
    elem.text = ical_blob
    return elem


def _filter_calendar_data_with_active_tz(ical_blob, calendar_data_request):
    def ensure_shifted_recurrence_id(ical_text, master_starts, tzinfo):
        return core_calendar_data.ensure_shifted_first_occurrence_recurrence_id(
            ical_text,
            master_starts,
            tzinfo,
            core_recurrence.extract_component_blocks,
            core_time.first_ical_line_value,
            core_time.first_ical_line,
            core_time.format_value_date_or_datetime,
        )

    return core_calendar_data.filter_calendar_data_for_response(
        ical_blob,
        calendar_data_request,
        _ACTIVE_REPORT_TZINFO,
        core_time.parse_ical_datetime,
        core_time.as_utc_datetime,
        _serialize_expanded_components,
        ensure_shifted_recurrence_id,
    )


def _report_unknown_type():
    return HttpResponse(status=501)


def _render_freebusy_report(calendars, root):
    time_range = root.find(qname(NS_CALDAV, "time-range"))
    if time_range is None:
        return HttpResponse(status=400)

    start = core_time.parse_ical_datetime(time_range.get("start"))
    end = core_time.parse_ical_datetime(time_range.get("end"))
    if start is None or end is None:
        return HttpResponse(status=400)
    window_start = core_time.as_utc_datetime(start)
    window_end = core_time.as_utc_datetime(end)

    busy = []
    tentative = []
    unavailable = []
    for calendar in calendars:
        default_tz = _calendar_default_tzinfo(calendar)
        for obj in calendar.calendar_objects.all():
            b, t, u = core_freebusy.freebusy_intervals_for_object(
                obj.ical_blob,
                window_start,
                window_end,
                default_tz,
                lambda value: core_freebusy.parse_freebusy_value(
                    value,
                    core_time.parse_ical_datetime,
                    core_time.parse_ical_duration,
                    core_time.as_utc_datetime,
                ),
                core_time.as_utc_datetime,
            )
            busy.extend(b)
            tentative.extend(t)
            unavailable.extend(u)

    def merge_intervals(intervals):
        return core_freebusy.merge_intervals(intervals)

    busy = merge_intervals(busy)
    tentative = merge_intervals(tentative)
    unavailable = merge_intervals(unavailable)

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//davhome//EN",
        "BEGIN:VFREEBUSY",
        f"DTSTART:{core_freebusy.format_ical_utc(window_start)}",
        f"DTEND:{core_freebusy.format_ical_utc(window_end)}",
    ]
    if busy:
        values = ",".join(
            f"{core_freebusy.format_ical_utc(start_i)}/{core_freebusy.format_ical_utc(end_i)}"
            for start_i, end_i in busy
        )
        lines.append(f"FREEBUSY:{values}")
    if tentative:
        values = ",".join(
            f"{core_freebusy.format_ical_utc(start_i)}/{core_freebusy.format_ical_utc(end_i)}"
            for start_i, end_i in tentative
        )
        lines.append(f"FREEBUSY;FBTYPE=BUSY-TENTATIVE:{values}")
    if unavailable:
        values = ",".join(
            f"{core_freebusy.format_ical_utc(start_i)}/{core_freebusy.format_ical_utc(end_i)}"
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
        return None, core_davxml.propfind_finite_depth_error(
            _xml_response, qname, NS_DAV
        )
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

    prop_map = core_propmap.build_root_prop_map(user, _principal_href_for_user)

    depth = request.headers.get("Depth", "infinity")
    if parsed is None:
        return HttpResponse(status=400)

    requested = parsed["requested"] if parsed["mode"] == "prop" else None
    root_ok, root_missing = core_props.select_props(prop_map, requested)
    responses = [response_with_props("/dav/", root_ok, root_missing)]

    if depth == "1":
        principal_href = _principal_href_for_user(user)
        home_href = _calendar_home_href_for_user(user)

        principal_map = core_propmap.build_principal_prop_map(
            user,
            user,
            _principal_href_for_user,
            _calendar_home_href_for_user,
        )
        principal_ok, principal_missing = core_props.select_props(
            principal_map, requested
        )
        responses.append(
            response_with_props(principal_href, principal_ok, principal_missing)
        )

        home_map = core_propmap.build_calendar_home_prop_map(
            user,
            user,
            _principal_href_for_user,
        )
        home_ok, home_missing = core_props.select_props(home_map, requested)
        responses.append(response_with_props(home_href, home_ok, home_missing))

    return _xml_response(207, multistatus_document(responses))


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
    principal_map = core_propmap.build_principal_prop_map(
        user,
        principal,
        _principal_href_for_user,
        _calendar_home_href_for_user,
    )
    ok, missing = core_props.select_props(principal_map, requested)
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

    resolved_map = core_propmap.build_collection_prop_map(
        display_name,
        user,
        _principal_href_for_user,
    )

    requested = parsed["requested"] if parsed["mode"] == "prop" else None
    ok, missing = core_props.select_props(resolved_map, requested)
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
    home_map = core_propmap.build_calendar_home_prop_map(
        owner,
        user,
        _principal_href_for_user,
    )
    home_ok, home_missing = core_props.select_props(home_map, requested)
    responses = [
        response_with_props(f"/dav/calendars/{owner.username}/", home_ok, home_missing)
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


def _object_href_for_style_data(obj_data, style):
    if style == "uids":
        guid = _dav_guid_for_username(obj_data.owner_username)
        if guid is not None:
            return f"/dav/calendars/__uids__/{guid}/{obj_data.slug}/{obj_data.filename}"

    if style == "users":
        return f"/dav/calendars/users/{obj_data.owner_username}/{obj_data.slug}/{obj_data.filename}"

    return (
        f"/dav/calendars/{obj_data.owner_username}/{obj_data.slug}/{obj_data.filename}"
    )


def _all_object_hrefs_for_data(obj_data):
    hrefs = {
        f"/dav/calendars/{obj_data.owner_username}/{obj_data.slug}/{obj_data.filename}",
        f"/dav/calendars/users/{obj_data.owner_username}/{obj_data.slug}/{obj_data.filename}",
    }
    guid = _dav_guid_for_username(obj_data.owner_username)
    if guid is not None:
        hrefs.add(f"/dav/calendars/__uids__/{guid}/{obj_data.slug}/{obj_data.filename}")
    return hrefs


def _responses_for_multiget(calendars, requested, hrefs, calendar_data_request=None):
    responses = []
    objects = shell_repository.list_calendar_object_data_for_calendars(calendars)
    by_path = core_report.build_href_index(objects, _all_object_hrefs_for_data)
    resolved = core_report.resolve_multiget_hrefs(
        hrefs,
        by_path,
        core_paths.normalize_href_path,
    )

    for normalized, obj in resolved:
        if obj is None:
            responses.append(response_with_status(normalized, "404 Not Found"))
            continue

        obj_map = _build_prop_map_for_object(obj, calendar_data_request)
        ok, missing = core_props.select_props(obj_map, requested)
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
    objects = shell_repository.list_calendar_object_data_for_calendars(calendars)
    matched = core_report.select_query_objects(
        objects,
        query_filter,
        _object_matches_query_with_active_tz,
    )
    for obj in matched:
        obj_map = _build_prop_map_for_object(obj, calendar_data_request)
        ok, missing = core_props.select_props(obj_map, requested)
        href = _object_href_for_style_data(obj, style)
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
        ok, missing = core_props.select_props(prop_map, requested)
        return response_with_props(href, ok, missing)

    latest_revision = _latest_sync_revision(calendar)
    style = _report_href_style(request_path)
    changes = [
        core_sync.SyncChange(
            revision=change.revision,
            filename=change.filename,
            is_deleted=change.is_deleted,
        )
        for change in CalendarObjectChange.objects.filter(calendar=calendar).order_by(
            "revision"
        )
    ]
    current_filenames = [obj.filename for obj in calendar.calendar_objects.all()]
    selection = core_sync.select_sync_collection_items(
        token_revision=token_revision,
        latest_revision=latest_revision,
        changes=changes,
        current_filenames=current_filenames,
        limit=limit,
    )

    if selection.invalid_token:
        return _valid_sync_token_error_response()

    responses = []
    selected_items = []
    selected_items_source = selection.source
    next_revision = selection.next_revision

    if token_revision is None:
        cal_map = core_propmap.build_calendar_collection_prop_map(
            calendar,
            calendar.owner,
            _principal_href_for_user,
            _sync_token_for_calendar,
        )
        responses.append(
            response_for_props(
                _collection_href_for_style(calendar, style),
                cal_map,
            )
        )
    selected_filenames = [
        item.filename for item in selection.items if not item.is_deleted
    ]
    current_objects = {
        obj.filename: obj
        for obj in calendar.calendar_objects.filter(filename__in=selected_filenames)
    }

    for item in selection.items:
        obj = current_objects.get(item.filename)
        selected_items.append(
            {
                "revision": item.revision,
                "filename": item.filename,
                "is_deleted": item.is_deleted,
                "object_found": obj is not None,
            }
        )
        if item.is_deleted:
            if token_revision is not None:
                href = _object_href_for_filename(calendar, item.filename, style)
                responses.append(response_with_status(href, "404 Not Found"))
            continue

        if obj is None:
            if token_revision is not None:
                href = _object_href_for_filename(calendar, item.filename, style)
                responses.append(response_with_status(href, "404 Not Found"))
            continue

        href = _object_href_for_style(calendar, obj, style)
        if token_revision is not None:
            href = _object_href_for_filename(calendar, item.filename, style)
        obj_map = _build_prop_map_for_object(obj, calendar_data_request)
        responses.append(response_for_props(href, obj_map))

    max_items_to_log = 200
    logger.info(
        "dav_sync_collection_selection path=%s calendar_id=%s owner=%s slug=%s token_revision=%r latest_revision=%s next_revision=%s initial_sync=%s limit=%r selected_items_source=%s selected_items_count=%s selected_items_truncated=%s selected_items=%r",
        request_path,
        calendar.id,
        calendar.owner.username,
        calendar.slug,
        token_revision,
        latest_revision,
        next_revision,
        token_revision is None,
        limit,
        selected_items_source,
        len(selected_items),
        len(selected_items) > max_items_to_log,
        selected_items[:max_items_to_log],
    )

    response_sync_token = _build_sync_token(calendar.id, next_revision)
    logger.info(
        "dav_sync_collection_response path=%s calendar_id=%s owner=%s slug=%s response_count=%s response_sync_token=%s",
        request_path,
        calendar.id,
        calendar.owner.username,
        calendar.slug,
        len(responses),
        response_sync_token,
    )
    body = _sync_collection_multistatus_document(
        responses,
        response_sync_token,
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

    time_range_error = core_report.validate_time_range_payloads(
        root,
        core_time.parse_ical_datetime,
    )
    if time_range_error is not None:
        _ACTIVE_REPORT_TZINFO = None
        return HttpResponse(status=400)

    range_bounds_error = core_report.validate_comp_filter_range_bounds(
        root,
        core_time.parse_ical_datetime,
        timezone.now().year,
    )
    if range_bounds_error is not None:
        _ACTIVE_REPORT_TZINFO = None
        return _caldav_error_response(range_bounds_error)

    context = core_report_dispatch.build_report_execution_context(
        parsed_report=parsed_report,
        calendars=calendars,
        request_path=request.path,
        classify_report_kind=core_report.classify_report_kind,
    )

    def handle_multiget(exec_context):
        responses = _responses_for_multiget(
            exec_context.calendars,
            exec_context.requested_props,
            exec_context.parsed_report.hrefs,
            exec_context.calendar_data_request,
        )
        return _xml_response(207, multistatus_document(responses))

    def handle_query(exec_context):
        responses = _responses_for_calendar_query(
            exec_context.calendars,
            exec_context.requested_props,
            exec_context.parsed_report.query_filter,
            exec_context.request_path,
            exec_context.calendar_data_request,
        )
        return _xml_response(207, multistatus_document(responses))

    def handle_freebusy(exec_context):
        return _render_freebusy_report(exec_context.calendars, exec_context.root)

    def handle_sync_collection(exec_context):
        sync_request = core_report.parse_sync_collection_request(
            exec_context.root,
            _sync_collection_limit,
        )
        requested_limit = sync_request.requested_limit

        if not allow_sync_collection:
            return HttpResponse(status=501)

        sync_level = sync_request.sync_level
        if sync_level and sync_level != "1":
            return HttpResponse(status=400)

        if len(exec_context.calendars) != 1:
            return HttpResponse(status=501)

        sync_token_value = sync_request.sync_token
        calendar = exec_context.calendars[0]
        logger.info(
            "dav_sync_collection_request path=%s calendar_id=%s owner=%s slug=%s sync_level=%r token_present=%s sync_token=%r requested_limit=%r",
            exec_context.request_path,
            calendar.id,
            calendar.owner.username,
            calendar.slug,
            sync_level,
            bool(sync_token_value),
            sync_token_value or None,
            requested_limit,
        )
        token_revision = None
        if sync_token_value:
            token_revision, token_error = _parse_sync_token_for_calendar(
                sync_token_value,
                calendar,
            )
            if token_error is not None:
                logger.info(
                    "dav_sync_collection_token_rejected path=%s calendar_id=%s sync_token=%r",
                    exec_context.request_path,
                    calendar.id,
                    sync_token_value,
                )
                return token_error
            logger.info(
                "dav_sync_collection_token_parsed path=%s calendar_id=%s token_revision=%s",
                exec_context.request_path,
                calendar.id,
                token_revision,
            )

        return _sync_collection_response(
            calendar,
            exec_context.request_path,
            exec_context.requested_props,
            exec_context.calendar_data_request,
            token_revision,
            requested_limit,
        )

    response = core_report_dispatch.dispatch_report(
        context=context,
        report_kind_multiget=core_report.REPORT_KIND_MULTIGET,
        report_kind_query=core_report.REPORT_KIND_QUERY,
        report_kind_freebusy=core_report.REPORT_KIND_FREEBUSY,
        report_kind_sync_collection=core_report.REPORT_KIND_SYNC_COLLECTION,
        handle_multiget=handle_multiget,
        handle_query=handle_query,
        handle_freebusy=handle_freebusy,
        handle_sync_collection=handle_sync_collection,
        handle_unknown=lambda _exec_context: _report_unknown_type(),
    )
    _ACTIVE_REPORT_TZINFO = None
    return response


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
                    tzid = core_payloads.extract_tzid_from_timezone_text(timezone_text)
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


@csrf_exempt
def calendar_collection_users_view(request, username, slug):
    return calendar_collection_view(request, username, slug)


def _build_prop_map_for_object(obj, calendar_data_request=None):
    size = getattr(obj, "size", None)
    if size is None:
        size = len(getattr(obj, "ical_blob", "") or "")
    last_modified = getattr(obj, "updated_at", None) or getattr(
        obj, "last_modified", None
    )
    if last_modified is None:
        last_modified = timezone.now()
    return core_propmap.build_object_prop_map(
        obj=obj,
        etag_for_object=_etag_for_object,
        getlastmodified_text=http_date(last_modified.timestamp()),
        calendar_data_element=_calendar_data_prop(
            _filter_calendar_data_with_active_tz(
                obj.ical_blob,
                calendar_data_request,
            )
        ),
    )


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
                return _copy_or_move_calendar_object(
                    writable,
                    request,
                    username,
                    slug,
                    filename,
                    next_revision,
                    is_move=request.method == "MOVE",
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
                                entry, encoding="unicode"
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
                    error, status=400, content_type="text/plain; charset=utf-8"
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
        normalized_filename = core_paths.collection_marker(filename)

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
    ok, missing = core_props.select_props(obj_map, requested)
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
