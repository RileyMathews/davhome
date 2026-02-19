import re


def extract_uid(ical_text):
    match = re.search(r"^UID:(.+)$", ical_text, flags=re.MULTILINE)
    if match is None:
        return None
    return match.group(1).strip()


def extract_tzid_from_timezone_text(timezone_text):
    if not timezone_text:
        return None
    match = re.search(r"^TZID:(.+)$", timezone_text, flags=re.MULTILINE)
    if match is None:
        return None
    return match.group(1).strip()


def component_kind_from_payload(payload_text):
    upper = payload_text.upper()
    has_event = "BEGIN:VEVENT" in upper
    has_todo = "BEGIN:VTODO" in upper
    if has_event and has_todo:
        return None
    if has_todo:
        return "VTODO"
    if has_event:
        return "VEVENT"
    return None


def validate_ical_payload(payload):
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return None, "Calendar payload must be UTF-8 text."

    if "BEGIN:VCALENDAR" not in text or "END:VCALENDAR" not in text:
        return None, "Calendar payload must contain VCALENDAR boundaries."

    uid = extract_uid(text)
    if uid is None:
        return None, "Calendar payload must contain a UID property."

    return {"text": text, "uid": uid}, None


def validate_generic_payload(payload):
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return None, "Generic DAV payload must be UTF-8 text."

    return {"text": text, "uid": None}, None


def if_match_values(header):
    return [value.strip() for value in header.split(",") if value.strip()]


def precondition_failed_for_write(request, existing_obj):
    if_none_match = request.headers.get("If-None-Match")
    if if_none_match == "*" and existing_obj is not None:
        return True

    if_match = request.headers.get("If-Match")
    if if_match:
        if existing_obj is None:
            return True
        allowed = if_match_values(if_match)
        if "*" not in allowed and existing_obj.etag not in allowed:
            return True

    return False
