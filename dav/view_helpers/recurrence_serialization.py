from datetime import datetime

from ..core import time as core_time


def _append_date_or_datetime_line(lines, prop_name, text, is_date):
    if is_date:
        lines.append(f"{prop_name};VALUE=DATE:{text}")
    else:
        lines.append(f"{prop_name}:{text}")


def _uid_drop_recurrence_map(expanded, tzinfo):
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

    return {
        uid: min(values)
        for uid, values in uid_recurrence_ids.items()
        if uid not in uid_has_master and len(values) > 1
    }


def _resolved_recurrence_text(
    component,
    uid_key,
    tzinfo,
    dtstart_text,
    dtstart_is_date,
    master_starts,
    first_instance_excluded_uids,
    uid_drop_recurrence,
):
    rec_id = component.decoded("RECURRENCE-ID", None)
    rec_text, rec_is_date = core_time.format_value_date_or_datetime(rec_id, tzinfo)
    if rec_text is None and master_starts is not None and dtstart_text:
        master_start = master_starts.get(uid_key)
        master_text, _ = core_time.format_value_date_or_datetime(master_start, tzinfo)
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
    return rec_text, rec_is_date


def _serialize_expanded_components(
    expanded,
    tzinfo=None,
    master_starts=None,
    first_instance_excluded_uids=None,
):
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0"]
    uid_drop_recurrence = _uid_drop_recurrence_map(expanded, tzinfo)

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

        uid_key = str(uid or "")
        rec_text, rec_is_date = _resolved_recurrence_text(
            component,
            uid_key,
            tzinfo,
            dtstart_text,
            dtstart_is_date,
            master_starts,
            first_instance_excluded_uids,
            uid_drop_recurrence,
        )
        if rec_text:
            _append_date_or_datetime_line(lines, "RECURRENCE-ID", rec_text, rec_is_date)

        dtend = component.decoded("DTEND", None)
        dtend_text, dtend_is_date = core_time.format_value_date_or_datetime(
            dtend, tzinfo
        )
        if dtend_text:
            _append_date_or_datetime_line(lines, "DTEND", dtend_text, dtend_is_date)

        due = component.decoded("DUE", None)
        due_text, due_is_date = core_time.format_value_date_or_datetime(due, tzinfo)
        if due_text:
            _append_date_or_datetime_line(lines, "DUE", due_text, due_is_date)

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
