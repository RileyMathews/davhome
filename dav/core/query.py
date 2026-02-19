import re
from datetime import timedelta

from dav.xml import NS_CALDAV, qname


def matches_time_range(
    component_text,
    time_range,
    parse_ical_datetime,
    matches_time_range_recurrence,
    parse_line_datetime_with_tz,
    first_ical_line,
    parse_ical_duration,
    first_ical_line_value,
):
    start = parse_ical_datetime(time_range.get("start"))
    end = parse_ical_datetime(time_range.get("end"))

    if start is None and end is None:
        return False

    component_upper = component_text.upper()
    if "BEGIN:VTODO" in component_upper and "RRULE:" in component_upper:
        if "DTSTART" not in component_upper and "DUE" in component_upper:
            synthetic = component_text.replace("BEGIN:VTODO", "BEGIN:VEVENT").replace(
                "END:VTODO", "END:VEVENT"
            )
            synthetic = re.sub(
                r"^DUE(;[^:]*)?:",
                r"DTSTART\1:",
                synthetic,
                flags=re.MULTILINE,
            )
            return matches_time_range_recurrence(synthetic, start, end, "VEVENT")

    if "RRULE:" in component_upper or "RECURRENCE-ID" in component_upper:
        if "BEGIN:VTODO" in component_upper:
            return matches_time_range_recurrence(component_text, start, end, "VTODO")
        return matches_time_range_recurrence(component_text, start, end, "VEVENT")

    event_start = parse_line_datetime_with_tz(
        first_ical_line(component_text, "DTSTART")
    )
    event_end = parse_line_datetime_with_tz(first_ical_line(component_text, "DTEND"))
    due = parse_line_datetime_with_tz(first_ical_line(component_text, "DUE"))
    duration = parse_ical_duration(
        first_ical_line_value(component_text, "DURATION") or ""
    )

    if event_start is None:
        event_start = due
    if event_start is None:
        return True
    if event_end is None:
        dtstart_line = first_ical_line(component_text, "DTSTART") or ""
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


def matches_comp_filter(
    context_text,
    comp_filter,
    extract_component_blocks,
    matches_time_range,
    matches_prop_filter,
    alarm_matches_time_range,
    combine_filter_results,
):
    name = (comp_filter.get("name") or "").upper()
    if not name:
        return True

    if name == "VCALENDAR":
        candidates = [context_text]
    else:
        candidates = extract_component_blocks(context_text, name)

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
        return matches_time_range(context_text, time_range)

    if name == "VEVENT":
        asks_no_alarm = any(
            (child.get("name") or "").upper() == "VALARM"
            and child.find(qname(NS_CALDAV, "is-not-defined")) is not None
            for child in child_comp_filters
        )
        if asks_no_alarm:
            vevents = extract_component_blocks(context_text, "VEVENT")
            if not vevents:
                return False
            master = next(
                (block for block in vevents if "RECURRENCE-ID" not in block.upper()),
                vevents[0],
            )
            return "BEGIN:VALARM" not in master.upper()

    if name == "VALARM" and time_range is not None:
        return alarm_matches_time_range(context_text, time_range)

    candidate_results = []
    for candidate in candidates:
        checks = []
        if time_range is not None:
            checks.append(matches_time_range(candidate, time_range))
        checks.extend(
            matches_prop_filter(candidate, prop_filter) for prop_filter in prop_filters
        )
        checks.extend(
            matches_comp_filter(
                context_text
                if (
                    name == "VEVENT"
                    and (child_comp_filter.get("name") or "").upper() == "VALARM"
                    and child_comp_filter.find(qname(NS_CALDAV, "time-range"))
                    is not None
                )
                else candidate,
                child_comp_filter,
                extract_component_blocks,
                matches_time_range,
                matches_prop_filter,
                alarm_matches_time_range,
                combine_filter_results,
            )
            for child_comp_filter in child_comp_filters
        )
        candidate_results.append(
            combine_filter_results(checks, test_attr) if checks else True
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


def object_matches_query(ical_blob, query_filter, unfold_ical, matches_comp_filter):
    if query_filter is None:
        return True
    unfolded = unfold_ical(ical_blob)
    return matches_comp_filter(unfolded, query_filter)
