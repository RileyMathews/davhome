from dataclasses import dataclass
from datetime import datetime
from xml.etree import ElementTree as ET


_PROTOCOL_NAMESPACES = ("caldav", "dav")


@dataclass(frozen=True, slots=True)
class TimeRange:
    start: datetime | None = None
    end: datetime | None = None

    def __post_init__(self):
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError("TimeRange.end must be greater than TimeRange.start")


@dataclass(frozen=True, slots=True)
class ProtocolError:
    code: str
    http_status: int = 403
    namespace: str = "caldav"

    def __post_init__(self):
        if not self.code:
            raise ValueError("ProtocolError.code must be non-empty")
        if self.namespace not in _PROTOCOL_NAMESPACES:
            raise ValueError("ProtocolError.namespace must be 'caldav' or 'dav'")
        if self.http_status < 100 or self.http_status > 599:
            raise ValueError("ProtocolError.http_status must be an HTTP status")


@dataclass(frozen=True, slots=True)
class CalendarObjectData:
    calendar_id: str
    owner_username: str
    slug: str
    filename: str
    etag: str
    content_type: str
    ical_blob: str
    last_modified: datetime | None = None

    def __post_init__(self):
        required = {
            "calendar_id": self.calendar_id,
            "owner_username": self.owner_username,
            "slug": self.slug,
            "filename": self.filename,
            "etag": self.etag,
            "content_type": self.content_type,
        }
        for field_name, value in required.items():
            if not value:
                raise ValueError(f"CalendarObjectData.{field_name} must be non-empty")


@dataclass(frozen=True, slots=True)
class WritePrecondition:
    if_match: tuple[str, ...] = ()
    if_none_match: str | None = None
    existing_etag: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "if_match", tuple(self.if_match))
        if self.if_none_match is not None and self.if_none_match != "*":
            raise ValueError("WritePrecondition.if_none_match must be '*' or None")


@dataclass(frozen=True, slots=True)
class WriteDecision:
    allowed: bool
    error: ProtocolError | None = None

    def __post_init__(self):
        if self.allowed and self.error is not None:
            raise ValueError("WriteDecision cannot have an error when allowed=True")
        if not self.allowed and self.error is None:
            raise ValueError("WriteDecision must include error when allowed=False")


@dataclass(frozen=True, slots=True)
class ReportRequest:
    report_name: str
    requested_props: tuple[str, ...] = ()
    calendar_data_request: ET.Element | None = None
    hrefs: tuple[str, ...] = ()
    query_filter: ET.Element | None = None
    time_range: TimeRange | None = None

    def __post_init__(self):
        if not self.report_name:
            raise ValueError("ReportRequest.report_name must be non-empty")
        object.__setattr__(self, "requested_props", tuple(self.requested_props))
        object.__setattr__(self, "hrefs", tuple(self.hrefs))


@dataclass(frozen=True, slots=True)
class ReportResult:
    responses: tuple[ET.Element, ...] = ()
    sync_token: str | None = None
    error: ProtocolError | None = None

    def __post_init__(self):
        object.__setattr__(self, "responses", tuple(self.responses))
