from xml.etree import ElementTree as ET


def text_prop(qname_func, namespace, name, value):
    elem = ET.Element(qname_func(namespace, name))
    elem.text = value
    return elem


def resourcetype_prop(qname_func, ns_dav, *types):
    elem = ET.Element(qname_func(ns_dav, "resourcetype"))
    for resource_type in types:
        ET.SubElement(elem, qname_func(*resource_type))
    return elem


def supported_components_prop(qname_func, ns_caldav, component_kind):
    elem = ET.Element(qname_func(ns_caldav, "supported-calendar-component-set"))
    ET.SubElement(elem, qname_func(ns_caldav, "comp"), name=component_kind)
    return elem


def supported_component_sets_prop(qname_func, ns_caldav, component_kinds):
    elem = ET.Element(qname_func(ns_caldav, "supported-calendar-component-sets"))
    for component_kind in component_kinds:
        subset = ET.SubElement(
            elem,
            qname_func(ns_caldav, "supported-calendar-component-set"),
        )
        ET.SubElement(subset, qname_func(ns_caldav, "comp"), name=component_kind)
    return elem


def calendar_timezone_prop(qname_func, ns_caldav, timezone_name):
    elem = ET.Element(qname_func(ns_caldav, "calendar-timezone"))
    elem.text = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "BEGIN:VTIMEZONE\r\n"
        f"TZID:{timezone_name}\r\n"
        "END:VTIMEZONE\r\n"
        "END:VCALENDAR\r\n"
    )
    return elem


def calendar_color_prop(qname_func, ns_apple_ical, color):
    elem = ET.Element(qname_func(ns_apple_ical, "calendar-color"))
    elem.text = color
    return elem


def calendar_order_prop(qname_func, ns_apple_ical, sort_order):
    elem = ET.Element(qname_func(ns_apple_ical, "calendar-order"))
    elem.text = str(sort_order)
    return elem


def select_props(prop_map, requested_tags):
    if requested_tags is None:
        return [builder() for builder in prop_map.values()], []

    ok = []
    missing = []
    for tag in requested_tags:
        builder = prop_map.get(tag)
        if builder is None:
            missing.append(ET.Element(tag))
        else:
            ok.append(builder())
    return ok, missing
