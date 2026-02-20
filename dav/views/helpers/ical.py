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
