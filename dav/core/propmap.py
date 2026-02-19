from xml.etree import ElementTree as ET

from dav.xml import NS_APPLE_ICAL, NS_CALDAV, NS_CS, NS_DAV, qname

from calendars.models import Calendar
from calendars.permissions import can_write_calendar

from . import davxml as core_davxml
from . import props as core_props


def build_root_prop_map(user, principal_href_for_user):
    def current_user_principal():
        elem = ET.Element(qname(NS_DAV, "current-user-principal"))
        href = ET.SubElement(elem, qname(NS_DAV, "href"))
        href.text = principal_href_for_user(user)
        return elem

    return {
        qname(NS_DAV, "resourcetype"): lambda: ET.Element(
            qname(NS_DAV, "resourcetype")
        ),
        qname(NS_DAV, "displayname"): lambda: core_props.text_prop(
            qname,
            NS_DAV,
            "displayname",
            "davhome",
        ),
        qname(NS_DAV, "current-user-principal"): current_user_principal,
    }


def build_root_unauthenticated_prop_map():
    def current_user_principal():
        elem = ET.Element(qname(NS_DAV, "current-user-principal"))
        ET.SubElement(elem, qname(NS_DAV, "unauthenticated"))
        return elem

    return {
        qname(NS_DAV, "resourcetype"): lambda: ET.Element(
            qname(NS_DAV, "resourcetype")
        ),
        qname(NS_DAV, "displayname"): lambda: core_props.text_prop(
            qname,
            NS_DAV,
            "displayname",
            "davhome",
        ),
        qname(NS_DAV, "current-user-principal"): current_user_principal,
    }


def build_principal_prop_map(
    auth_user,
    principal_user,
    principal_href_for_user,
    calendar_home_href_for_user,
):
    def current_user_principal():
        elem = ET.Element(qname(NS_DAV, "current-user-principal"))
        href = ET.SubElement(elem, qname(NS_DAV, "href"))
        href.text = principal_href_for_user(auth_user)
        return elem

    def calendar_home_set():
        elem = ET.Element(qname(NS_CALDAV, "calendar-home-set"))
        href = ET.SubElement(elem, qname(NS_DAV, "href"))
        href.text = calendar_home_href_for_user(principal_user)
        return elem

    return {
        qname(NS_DAV, "resourcetype"): lambda: core_props.resourcetype_prop(
            qname,
            NS_DAV,
            (NS_DAV, "principal"),
        ),
        qname(NS_DAV, "displayname"): lambda: core_props.text_prop(
            qname,
            NS_DAV,
            "displayname",
            principal_user.username,
        ),
        qname(NS_DAV, "current-user-principal"): current_user_principal,
        qname(NS_CALDAV, "calendar-home-set"): calendar_home_set,
    }


def build_calendar_home_prop_map(owner, auth_user, principal_href_for_user):
    can_write = owner == auth_user
    return {
        qname(NS_DAV, "resourcetype"): lambda: core_props.resourcetype_prop(
            qname,
            NS_DAV,
            (NS_DAV, "collection"),
        ),
        qname(NS_DAV, "getcontentlength"): lambda: core_props.text_prop(
            qname,
            NS_DAV,
            "getcontentlength",
            "",
        ),
        qname(NS_DAV, "displayname"): lambda: core_props.text_prop(
            qname,
            NS_DAV,
            "displayname",
            f"{owner.username} calendars",
        ),
        qname(NS_DAV, "owner"): lambda: core_davxml.owner_prop(
            qname,
            NS_DAV,
            principal_href_for_user,
            owner,
        ),
        qname(
            NS_DAV,
            "current-user-privilege-set",
        ): lambda: core_davxml.current_user_privilege_set_prop(
            qname,
            NS_DAV,
            can_write,
        ),
        qname(
            NS_DAV,
            "supported-report-set",
        ): lambda: core_davxml.supported_report_set_prop(
            qname,
            NS_DAV,
            NS_CALDAV,
            include_freebusy=True,
        ),
        qname(
            NS_CALDAV,
            "supported-calendar-component-sets",
        ): lambda: core_props.supported_component_sets_prop(
            qname,
            NS_CALDAV,
            (Calendar.COMPONENT_VEVENT, Calendar.COMPONENT_VTODO),
        ),
    }


def build_collection_prop_map(display_name, auth_user, principal_href_for_user):
    def current_user_principal_for_requester():
        elem = ET.Element(qname(NS_DAV, "current-user-principal"))
        href = ET.SubElement(elem, qname(NS_DAV, "href"))
        href.text = principal_href_for_user(auth_user)
        return elem

    return {
        qname(NS_DAV, "resourcetype"): lambda: core_props.resourcetype_prop(
            qname,
            NS_DAV,
            (NS_DAV, "collection"),
        ),
        qname(NS_DAV, "displayname"): lambda: core_props.text_prop(
            qname,
            NS_DAV,
            "displayname",
            display_name,
        ),
        qname(NS_DAV, "current-user-principal"): current_user_principal_for_requester,
        qname(
            NS_DAV,
            "supported-report-set",
        ): lambda: core_davxml.supported_report_set_prop(
            qname,
            NS_DAV,
            NS_CALDAV,
            include_freebusy=True,
        ),
    }


