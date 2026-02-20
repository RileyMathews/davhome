from dataclasses import dataclass
from xml.etree import ElementTree as ET

from dav.core.contracts import ReportRequest
from dav.xml import NS_CALDAV, NS_DAV, qname


@dataclass
class ParsedReportRequest:
    root: ET.Element
    report_request: ReportRequest
    requested_props_raw: list[str] | None

    @property
    def requested_props(self):
        if self.requested_props_raw is None:
            return None
        return list(self.report_request.requested_props)

    @property
    def calendar_data_request(self):
        return self.report_request.calendar_data_request

    @property
    def hrefs(self):
        return list(self.report_request.hrefs)

    @property
    def query_filter(self):
        return self.report_request.query_filter


def parse_report_request(payload: bytes) -> ParsedReportRequest | None:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return None

    prop = root.find(qname(NS_DAV, "prop"))
    requested_props = None
    calendar_data_request = None
    if prop is not None:
        requested_props = [child.tag for child in list(prop)]
        calendar_data_request = prop.find(qname(NS_CALDAV, "calendar-data"))

    hrefs = [elem.text or "" for elem in root.findall(qname(NS_DAV, "href"))]
    query_filter = None
    if root.tag == qname(NS_CALDAV, "calendar-query"):
        filter_elem = root.find(qname(NS_CALDAV, "filter"))
        if filter_elem is not None:
            query_filter = filter_elem.find(qname(NS_CALDAV, "comp-filter"))

    return ParsedReportRequest(
        root=root,
        report_request=ReportRequest(
            report_name=root.tag,
            requested_props=tuple(requested_props or []),
            calendar_data_request=calendar_data_request,
            hrefs=tuple(hrefs),
            query_filter=query_filter,
        ),
        requested_props_raw=requested_props,
    )
