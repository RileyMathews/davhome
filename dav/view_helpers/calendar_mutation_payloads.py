from zoneinfo import ZoneInfo

from calendars.models import Calendar

from ..core import payloads as core_payloads
from ..xml import NS_APPLE_ICAL, NS_CALDAV, NS_DAV, qname
from .parsing import _parse_xml_body


def _mkcalendar_props_from_payload(payload, caldav_error_response):
    defaults = {
        "display_name": None,
        "description": "",
        "timezone": "UTC",
        "color": "",
        "sort_order": None,
        "component_kind": Calendar.COMPONENT_VEVENT,
    }
    if not payload:
        return defaults, [], None

    root = _parse_xml_body(payload)
    if root is None or root.tag != qname(NS_CALDAV, "mkcalendar"):
        return None, [], caldav_error_response("valid-calendar-data", status=400)

    prop = root.find(f".//{qname(NS_DAV, 'set')}/{qname(NS_DAV, 'prop')}")
    if prop is None:
        return defaults, [], None

    property_tags = [entry.tag for entry in list(prop)]
    allowed_tags = {
        qname(NS_DAV, "displayname"),
        qname(NS_CALDAV, "calendar-description"),
        qname(NS_CALDAV, "calendar-timezone"),
        qname(NS_CALDAV, "calendar-free-busy-set"),
        qname(NS_CALDAV, "supported-calendar-component-set"),
        qname(NS_APPLE_ICAL, "calendar-color"),
        qname(NS_APPLE_ICAL, "calendar-order"),
    }
    if qname(NS_DAV, "getetag") in property_tags:
        return defaults, property_tags, None
    if any(tag not in allowed_tags for tag in property_tags):
        return defaults, property_tags, None

    display = prop.find(qname(NS_DAV, "displayname"))
    if display is not None and (display.text or "").strip():
        defaults["display_name"] = (display.text or "").strip()

    description = prop.find(qname(NS_CALDAV, "calendar-description"))
    if description is not None and (description.text or "").strip():
        defaults["description"] = (description.text or "").strip()

    timezone_elem = prop.find(qname(NS_CALDAV, "calendar-timezone"))
    if timezone_elem is not None and (timezone_elem.text or "").strip():
        tzid = core_payloads.extract_tzid_from_timezone_text(
            (timezone_elem.text or "").strip()
        )
        if not tzid:
            return None, [], caldav_error_response("valid-calendar-data", status=400)
        try:
            ZoneInfo(tzid)
            defaults["timezone"] = tzid
        except Exception:
            return None, [], caldav_error_response("valid-calendar-data", status=400)

    color_elem = prop.find(qname(NS_APPLE_ICAL, "calendar-color"))
    if color_elem is not None and (color_elem.text or "").strip():
        defaults["color"] = (color_elem.text or "").strip()

    order_elem = prop.find(qname(NS_APPLE_ICAL, "calendar-order"))
    if order_elem is not None and (order_elem.text or "").strip():
        try:
            defaults["sort_order"] = int((order_elem.text or "").strip())
        except ValueError:
            return None, [], caldav_error_response("valid-calendar-data", status=400)

    comp_set = prop.find(qname(NS_CALDAV, "supported-calendar-component-set"))
    if comp_set is not None:
        names = {
            (comp.get("name") or "").upper()
            for comp in comp_set.findall(qname(NS_CALDAV, "comp"))
            if (comp.get("name") or "").strip()
        }
        if len(names) != 1:
            return (
                defaults,
                [qname(NS_CALDAV, "supported-calendar-component-set")],
                None,
            )
        if not names.issubset({Calendar.COMPONENT_VEVENT, Calendar.COMPONENT_VTODO}):
            return (
                defaults,
                [qname(NS_CALDAV, "supported-calendar-component-set")],
                None,
            )
        defaults["component_kind"] = names.pop()

    return defaults, [], None


def _calendar_collection_proppatch_plan(root, calendar_slug, current_values):
    pending_values = dict(current_values)
    update_fields = set()
    ok_tags = []
    bad_tags = []

    for operation in list(root):
        if operation.tag not in (qname(NS_DAV, "set"), qname(NS_DAV, "remove")):
            continue
        prop = operation.find(qname(NS_DAV, "prop"))
        if prop is None:
            continue
        is_set = operation.tag == qname(NS_DAV, "set")
        for entry in list(prop):
            if entry.tag == qname(NS_DAV, "displayname"):
                if is_set:
                    pending_values["name"] = (entry.text or "").strip() or calendar_slug
                else:
                    pending_values["name"] = calendar_slug
                update_fields.add("name")
                ok_tags.append(entry.tag)
                continue

            if entry.tag == qname(NS_CALDAV, "calendar-description"):
                pending_values["description"] = (
                    (entry.text or "").strip() if is_set else ""
                )
                update_fields.add("description")
                ok_tags.append(entry.tag)
                continue

            if entry.tag == qname(NS_CALDAV, "calendar-timezone"):
                if not is_set:
                    pending_values["timezone"] = "UTC"
                    update_fields.add("timezone")
                    ok_tags.append(entry.tag)
                    continue
                timezone_text = (entry.text or "").strip()
                tzid = core_payloads.extract_tzid_from_timezone_text(timezone_text)
                if not tzid:
                    bad_tags.append(entry.tag)
                    continue
                try:
                    ZoneInfo(tzid)
                except Exception:
                    bad_tags.append(entry.tag)
                    continue
                pending_values["timezone"] = tzid
                update_fields.add("timezone")
                ok_tags.append(entry.tag)
                continue

            if entry.tag == qname(NS_APPLE_ICAL, "calendar-color"):
                pending_values["color"] = (entry.text or "").strip() if is_set else ""
                update_fields.add("color")
                ok_tags.append(entry.tag)
                continue

            if entry.tag == qname(NS_APPLE_ICAL, "calendar-order"):
                if not is_set:
                    pending_values["sort_order"] = None
                    update_fields.add("sort_order")
                    ok_tags.append(entry.tag)
                    continue
                try:
                    pending_values["sort_order"] = int((entry.text or "").strip())
                except ValueError:
                    bad_tags.append(entry.tag)
                    continue
                update_fields.add("sort_order")
                ok_tags.append(entry.tag)
                continue

            bad_tags.append(entry.tag)

    return pending_values, update_fields, ok_tags, bad_tags
