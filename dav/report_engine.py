from dataclasses import dataclass
from xml.etree import ElementTree as ET

from .xml import NS_CALDAV, NS_DAV, qname


@dataclass
class ParsedReportRequest:
    root: ET.Element
    requested_props: list[str] | None
    calendar_data_request: ET.Element | None
    hrefs: list[str]
    query_filter: ET.Element | None


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
        requested_props=requested_props,
        calendar_data_request=calendar_data_request,
        hrefs=hrefs,
        query_filter=query_filter,
    )
