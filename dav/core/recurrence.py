import re
from datetime import datetime, timedelta, timezone as datetime_timezone
from zoneinfo import ZoneInfo

import icalendar
from recurring_ical_events import of as recurring_of

from . import filters as core_filters
from . import time as core_time


def extract_component_blocks(ical_text, component_name):
    pattern = rf"BEGIN:{re.escape(component_name)}\r?\n(.*?)\r?\nEND:{re.escape(component_name)}"
    matches = re.finditer(pattern, ical_text, flags=re.DOTALL | re.IGNORECASE)
    return [match.group(0) for match in matches]


def calendar_for_component_text(component_text):
    unfolded = core_time.unfold_ical(component_text)
    if "BEGIN:VCALENDAR" in unfolded:
        return unfolded
    return f"BEGIN:VCALENDAR\nVERSION:2.0\n{unfolded}\nEND:VCALENDAR\n"


def parse_rrule_count(component_text):
    rrule = core_time.first_ical_line_value(component_text, "RRULE")
    if not rrule:
        return None
    match = re.search(r"(?:^|;)COUNT=(\d+)(?:;|$)", rrule)
    if match is None:
        return None
    return int(match.group(1))


def parse_line_datetime_with_tz(line, active_report_tzinfo=None):
    if not line or ":" not in line:
        return None
    params = core_filters.parse_property_params(line)
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
        default_tz = active_report_tzinfo or datetime_timezone.utc
        dt = dt.replace(tzinfo=default_tz).astimezone(datetime_timezone.utc)
    return dt


def line_matches_time_range(line, time_range, active_report_tzinfo=None):
    prop_dt = parse_line_datetime_with_tz(
        line, active_report_tzinfo=active_report_tzinfo
    )
    if prop_dt is None:
        return False
    start = core_time.parse_ical_datetime(time_range.get("start"))
    end = core_time.parse_ical_datetime(time_range.get("end"))
    if start is not None and prop_dt < start:
        return False
    if end is not None and prop_dt >= end:
        return False
    return True


