from datetime import datetime, timedelta, timezone as datetime_timezone
from typing import Any, cast

import icalendar
from recurring_ical_events import of as recurring_of


type Interval = tuple[datetime, datetime]


def format_ical_utc(dt):
    return dt.astimezone(datetime_timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def parse_freebusy_value(
    value,
    parse_ical_datetime,
    parse_ical_duration,
    as_utc_datetime,
):
    if "/" not in value:
        return None
    start_raw, end_raw = value.split("/", 1)
    start = parse_ical_datetime(start_raw)
    if start is None:
        return None
    if end_raw.startswith("P") or end_raw.startswith("-P"):
        duration = parse_ical_duration(end_raw)
        if duration is None:
            return None
        end = start + duration
    else:
        end = parse_ical_datetime(end_raw)
        if end is None:
            return None
    return as_utc_datetime(start), as_utc_datetime(end)


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


def freebusy_intervals_for_object(
    ical_blob,
    window_start,
    window_end,
    default_tz,
    parse_freebusy_value,
    as_utc_datetime,
):
    busy: list[Interval] = []
    tentative: list[Interval] = []
    unavailable: list[Interval] = []

    try:
        cal = icalendar.Calendar.from_ical(ical_blob)
    except Exception:
        return busy, tentative, unavailable

    for component in cal.walk():
        name = (component.name or "").upper()
        if name == "VFREEBUSY":
            freebusy_component = cast(Any, component)
            for prop in freebusy_component.getall("FREEBUSY"):
                params = {k.upper(): str(v) for k, v in prop.params.items()}
                fbtype = params.get("FBTYPE", "BUSY").upper()
                values = prop.to_ical().decode("utf-8").split(":", 1)[1].split(",")
                for value in values:
                    parsed = parse_freebusy_value(value.strip())
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
            start = as_utc_datetime(start_raw, default_tz)
            end = as_utc_datetime(end_raw, default_tz)
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
