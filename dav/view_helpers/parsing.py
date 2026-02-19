from datetime import timezone as datetime_timezone
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo


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
