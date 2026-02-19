from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET


def caldav_error_response(
    xml_response, qname_func, ns_dav, ns_caldav, error_name, status=403
):
    error = ET.Element(qname_func(ns_dav, "error"))
    ET.SubElement(error, qname_func(ns_caldav, error_name))
    return xml_response(
        status,
        ET.tostring(error, encoding="utf-8", xml_declaration=True),
    )


def dav_error_response(xml_response, qname_func, ns_dav, error_name, status=403):
    error = ET.Element(qname_func(ns_dav, "error"))
    ET.SubElement(error, qname_func(ns_dav, error_name))
    return xml_response(
        status,
        ET.tostring(error, encoding="utf-8", xml_declaration=True),
    )


def valid_sync_token_error_response(xml_response, qname_func, ns_dav):
    return dav_error_response(
        xml_response,
        qname_func,
        ns_dav,
        "valid-sync-token",
        status=403,
    )


def propfind_finite_depth_error(xml_response, qname_func, ns_dav):
    error = ET.Element(qname_func(ns_dav, "error"))
    ET.SubElement(error, qname_func(ns_dav, "propfind-finite-depth"))
    return xml_response(
        403,
        ET.tostring(error, encoding="utf-8", xml_declaration=True),
    )


def owner_prop(qname_func, ns_dav, principal_href_for_user, owner_user):
    elem = ET.Element(qname_func(ns_dav, "owner"))
    href = ET.SubElement(elem, qname_func(ns_dav, "href"))
    href.text = principal_href_for_user(owner_user)
    return elem


def current_user_privilege_set_prop(qname_func, ns_dav, can_write):
    elem = ET.Element(qname_func(ns_dav, "current-user-privilege-set"))
    privileges = ["read", "read-current-user-privilege-set"]
    if can_write:
        privileges.extend(["write", "write-content", "bind", "unbind"])

    for privilege_name in privileges:
        privilege = ET.SubElement(elem, qname_func(ns_dav, "privilege"))
        ET.SubElement(privilege, qname_func(ns_dav, privilege_name))

    return elem


def supported_report_set_prop(
    qname_func,
    ns_dav,
    ns_caldav,
    include_freebusy=False,
    include_sync_collection=False,
):
    elem = ET.Element(qname_func(ns_dav, "supported-report-set"))

    def add_report(namespace, name):
        supported_report = ET.SubElement(elem, qname_func(ns_dav, "supported-report"))
        report = ET.SubElement(supported_report, qname_func(ns_dav, "report"))
        ET.SubElement(report, qname_func(namespace, name))

    add_report(ns_caldav, "calendar-query")
    add_report(ns_caldav, "calendar-multiget")
    if include_freebusy:
        add_report(ns_caldav, "free-busy-query")
    if include_sync_collection:
        add_report(ns_dav, "sync-collection")

    return elem


def if_none_match_matches(header_value, if_match_values, etag):
    if not header_value:
        return False
    values = if_match_values(header_value)
    return "*" in values or etag in values


def if_modified_since_not_modified(header_value, timestamp):
    if not header_value:
        return False
    try:
        date = parsedate_to_datetime(header_value)
    except (TypeError, ValueError):
        return False
    if date is None:
        return False
    if date.tzinfo is None:
        from datetime import timezone as datetime_timezone

        date = date.replace(tzinfo=datetime_timezone.utc)
    return int(timestamp) <= int(date.timestamp())
