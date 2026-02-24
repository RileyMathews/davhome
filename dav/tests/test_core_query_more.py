from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

from django.test import SimpleTestCase

from dav.core import query as core_query


class DavCoreQueryMoreTests(SimpleTestCase):
    def test_matches_time_range_requires_start_or_end(self):
        result = core_query.matches_time_range(
            "BEGIN:VEVENT\nEND:VEVENT\n",
            {},
            lambda _: None,
            lambda *_: False,
            lambda *_: None,
            lambda *_: None,
            lambda *_: None,
            lambda *_: None,
        )
        self.assertFalse(result)

    def test_matches_time_range_vtodo_recurrence_due_promotes_to_vevent(self):
        seen = {}

        def recurrence(component_text, start, end, kind):
            seen["component_text"] = component_text
            seen["kind"] = kind
            return True

        result = core_query.matches_time_range(
            "BEGIN:VTODO\nRRULE:FREQ=DAILY\nDUE:20260221T100000Z\nEND:VTODO\n",
            {"start": "20260220T000000Z", "end": "20260225T000000Z"},
            lambda _: datetime(2026, 2, 20, tzinfo=timezone.utc),
            recurrence,
            lambda *_: None,
            lambda *_: None,
            lambda *_: None,
            lambda *_: None,
        )

        self.assertTrue(result)
        self.assertIn("BEGIN:VEVENT", seen["component_text"])
        self.assertEqual(seen["kind"], "VEVENT")

    def test_matches_time_range_rrule_uses_component_kind(self):
        seen = {}

        def recurrence(_component_text, _start, _end, kind):
            seen["kind"] = kind
            return True

        result = core_query.matches_time_range(
            "BEGIN:VTODO\nRRULE:FREQ=DAILY\nDTSTART:20260221T100000Z\nEND:VTODO\n",
            {"start": "20260220T000000Z", "end": "20260225T000000Z"},
            lambda _: datetime(2026, 2, 20, tzinfo=timezone.utc),
            recurrence,
            lambda *_: None,
            lambda *_: None,
            lambda *_: None,
            lambda *_: None,
        )

        self.assertTrue(result)
        self.assertEqual(seen["kind"], "VTODO")

    def test_matches_time_range_nonrecurring_edge_cases(self):
        start = datetime(2026, 2, 20, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 2, 21, 12, 0, tzinfo=timezone.utc)

        def parse_dt(value):
            if not value:
                return None
            if value == "20260219T000000Z":
                return datetime(2026, 2, 19, 0, 0, tzinfo=timezone.utc)
            if value == "20260221T120000Z":
                return end
            if value == "20260220T120000Z":
                return datetime(2026, 2, 20, 12, 0, tzinfo=timezone.utc)
            if value == "20260220T090000Z":
                return datetime(2026, 2, 20, 9, 0, tzinfo=timezone.utc)
            return start

        # Missing DTSTART and DUE returns True.
        no_start = core_query.matches_time_range(
            "BEGIN:VEVENT\nSUMMARY:X\nEND:VEVENT\n",
            {"start": "20260220T000000Z", "end": "20260221T000000Z"},
            parse_dt,
            lambda *_: False,
            lambda *_: None,
            lambda *_: "",
            lambda *_: None,
            lambda *_: "",
        )
        self.assertTrue(no_start)

        # Date DTSTART computes one-day default duration.
        date_event = core_query.matches_time_range(
            "BEGIN:VEVENT\nDTSTART;VALUE=DATE:20260220\nEND:VEVENT\n",
            {"start": "20260219T000000Z", "end": "20260221T120000Z"},
            parse_dt,
            lambda *_: False,
            lambda line: start if line else None,
            lambda _text, name: "DTSTART;VALUE=DATE:20260220"
            if name == "DTSTART"
            else None,
            lambda *_: None,
            lambda *_: "",
        )
        self.assertTrue(date_event)

        # Start/end exclusion checks.
        ends_before = core_query.matches_time_range(
            "BEGIN:VEVENT\nDTSTART:20260220T100000Z\nDTEND:20260220T110000Z\nEND:VEVENT\n",
            {"start": "20260220T120000Z"},
            parse_dt,
            lambda *_: False,
            lambda line: datetime(2026, 2, 20, 11, 0, tzinfo=timezone.utc)
            if line and "DTEND" in line
            else datetime(2026, 2, 20, 10, 0, tzinfo=timezone.utc),
            lambda text, name: name,
            lambda *_: timedelta(hours=1),
            lambda *_: "PT1H",
        )
        self.assertFalse(ends_before)

        starts_after = core_query.matches_time_range(
            "BEGIN:VEVENT\nDTSTART:20260220T100000Z\nEND:VEVENT\n",
            {"end": "20260220T090000Z"},
            parse_dt,
            lambda *_: False,
            lambda *_: datetime(2026, 2, 20, 10, 0, tzinfo=timezone.utc),
            lambda *_: "",
            lambda *_: None,
            lambda *_: "",
        )
        self.assertFalse(starts_after)

        self.assertIsNotNone(end)

    def test_matches_comp_filter_branches(self):
        context = (
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:Plan\nEND:VEVENT\nEND:VCALENDAR\n"
        )

        no_name = ET.fromstring(
            '<C:comp-filter xmlns:C="urn:ietf:params:xml:ns:caldav" />'
        )
        self.assertTrue(
            core_query.matches_comp_filter(
                context,
                no_name,
                lambda *_: [],
                lambda *_: True,
                lambda *_: True,
                lambda *_: False,
                lambda checks, _test: all(checks),
            )
        )

        is_not_defined = ET.fromstring(
            '<C:comp-filter xmlns:C="urn:ietf:params:xml:ns:caldav" name="VEVENT"><C:is-not-defined/></C:comp-filter>'
        )
        self.assertFalse(
            core_query.matches_comp_filter(
                context,
                is_not_defined,
                lambda *_: ["BEGIN:VEVENT\nEND:VEVENT\n"],
                lambda *_: True,
                lambda *_: True,
                lambda *_: False,
                lambda checks, _test: all(checks),
            )
        )

        quick_path = ET.fromstring(
            '<C:comp-filter xmlns:C="urn:ietf:params:xml:ns:caldav" name="VEVENT"><C:time-range start="20260220T000000Z"/></C:comp-filter>'
        )
        self.assertTrue(
            core_query.matches_comp_filter(
                context,
                quick_path,
                lambda *_: ["event"],
                lambda text, _range: text == context,
                lambda *_: True,
                lambda *_: False,
                lambda checks, _test: all(checks),
            )
        )

        no_alarm = ET.fromstring(
            '<C:comp-filter xmlns:C="urn:ietf:params:xml:ns:caldav" name="VEVENT"><C:comp-filter name="VALARM"><C:is-not-defined/></C:comp-filter></C:comp-filter>'
        )
        self.assertTrue(
            core_query.matches_comp_filter(
                context,
                no_alarm,
                lambda *_: ["BEGIN:VEVENT\nSUMMARY:No alarm\nEND:VEVENT\n"],
                lambda *_: True,
                lambda *_: True,
                lambda *_: False,
                lambda checks, _test: all(checks),
            )
        )

        valarm = ET.fromstring(
            '<C:comp-filter xmlns:C="urn:ietf:params:xml:ns:caldav" name="VALARM"><C:time-range start="20260220T000000Z"/></C:comp-filter>'
        )
        self.assertTrue(
            core_query.matches_comp_filter(
                context,
                valarm,
                lambda *_: ["BEGIN:VALARM\nEND:VALARM\n"],
                lambda *_: False,
                lambda *_: True,
                lambda *_: True,
                lambda checks, _test: all(checks),
            )
        )

    def test_matches_comp_filter_is_not_defined_child_uses_all(self):
        comp_filter = ET.fromstring(
            """
<C:comp-filter xmlns:C="urn:ietf:params:xml:ns:caldav" name="VTODO">
  <C:prop-filter name="SUMMARY" />
  <C:comp-filter name="VALARM"><C:is-not-defined/></C:comp-filter>
</C:comp-filter>
"""
        )

        result = core_query.matches_comp_filter(
            "BEGIN:VCALENDAR\nEND:VCALENDAR\n",
            comp_filter,
            lambda _context, name: ["ONE", "TWO"] if name == "VTODO" else [],
            lambda *_: True,
            lambda candidate, _prop_filter: candidate == "ONE",
            lambda *_: False,
            lambda checks, _test: all(checks),
        )
        self.assertFalse(result)

    def test_object_matches_query_delegates_filter(self):
        query_filter = ET.fromstring(
            '<C:comp-filter xmlns:C="urn:ietf:params:xml:ns:caldav" name="VCALENDAR"/>'
        )
        called = {}

        def matches(unfolded, passed_filter):
            called["unfolded"] = unfolded
            called["filter"] = passed_filter
            return True

        result = core_query.object_matches_query(
            "BEGIN:VCALENDAR\nEND:VCALENDAR\n",
            query_filter,
            lambda text: text + "UNFOLDED",
            matches,
        )

        self.assertTrue(result)
        self.assertTrue(called["unfolded"].endswith("UNFOLDED"))
        self.assertIs(called["filter"], query_filter)
