from dataclasses import dataclass
from datetime import datetime, timezone as datetime_timezone

from dav.xml import NS_CALDAV, NS_DAV, qname


REPORT_KIND_MULTIGET = "calendar-multiget"
REPORT_KIND_QUERY = "calendar-query"
REPORT_KIND_FREEBUSY = "free-busy-query"
REPORT_KIND_SYNC_COLLECTION = "sync-collection"
REPORT_KIND_UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SyncCollectionRequest:
    sync_level: str
    sync_token: str
    requested_limit: int | None


def classify_report_kind(root_tag: str) -> str:
    if root_tag == qname(NS_CALDAV, "calendar-multiget"):
        return REPORT_KIND_MULTIGET
    if root_tag == qname(NS_CALDAV, "calendar-query"):
        return REPORT_KIND_QUERY
    if root_tag == qname(NS_CALDAV, "free-busy-query"):
        return REPORT_KIND_FREEBUSY
    if root_tag == qname(NS_DAV, "sync-collection"):
        return REPORT_KIND_SYNC_COLLECTION
    return REPORT_KIND_UNKNOWN


def validate_time_range_payloads(root, parse_ical_datetime):
    for time_range in root.findall(f".//{qname(NS_CALDAV, 'time-range')}"):
        start_raw = time_range.get("start")
        end_raw = time_range.get("end")
        if not start_raw and not end_raw:
            return "bad-request"

        start = parse_ical_datetime(start_raw)
        end = parse_ical_datetime(end_raw)
        if start_raw and start is None:
            return "bad-request"
        if end_raw and end is None:
            return "bad-request"
    return None


def validate_comp_filter_range_bounds(root, parse_ical_datetime, now_year: int):
    low_limit = datetime(now_year - 1, 1, 1, tzinfo=datetime_timezone.utc)
    high_limit = datetime(
        now_year + 5, 12, 31, 23, 59, 59, tzinfo=datetime_timezone.utc
    )

    for comp_filter in root.findall(f".//{qname(NS_CALDAV, 'comp-filter')}"):
        time_range = comp_filter.find(qname(NS_CALDAV, "time-range"))
        if time_range is None:
            continue
        start = parse_ical_datetime(time_range.get("start"))
        end = parse_ical_datetime(time_range.get("end"))
        if start is not None and start < low_limit:
            return "min-date-time"
        if end is not None and end < low_limit:
            return "min-date-time"
        if start is not None and start > high_limit:
            return "max-date-time"
        if end is not None and end > high_limit:
            return "max-date-time"

    return None


def parse_sync_collection_request(root, parse_limit):
    return SyncCollectionRequest(
        sync_level=(root.findtext(qname(NS_DAV, "sync-level")) or "").strip(),
        sync_token=(root.findtext(qname(NS_DAV, "sync-token")) or "").strip(),
        requested_limit=parse_limit(root),
    )
