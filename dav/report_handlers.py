# pyright: reportGeneralTypeIssues=false, reportAttributeAccessIssue=false

import logging
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

from django.http import HttpResponse
from django.utils import timezone
from django.utils.http import http_date

from calendars.models import CalendarObjectChange

from .core import calendar_data as core_calendar_data
from .core import filters as core_filters
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
from .report_engine import parse_report_request
from .shell import repository as shell_repository
from .xml import NS_CALDAV, NS_DAV, multistatus_document, qname, response_with_props
from .xml import response_with_status
from .view_helpers.freebusy import _build_freebusy_response_lines
from .view_helpers.parsing import _calendar_default_tzinfo
from .view_helpers.recurrence_serialization import _serialize_expanded_components
from .view_helpers.report_paths import _all_object_hrefs_for_data
from .view_helpers.report_paths import _collection_href_for_style
from .view_helpers.report_paths import _object_href_for_filename
from .view_helpers.report_paths import _object_href_for_style
from .view_helpers.report_paths import _object_href_for_style_data
from .view_helpers.report_paths import _report_href_style
from .view_helpers.identity import _principal_href_for_user
from .view_helpers.sync_tokens import _build_sync_token, _parse_sync_token_for_calendar
from .common import _caldav_error_response
from .common import _dav_common_headers
from .common import _etag_for_object
from .common import _latest_sync_revision
from .common import _sync_token_for_calendar
from .common import _valid_sync_token_error_response
from .common import _xml_response


logger = logging.getLogger("dav.audit")


_ACTIVE_REPORT_TZINFO = None


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


def _build_prop_map_for_object(obj, calendar_data_request=None):
    size = getattr(obj, "size", None)
    if size is None:
        size = len(getattr(obj, "ical_blob", "") or "")
    last_modified = getattr(obj, "updated_at", None) or getattr(
        obj,
        "last_modified",
        None,
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

    busy = core_freebusy.merge_intervals(busy)
    tentative = core_freebusy.merge_intervals(tentative)
    unavailable = core_freebusy.merge_intervals(unavailable)

    lines = _build_freebusy_response_lines(
        window_start,
        window_end,
        busy,
        tentative,
        unavailable,
    )
    response = HttpResponse(
        "\r\n".join(lines).encode("utf-8"),
        status=200,
        content_type="text/calendar; charset=utf-8",
    )
    return _dav_common_headers(response)


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
                _valid_sync_token_error_response,
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
