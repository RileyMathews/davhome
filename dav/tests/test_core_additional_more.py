from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from xml.etree import ElementTree as ET

from django.test import SimpleTestCase

from dav.core import contracts as core_contracts
from dav.core import davxml as core_davxml
from dav.core import filters as core_filters
from dav.core import query as core_query
from dav.core import report as core_report
from dav.core import time as core_time
from dav.core import write_ops as core_write_ops


class DavCoreAdditionalMoreTests(SimpleTestCase):
    def test_contracts_and_write_ops_invalid_paths(self):
        with self.assertRaises(ValueError):
            core_contracts.ProtocolError(code="")

        with self.assertRaises(ValueError):
            core_contracts.CalendarObjectData(
                calendar_id="c",
                owner_username="o",
                slug="s",
                filename="f",
                etag="e",
                content_type="t",
                ical_blob="x",
                size=-1,
            )

        decision = core_write_ops.decide_precondition(
            core_write_ops.build_write_precondition(
                if_match_header='"e1"',
                if_none_match_header=None,
                existing_etag=None,
                parse_if_match_values=lambda header: [header],
            )
        )
        self.assertFalse(decision.allowed)

    def test_time_helpers_extra_branches(self):
        self.assertEqual(core_time.format_ical_duration(timedelta(seconds=45)), "PT45S")
        self.assertEqual(core_time.format_ical_duration(timedelta(days=1)), "P1D")

        as_date, is_date = core_time.format_value_date_or_datetime(
            datetime(2026, 2, 20, 10, 0, tzinfo=timezone.utc),
            tzinfo=ZoneInfo("America/New_York"),
        )
        self.assertFalse(is_date)
        self.assertEqual(as_date, "20260220T100000Z")

        date_only, is_date_only = core_time.format_value_date_or_datetime(
            datetime(2026, 2, 20, 0, 0, tzinfo=timezone.utc).date(),
            tzinfo=ZoneInfo("America/New_York"),
        )
        self.assertTrue(is_date_only)
        self.assertEqual(date_only, "20260219")

    def test_davxml_and_report_bounds(self):
        with self.subTest("if-modified-since parsed None"):
            original = core_davxml.parsedate_to_datetime
            core_davxml.parsedate_to_datetime = lambda _v: None
            try:
                self.assertFalse(core_davxml.if_modified_since_not_modified("x", 1))
            finally:
                core_davxml.parsedate_to_datetime = original

        root = ET.fromstring(
            '<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav"><C:comp-filter name="VEVENT"><C:time-range end="20330101T000000Z"/></C:comp-filter></C:calendar-query>'
        )
        result = core_report.validate_comp_filter_range_bounds(
            root,
            core_time.parse_ical_datetime,
            2026,
        )
        self.assertEqual(result, "max-date-time")

    def test_filters_and_query_extra_paths(self):
        param_filter = ET.fromstring(
            '<C:param-filter xmlns:C="urn:ietf:params:xml:ns:caldav" name="TZID"><C:text-match>UTC</C:text-match></C:param-filter>'
        )
        self.assertFalse(core_filters.matches_param_filter([], param_filter))

        empty_prop_filter = ET.fromstring(
            '<C:prop-filter xmlns:C="urn:ietf:params:xml:ns:caldav" />'
        )
        self.assertTrue(
            core_filters.matches_prop_filter(
                "BEGIN:VEVENT\nEND:VEVENT\n", empty_prop_filter, lambda *_: True
            )
        )

        self.assertEqual(
            core_query._component_kind_for_recurrence("BEGIN:VEVENT"), "VEVENT"
        )
        self.assertEqual(
            core_query._calculate_event_end(
                "BEGIN:VEVENT\nDTSTART;VALUE=DATE:20260220\nEND:VEVENT\n",
                datetime(2026, 2, 20, tzinfo=timezone.utc),
                datetime(2026, 2, 20, 1, 0, tzinfo=timezone.utc),
                None,
                lambda text, key: "DTSTART;VALUE=DATE:20260220"
                if key == "DTSTART"
                else None,
            ),
            datetime(2026, 2, 20, 1, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(
            core_query._calculate_event_end(
                "",
                datetime(2026, 2, 20, tzinfo=timezone.utc),
                None,
                timedelta(hours=2),
                lambda *_: None,
            ),
            datetime(2026, 2, 20, 2, 0, tzinfo=timezone.utc),
        )

        comp_filter = ET.fromstring(
            '<C:comp-filter xmlns:C="urn:ietf:params:xml:ns:caldav" name="VEVENT"><C:comp-filter name="VALARM"><C:is-not-defined/></C:comp-filter></C:comp-filter>'
        )
        self.assertFalse(
            core_query.matches_comp_filter(
                "BEGIN:VCALENDAR\nEND:VCALENDAR\n",
                comp_filter,
                lambda _text, name: [] if name == "VEVENT" else [],
                lambda *_: True,
                lambda *_: True,
                lambda *_: False,
                core_filters.combine_filter_results,
            )
        )

        # Candidate-level time-range evaluation path.
        comp_filter_time = ET.fromstring(
            '<C:comp-filter xmlns:C="urn:ietf:params:xml:ns:caldav" name="VTODO"><C:time-range start="20260220T000000Z"/></C:comp-filter>'
        )
        self.assertTrue(
            core_query.matches_comp_filter(
                "BEGIN:VCALENDAR\nEND:VCALENDAR\n",
                comp_filter_time,
                lambda _text, name: ["candidate"] if name == "VTODO" else [],
                lambda candidate, _time_range: candidate == "candidate",
                lambda *_: True,
                lambda *_: False,
                core_filters.combine_filter_results,
            )
        )

        # Empty iterable after truthy candidates reaches candidate_results fallback.
        class _TruthyEmpty:
            def __bool__(self):
                return True

            def __iter__(self):
                return iter(())

        self.assertFalse(
            core_query.matches_comp_filter(
                "BEGIN:VCALENDAR\nEND:VCALENDAR\n",
                ET.fromstring(
                    '<C:comp-filter xmlns:C="urn:ietf:params:xml:ns:caldav" name="VTODO" />'
                ),
                lambda *_: _TruthyEmpty(),
                lambda *_: True,
                lambda *_: True,
                lambda *_: False,
                core_filters.combine_filter_results,
            )
        )
