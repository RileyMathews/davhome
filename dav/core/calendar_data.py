import icalendar
from recurring_ical_events import of as recurring_of

from dav.xml import NS_CALDAV, qname


def ensure_shifted_first_occurrence_recurrence_id(
    ical_blob,
    master_starts,
    tzinfo,
    extract_component_blocks,
    first_ical_line_value,
    first_ical_line,
    format_value_date_or_datetime,
):
    if not master_starts:
        return ical_blob

    updated = ical_blob
    for component_name in ("VEVENT", "VTODO"):
        blocks = extract_component_blocks(updated, component_name)
        for block in blocks:
            if "RECURRENCE-ID" in block.upper():
                continue
            uid = first_ical_line_value(block, "UID")
            if not uid:
                continue

            dt_line = first_ical_line(block, "DTSTART") or first_ical_line(block, "DUE")
            if dt_line is None or ":" not in dt_line:
                continue
            dt_text = dt_line.split(":", 1)[1].strip()

            master_text, master_is_date = format_value_date_or_datetime(
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


def filter_calendar_data_for_response(
    ical_blob,
    calendar_data_request,
    active_report_tzinfo,
    parse_ical_datetime,
    as_utc_datetime,
    serialize_expanded_components,
    ensure_shifted_first_occurrence_recurrence_id,
):
    if calendar_data_request is None or len(list(calendar_data_request)) == 0:
        return ical_blob

    expand = calendar_data_request.find(qname(NS_CALDAV, "expand"))
    if expand is not None:
        start = parse_ical_datetime(expand.get("start"))
        end = parse_ical_datetime(expand.get("end"))
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
                    start_utc = as_utc_datetime(start_value)
                    for ex_prop in exdate_props:
                        for ex_entry in getattr(ex_prop, "dts", []):
                            ex_value = getattr(ex_entry, "dt", None)
                            if ex_value is None:
                                continue
                            ex_utc = as_utc_datetime(ex_value)
                            if start_utc is not None and ex_utc == start_utc:
                                first_instance_excluded_uids.add(uid_key)
                                break
                query = recurring_of(cal)
                query.keep_recurrence_attributes = True
                expanded = query.between(start, end)
                ical_blob = serialize_expanded_components(
                    expanded,
                    active_report_tzinfo,
                    master_starts,
                    first_instance_excluded_uids,
                )
                ical_blob = ensure_shifted_first_occurrence_recurrence_id(
                    ical_blob,
                    master_starts,
                    active_report_tzinfo,
                )
            except Exception:
                pass

    lines = ical_blob.replace("\r\n", "\n").split("\n")
    filtered = [line for line in lines if not line.upper().startswith("DTSTAMP")]
    return "\r\n".join(filtered).rstrip("\r\n") + "\r\n"