def build_calendar_collection_prop_map(
    calendar,
    auth_user,
    principal_href_for_user,
    sync_token_for_calendar,
):
    can_write = can_write_calendar(calendar, auth_user)
    return {
        qname(NS_DAV, "resourcetype"): lambda: core_props.resourcetype_prop(
            qname,
            NS_DAV,
            (NS_DAV, "collection"),
            (NS_CALDAV, "calendar"),
        ),
        qname(NS_DAV, "getcontentlength"): lambda: core_props.text_prop(
            qname,
            NS_DAV,
            "getcontentlength",
            "",
        ),
        qname(NS_DAV, "getcontenttype"): lambda: core_props.text_prop(
            qname,
            NS_DAV,
            "getcontenttype",
            "text/calendar",
        ),
        qname(NS_DAV, "displayname"): lambda: core_props.text_prop(
            qname,
            NS_DAV,
            "displayname",
            calendar.name,
        ),
        qname(NS_CS, "getctag"): lambda: core_props.text_prop(
            qname,
            NS_CS,
            "getctag",
            str(int(calendar.updated_at.timestamp())),
        ),
        qname(
            NS_CALDAV,
            "supported-calendar-component-set",
        ): lambda: core_props.supported_components_prop(
            qname,
            NS_CALDAV,
            calendar.component_kind,
        ),
        qname(
            NS_CALDAV,
            "calendar-timezone",
        ): lambda: core_props.calendar_timezone_prop(
            qname,
            NS_CALDAV,
            calendar.timezone,
        ),
        qname(NS_CALDAV, "calendar-description"): lambda: core_props.text_prop(
            qname,
            NS_CALDAV,
            "calendar-description",
            calendar.description,
        ),
        qname(NS_APPLE_ICAL, "calendar-color"): lambda: core_props.calendar_color_prop(
            qname,
            NS_APPLE_ICAL,
            calendar.color,
        ),
        qname(NS_APPLE_ICAL, "calendar-order"): lambda: core_props.calendar_order_prop(
            qname,
            NS_APPLE_ICAL,
            calendar.sort_order if calendar.sort_order is not None else 0,
        ),
        qname(NS_DAV, "getetag"): lambda: core_props.text_prop(
            qname,
            NS_DAV,
            "getetag",
            f'"{int(calendar.updated_at.timestamp())}"',
        ),
        qname(NS_DAV, "owner"): lambda: core_davxml.owner_prop(
            qname,
            NS_DAV,
            principal_href_for_user,
            calendar.owner,
        ),
        qname(
            NS_DAV,
            "current-user-privilege-set",
        ): lambda: core_davxml.current_user_privilege_set_prop(
            qname,
            NS_DAV,
            can_write,
        ),
        qname(
            NS_DAV,
            "supported-report-set",
        ): lambda: core_davxml.supported_report_set_prop(
            qname,
            NS_DAV,
            NS_CALDAV,
            include_freebusy=True,
            include_sync_collection=True,
        ),
        qname(NS_DAV, "sync-token"): lambda: core_props.text_prop(
            qname,
            NS_DAV,
            "sync-token",
            sync_token_for_calendar(calendar),
        ),
    }


def build_object_prop_map(
    *,
    obj,
    etag_for_object,
    getlastmodified_text,
    calendar_data_element,
):
    size = getattr(obj, "size", None)
    if size is None:
        size = len(getattr(obj, "ical_blob", "") or "")
    dead_properties = getattr(obj, "dead_properties", None) or {}

    prop_map = {
        qname(NS_DAV, "resourcetype"): lambda: ET.Element(
            qname(NS_DAV, "resourcetype")
        ),
        qname(NS_DAV, "getetag"): lambda: core_props.text_prop(
            qname,
            NS_DAV,
            "getetag",
            etag_for_object(obj),
        ),
        qname(NS_DAV, "getcontenttype"): lambda: core_props.text_prop(
            qname,
            NS_DAV,
            "getcontenttype",
            obj.content_type,
        ),
        qname(NS_DAV, "getcontentlength"): lambda: core_props.text_prop(
            qname,
            NS_DAV,
            "getcontentlength",
            str(size),
        ),
        qname(NS_DAV, "getlastmodified"): lambda: core_props.text_prop(
            qname,
            NS_DAV,
            "getlastmodified",
            getlastmodified_text,
        ),
        qname(NS_CALDAV, "calendar-data"): lambda: calendar_data_element,
    }

    for tag, xml_value in dead_properties.items():

        def _dead_prop_builder(value=xml_value):
            try:
                return ET.fromstring(value)
            except ET.ParseError:
                return ET.Element(qname(NS_DAV, "invalid-dead-property"))

        prop_map[tag] = _dead_prop_builder

    return prop_map


def object_live_property_tags():
    return {
        qname(NS_DAV, "resourcetype"),
        qname(NS_DAV, "getetag"),
        qname(NS_DAV, "getcontenttype"),
        qname(NS_DAV, "getcontentlength"),
        qname(NS_DAV, "getlastmodified"),
        qname(NS_CALDAV, "calendar-data"),
    }
