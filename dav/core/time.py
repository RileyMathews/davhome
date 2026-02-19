import re
from datetime import datetime, timedelta, timezone as datetime_timezone


def parse_ical_datetime(value):
    if not value:
        return None
    raw = value.strip()
    try:
        if re.fullmatch(r"\d{8}", raw):
            return datetime.strptime(raw, "%Y%m%d").replace(
                tzinfo=datetime_timezone.utc
            )
        if raw.endswith("Z") and re.fullmatch(r"\d{8}T\d{6}Z", raw):
            return datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(
                tzinfo=datetime_timezone.utc
            )
        if re.fullmatch(r"\d{8}T\d{6}", raw):
            return datetime.strptime(raw, "%Y%m%dT%H%M%S").replace(
                tzinfo=datetime_timezone.utc
            )
    except ValueError:
        return None
    return None


def parse_ical_duration(value):
    if not value:
        return None
    text = value.strip().upper()
    sign = -1 if text.startswith("-") else 1
    if text[0] in "+-":
        text = text[1:]
    if not text.startswith("P"):
        return None
    text = text[1:]
    days = hours = minutes = seconds = 0
    if "T" in text:
        date_part, time_part = text.split("T", 1)
    else:
        date_part, time_part = text, ""

    day_match = re.search(r"(\d+)D", date_part)
    if day_match:
        days = int(day_match.group(1))
    hour_match = re.search(r"(\d+)H", time_part)
    if hour_match:
        hours = int(hour_match.group(1))
    minute_match = re.search(r"(\d+)M", time_part)
    if minute_match:
        minutes = int(minute_match.group(1))
    second_match = re.search(r"(\d+)S", time_part)
    if second_match:
        seconds = int(second_match.group(1))

    return sign * timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


def format_ical_duration(value):
    if value is None:
        return None
    seconds = int(value.total_seconds())
    sign = "-" if seconds < 0 else ""
    seconds = abs(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}D")
    time_parts = []
    if hours:
        time_parts.append(f"{hours}H")
    if minutes:
        time_parts.append(f"{minutes}M")
    if secs:
        time_parts.append(f"{secs}S")
    if not parts and not time_parts:
        time_parts.append("0S")
    if time_parts:
        return f"{sign}P{''.join(parts)}T{''.join(time_parts)}"
    return f"{sign}P{''.join(parts)}"


def format_value_date_or_datetime(value, tzinfo=None):
    if isinstance(value, datetime):
        return value.astimezone(datetime_timezone.utc).strftime("%Y%m%dT%H%M%SZ"), False
    if value is None:
        return None, False
    out_date = value
    if tzinfo is not None:
        probe = datetime(value.year, value.month, value.day, 12, tzinfo=tzinfo)
        offset = probe.utcoffset() or timedelta(0)
        if offset.total_seconds() < 0:
            out_date = value - timedelta(days=1)
    return out_date.strftime("%Y%m%d"), True


def as_utc_datetime(value, default_tz=None):
    if default_tz is None:
        default_tz = datetime_timezone.utc
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=default_tz).astimezone(datetime_timezone.utc)
        return value.astimezone(datetime_timezone.utc)
    return datetime.combine(value, datetime.min.time(), tzinfo=default_tz).astimezone(
        datetime_timezone.utc
    )


def unfold_ical(ical_text):
    return re.sub(r"\r?\n[ \t]", "", ical_text)


def first_ical_line_value(ical_text, key):
    pattern = rf"^{key}(?:;[^:]*)?:(.+)$"
    match = re.search(pattern, ical_text, flags=re.MULTILINE)
    if match is None:
        return None
    return match.group(1).strip()


def first_ical_line(ical_text, key):
    pattern = rf"^{key}(?:;[^:]*)?:(.+)$"
    match = re.search(pattern, ical_text, flags=re.MULTILINE)
    if match is None:
        return None
    return match.group(0)
