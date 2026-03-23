from xml.etree import ElementTree as ET

from django.test import SimpleTestCase

from calendars.models import Calendar
from dav.views.helpers import calendar_mutation_payloads as mutation_payloads
from dav.xml import NS_APPLE_ICAL, NS_CALDAV, NS_DAV, qname


class ViewHelpersCalendarMutationPayloadTests(SimpleTestCase):
    def _caldav_error_response(self, condition, status=403):
        return {
            "condition": condition,
            "status": status,
        }

    def test_mkcalendar_payload_rejects_invalid_root(self):
        defaults, bad_props, error = mutation_payloads._mkcalendar_props_from_payload(
            b"<D:propfind xmlns:D='DAV:' />",
            self._caldav_error_response,
        )
        self.assertIsNone(defaults)
        self.assertEqual(bad_props, [])
        self.assertEqual(error["condition"], "valid-calendar-data")
        self.assertEqual(error["status"], 400)

    def test_mkcalendar_payload_without_prop_uses_defaults(self):
        payload = (
            b"<?xml version='1.0'?>"
            b"<C:mkcalendar xmlns:C='urn:ietf:params:xml:ns:caldav' />"
        )
        defaults, bad_props, error = mutation_payloads._mkcalendar_props_from_payload(
            payload,
            self._caldav_error_response,
        )
        self.assertIsNone(error)
        self.assertEqual(bad_props, [])
        self.assertEqual(defaults["timezone"], "UTC")
        self.assertEqual(defaults["description"], "")
        self.assertEqual(defaults["color"], "")
        self.assertIsNone(defaults["sort_order"])
        self.assertEqual(defaults["component_kind"], Calendar.COMPONENT_VEVENT)

    def test_mkcalendar_payload_returns_prop_errors_for_protected_or_unknown_tags(self):
        protected_payload = (
            b"<?xml version='1.0'?>"
            b"<C:mkcalendar xmlns:C='urn:ietf:params:xml:ns:caldav' xmlns:D='DAV:'>"
            b"  <D:set><D:prop><D:getetag /></D:prop></D:set>"
            b"</C:mkcalendar>"
        )
        defaults, bad_props, error = mutation_payloads._mkcalendar_props_from_payload(
            protected_payload,
            self._caldav_error_response,
        )
        self.assertIsNone(error)
        self.assertEqual(defaults["timezone"], "UTC")
        self.assertIn(qname(NS_DAV, "getetag"), bad_props)

        unknown_payload = (
            b"<?xml version='1.0'?>"
            b"<C:mkcalendar xmlns:C='urn:ietf:params:xml:ns:caldav' xmlns:D='DAV:'>"
            b"  <D:set><D:prop><D:unknown /></D:prop></D:set>"
            b"</C:mkcalendar>"
        )
        defaults, bad_props, error = mutation_payloads._mkcalendar_props_from_payload(
            unknown_payload,
            self._caldav_error_response,
        )
        self.assertIsNone(error)
        self.assertEqual(defaults["timezone"], "UTC")
        self.assertIn(qname(NS_DAV, "unknown"), bad_props)

    def test_mkcalendar_payload_accepts_valid_values_and_component_kind(self):
        payload = (
            b"<?xml version='1.0'?>"
            b"<C:mkcalendar xmlns:C='urn:ietf:params:xml:ns:caldav' xmlns:D='DAV:' xmlns:A='http://apple.com/ns/ical/'>"
            b"  <D:set><D:prop>"
            b"    <D:displayname>   </D:displayname>"
            b"    <C:calendar-description>Home</C:calendar-description>"
            b"    <C:calendar-timezone>BEGIN:VTIMEZONE\nTZID:UTC\nEND:VTIMEZONE</C:calendar-timezone>"
            b"    <A:calendar-color>#00FF00</A:calendar-color>"
            b"    <A:calendar-order>4</A:calendar-order>"
            b"    <C:supported-calendar-component-set><C:comp name='VTODO' /></C:supported-calendar-component-set>"
            b"  </D:prop></D:set>"
            b"</C:mkcalendar>"
        )
        defaults, bad_props, error = mutation_payloads._mkcalendar_props_from_payload(
            payload,
            self._caldav_error_response,
        )
        self.assertIsNone(error)
        self.assertEqual(bad_props, [])
        self.assertIsNone(defaults["display_name"])
        self.assertEqual(defaults["description"], "Home")
        self.assertEqual(defaults["timezone"], "UTC")
        self.assertEqual(defaults["color"], "#00FF00")
        self.assertEqual(defaults["sort_order"], 4)
        self.assertEqual(defaults["component_kind"], Calendar.COMPONENT_VTODO)

    def test_mkcalendar_payload_rejects_bad_timezone_or_sort_order(self):
        no_tzid_payload = (
            b"<?xml version='1.0'?>"
            b"<C:mkcalendar xmlns:C='urn:ietf:params:xml:ns:caldav' xmlns:D='DAV:'>"
            b"  <D:set><D:prop><C:calendar-timezone>BEGIN:VTIMEZONE\nEND:VTIMEZONE</C:calendar-timezone></D:prop></D:set>"
            b"</C:mkcalendar>"
        )
        defaults, bad_props, error = mutation_payloads._mkcalendar_props_from_payload(
            no_tzid_payload,
            self._caldav_error_response,
        )
        self.assertIsNone(defaults)
        self.assertEqual(bad_props, [])
        self.assertEqual(error["status"], 400)

        invalid_tz_payload = (
            b"<?xml version='1.0'?>"
            b"<C:mkcalendar xmlns:C='urn:ietf:params:xml:ns:caldav' xmlns:D='DAV:'>"
            b"  <D:set><D:prop><C:calendar-timezone>BEGIN:VTIMEZONE\nTZID:No/Such_Zone\nEND:VTIMEZONE</C:calendar-timezone></D:prop></D:set>"
            b"</C:mkcalendar>"
        )
        defaults, bad_props, error = mutation_payloads._mkcalendar_props_from_payload(
            invalid_tz_payload,
            self._caldav_error_response,
        )
        self.assertIsNone(defaults)
        self.assertEqual(bad_props, [])
        self.assertEqual(error["status"], 400)

        invalid_order_payload = (
            b"<?xml version='1.0'?>"
            b"<C:mkcalendar xmlns:C='urn:ietf:params:xml:ns:caldav' xmlns:D='DAV:' xmlns:A='http://apple.com/ns/ical/'>"
            b"  <D:set><D:prop><A:calendar-order>nope</A:calendar-order></D:prop></D:set>"
            b"</C:mkcalendar>"
        )
        defaults, bad_props, error = mutation_payloads._mkcalendar_props_from_payload(
            invalid_order_payload,
            self._caldav_error_response,
        )
        self.assertIsNone(defaults)
        self.assertEqual(bad_props, [])
        self.assertEqual(error["status"], 400)

    def test_mkcalendar_payload_component_set_must_be_single_supported_value(self):
        multiple_payload = (
            b"<?xml version='1.0'?>"
            b"<C:mkcalendar xmlns:C='urn:ietf:params:xml:ns:caldav' xmlns:D='DAV:'>"
            b"  <D:set><D:prop>"
            b"    <C:supported-calendar-component-set>"
            b"      <C:comp name='VEVENT' />"
            b"      <C:comp name='VTODO' />"
            b"    </C:supported-calendar-component-set>"
            b"  </D:prop></D:set>"
            b"</C:mkcalendar>"
        )
        defaults, bad_props, error = mutation_payloads._mkcalendar_props_from_payload(
            multiple_payload,
            self._caldav_error_response,
        )
        self.assertIsNone(error)
        self.assertEqual(defaults["component_kind"], Calendar.COMPONENT_VEVENT)
        self.assertEqual(
            bad_props,
            [qname(NS_CALDAV, "supported-calendar-component-set")],
        )

        unsupported_payload = (
            b"<?xml version='1.0'?>"
            b"<C:mkcalendar xmlns:C='urn:ietf:params:xml:ns:caldav' xmlns:D='DAV:'>"
            b"  <D:set><D:prop>"
            b"    <C:supported-calendar-component-set><C:comp name='VJOURNAL' /></C:supported-calendar-component-set>"
            b"  </D:prop></D:set>"
            b"</C:mkcalendar>"
        )
        defaults, bad_props, error = mutation_payloads._mkcalendar_props_from_payload(
            unsupported_payload,
            self._caldav_error_response,
        )
        self.assertIsNone(error)
        self.assertEqual(defaults["component_kind"], Calendar.COMPONENT_VEVENT)
        self.assertEqual(
            bad_props,
            [qname(NS_CALDAV, "supported-calendar-component-set")],
        )

    def test_proppatch_plan_skips_unknown_operations_and_missing_prop(self):
        root = ET.fromstring(
            """<?xml version='1.0'?>
<D:propertyupdate xmlns:D='DAV:'>
  <D:foo />
  <D:set />
</D:propertyupdate>
"""
        )
        pending_values, update_fields, ok_tags, bad_tags = (
            mutation_payloads._calendar_collection_proppatch_plan(
                root,
                "family",
                {
                    "name": "Family",
                    "description": "Old",
                    "timezone": "UTC",
                    "color": "#000",
                    "sort_order": 2,
                },
            )
        )
        self.assertEqual(pending_values["name"], "Family")
        self.assertEqual(update_fields, set())
        self.assertEqual(ok_tags, [])
        self.assertEqual(bad_tags, [])

    def test_proppatch_plan_sets_valid_timezone_and_other_values(self):
        root = ET.fromstring(
            """<?xml version='1.0'?>
<D:propertyupdate xmlns:D='DAV:' xmlns:C='urn:ietf:params:xml:ns:caldav' xmlns:A='http://apple.com/ns/ical/'>
  <D:set>
    <D:prop>
      <D:displayname>Family Home</D:displayname>
      <C:calendar-timezone>BEGIN:VTIMEZONE\nTZID:UTC\nEND:VTIMEZONE</C:calendar-timezone>
      <A:calendar-color>#123456</A:calendar-color>
      <A:calendar-order>8</A:calendar-order>
    </D:prop>
  </D:set>
</D:propertyupdate>
"""
        )
        pending_values, update_fields, ok_tags, bad_tags = (
            mutation_payloads._calendar_collection_proppatch_plan(
                root,
                "family",
                {
                    "name": "Family",
                    "description": "",
                    "timezone": "America/Chicago",
                    "color": "",
                    "sort_order": None,
                },
            )
        )
        self.assertEqual(pending_values["name"], "Family Home")
        self.assertEqual(pending_values["timezone"], "UTC")
        self.assertEqual(pending_values["color"], "#123456")
        self.assertEqual(pending_values["sort_order"], 8)
        self.assertEqual(
            update_fields,
            {
                "name",
                "timezone",
                "color",
                "sort_order",
            },
        )
        self.assertEqual(len(ok_tags), 4)
        self.assertEqual(bad_tags, [])

    def test_proppatch_plan_remove_and_reject_invalid_set_values(self):
        root = ET.fromstring(
            """<?xml version='1.0'?>
<D:propertyupdate xmlns:D='DAV:' xmlns:C='urn:ietf:params:xml:ns:caldav' xmlns:A='http://apple.com/ns/ical/'>
  <D:remove>
    <D:prop>
      <D:displayname />
      <C:calendar-timezone />
      <A:calendar-color />
      <A:calendar-order />
    </D:prop>
  </D:remove>
  <D:set>
    <D:prop>
      <C:calendar-timezone>BEGIN:VTIMEZONE\nTZID:No/Such_Zone\nEND:VTIMEZONE</C:calendar-timezone>
      <A:calendar-order>bad</A:calendar-order>
      <D:unknown />
    </D:prop>
  </D:set>
</D:propertyupdate>
"""
        )
        pending_values, update_fields, ok_tags, bad_tags = (
            mutation_payloads._calendar_collection_proppatch_plan(
                root,
                "family",
                {
                    "name": "Family",
                    "description": "Desc",
                    "timezone": "America/Chicago",
                    "color": "#000000",
                    "sort_order": 11,
                },
            )
        )
        self.assertEqual(pending_values["name"], "family")
        self.assertEqual(pending_values["timezone"], "UTC")
        self.assertEqual(pending_values["color"], "")
        self.assertIsNone(pending_values["sort_order"])
        self.assertEqual(update_fields, {"name", "timezone", "color", "sort_order"})
        self.assertEqual(len(ok_tags), 4)
        self.assertIn(qname(NS_CALDAV, "calendar-timezone"), bad_tags)
        self.assertIn(qname(NS_APPLE_ICAL, "calendar-order"), bad_tags)
        self.assertIn(qname(NS_DAV, "unknown"), bad_tags)