def simple_recurrence_instances(component_text, active_report_tzinfo=None):
    upper_component = component_text.upper()
    component_name = "VEVENT" if "BEGIN:VEVENT" in upper_component else "VTODO"
    blocks = extract_component_blocks(component_text, component_name)
    if not blocks:
        return None
    master_block = next(
        (block for block in blocks if "RECURRENCE-ID" not in block.upper()),
        blocks[0],
    )

    master_start = parse_line_datetime_with_tz(
        core_time.first_ical_line(master_block, "DTSTART"),
        active_report_tzinfo=active_report_tzinfo,
    )
    master_due = parse_line_datetime_with_tz(
        core_time.first_ical_line(master_block, "DUE"),
        active_report_tzinfo=active_report_tzinfo,
    )
    base_start = master_start or master_due
    if base_start is None:
        return None

    rrule = core_time.first_ical_line_value(master_block, "RRULE")
    if not rrule:
        return None
    if "FREQ=DAILY" not in rrule.upper():
        return None
    count = parse_rrule_count(master_block)
    if not count:
        return None

    dtend = parse_line_datetime_with_tz(
        core_time.first_ical_line(master_block, "DTEND"),
        active_report_tzinfo=active_report_tzinfo,
    )
    duration = core_time.parse_ical_duration(
        core_time.first_ical_line_value(master_block, "DURATION") or ""
    )
    if dtend is not None:
        duration = dtend - base_start
    if duration is None:
        duration = timedelta(0)

    exdates = set()
    for line in core_filters.property_lines(master_block, "EXDATE"):
        if ":" not in line:
            continue
        values = line.split(":", 1)[1].split(",")
        for value in values:
            dt = core_time.parse_ical_datetime(value.strip())
            if dt is None:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime_timezone.utc)
            exdates.add(dt.astimezone(datetime_timezone.utc))

    overrides = {}
    this_and_future = None
    for block in blocks:
        rec_line = core_time.first_ical_line(block, "RECURRENCE-ID")
        if rec_line is None:
            continue
        rec_id = parse_line_datetime_with_tz(
            rec_line,
            active_report_tzinfo=active_report_tzinfo,
        )
        if rec_id is None:
            continue
        override_start = parse_line_datetime_with_tz(
            core_time.first_ical_line(block, "DTSTART"),
            active_report_tzinfo=active_report_tzinfo,
        )
        if override_start is None:
            override_start = parse_line_datetime_with_tz(
                core_time.first_ical_line(block, "DUE"),
                active_report_tzinfo=active_report_tzinfo,
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


def matches_time_range_recurrence(
    component_text, start, end, component_name, active_report_tzinfo=None
):
    simple = simple_recurrence_instances(
        component_text,
        active_report_tzinfo=active_report_tzinfo,
    )
    if simple is not None:
        for occ_start, occ_end in simple:
            if start is not None and occ_end <= start:
                continue
            if end is not None and occ_start >= end:
                continue
            return True
        return False

    try:
        calendar_text = calendar_for_component_text(component_text)
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


def alarm_matches_time_range(component_text, time_range, active_report_tzinfo=None):
    start = core_time.parse_ical_datetime(time_range.get("start"))
    end = core_time.parse_ical_datetime(time_range.get("end"))
    if start is None and end is None:
        return False

    window_start = core_time.as_utc_datetime(start) or datetime.now(
        datetime_timezone.utc
    ) - timedelta(days=365 * 20)
    window_end = core_time.as_utc_datetime(end) or datetime.now(
        datetime_timezone.utc
    ) + timedelta(days=365 * 20)

    simple_instances = simple_recurrence_instances(
        component_text,
        active_report_tzinfo=active_report_tzinfo,
    )
    has_override_alarm = any(
        "RECURRENCE-ID" in block.upper() and "BEGIN:VALARM" in block.upper()
        for block in extract_component_blocks(component_text, "VEVENT")
    )
    if simple_instances and not has_override_alarm:
        upper = component_text.upper()
        component_name = "VEVENT" if "BEGIN:VEVENT" in upper else "VTODO"
        blocks = extract_component_blocks(component_text, component_name)
        master_block = next(
            (block for block in blocks if "RECURRENCE-ID" not in block.upper()),
            blocks[0] if blocks else "",
        )
        alarms = extract_component_blocks(master_block, "VALARM")
        for alarm_block in alarms:
            trigger_line = core_time.first_ical_line(alarm_block, "TRIGGER")
            if trigger_line is None:
                continue
            trigger_delta = core_time.parse_ical_duration(trigger_line.split(":", 1)[1])
            if trigger_delta is None:
                continue
            related_end = "RELATED=END" in trigger_line.upper()
            repeat = int(core_time.first_ical_line_value(alarm_block, "REPEAT") or 0)
            repeat_duration = core_time.parse_ical_duration(
                core_time.first_ical_line_value(alarm_block, "DURATION") or ""
            )
            for occ_start, occ_end in simple_instances:
                base = occ_end if related_end else occ_start
                trigger_time = base + trigger_delta
                trigger_times = [trigger_time]
                if repeat > 0 and repeat_duration is not None:
                    for i in range(1, repeat + 1):
                        trigger_times.append(trigger_time + i * repeat_duration)
                for trigger in trigger_times:
                    if window_start <= trigger <= window_end:
                        return True
        return False

    try:
        cal = icalendar.Calendar.from_ical(calendar_for_component_text(component_text))
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
    for block in extract_component_blocks(component_text, "VEVENT"):
        rec_line = core_time.first_ical_line(block, "RECURRENCE-ID")
        if rec_line is None or "RANGE=THISANDFUTURE" not in rec_line.upper():
            continue
        if "BEGIN:VALARM" in block.upper():
            continue
        rec_id = parse_line_datetime_with_tz(
            rec_line,
            active_report_tzinfo=active_report_tzinfo,
        )
        if rec_id is not None:
            cutoff_without_alarm = rec_id

    for component in occurrences:
        component_name = (component.name or "").upper()
        if component_name not in ("VEVENT", "VTODO"):
            continue
        base_start = core_time.as_utc_datetime(component.decoded("DTSTART", None))
        base_end = core_time.as_utc_datetime(component.decoded("DTEND", None))
        due = core_time.as_utc_datetime(component.decoded("DUE", None))
        if base_start is None:
            base_start = due
        if base_end is None:
            base_end = due or base_start
        if base_start is None:
            continue

        if cutoff_without_alarm is not None and base_start >= cutoff_without_alarm:
            continue

        for alarm in component.subcomponents:
            if (alarm.name or "").upper() != "VALARM":
                continue
            trigger = alarm.decoded("TRIGGER", None)
            if trigger is None:
                continue
            if isinstance(trigger, datetime):
                trigger_time = core_time.as_utc_datetime(trigger)
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

            for trigger_value in trigger_times:
                if trigger_value is None:
                    continue
                if window_start <= trigger_value <= window_end:
                    return True

    return False
