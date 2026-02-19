from ..core import freebusy as core_freebusy


def _build_freebusy_response_lines(
    window_start, window_end, busy, tentative, unavailable
):
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
    return lines
