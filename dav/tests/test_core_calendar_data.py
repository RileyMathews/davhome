from xml.etree import ElementTree as ET

from django.test import SimpleTestCase

from dav.core import calendar_data as core_calendar_data
from dav.core import recurrence as core_recurrence
from dav.core import time as core_time


class DavCoreCalendarDataTests(SimpleTestCase):
    def test_ensure_shifted_first_occurrence_recurrence_id_no_master_starts(self):
        ical_blob = (
            "BEGIN:VEVENT\r\nUID:evt\r\nDTSTART:20260220T100000Z\r\nEND:VEVENT\r\n"
        )
        self.assertEqual(
            core_calendar_data.ensure_shifted_first_occurrence_recurrence_id(
                ical_blob,
                {},
                None,
                core_recurrence.extract_component_blocks,
                core_time.first_ical_line_value,
                core_time.first_ical_line,
                core_time.format_value_date_or_datetime,
            ),
            ical_blob,
        )

    def test_ensure_shifted_first_occurrence_recurrence_id(self):
        shifted = core_calendar_data.ensure_shifted_first_occurrence_recurrence_id(
            "BEGIN:VEVENT\r\nUID:evt-3\r\nDTSTART:20260221T100000Z\r\nEND:VEVENT\r\n",
            {"evt-3": core_time.parse_ical_datetime("20260220T100000Z")},
            None,
            core_recurrence.extract_component_blocks,
            core_time.first_ical_line_value,
            core_time.first_ical_line,
            core_time.format_value_date_or_datetime,
        )
        self.assertIn("RECURRENCE-ID:20260221T100000Z", shifted)

    def test_ensure_shifted_first_occurrence_recurrence_id_date_line(self):
        shifted = core_calendar_data.ensure_shifted_first_occurrence_recurrence_id(
            "BEGIN:VEVENT\r\nUID:evt-date\r\nDTSTART;VALUE=DATE:20260221\r\nEND:VEVENT\r\n",
            {"evt-date": core_time.parse_ical_datetime("20260220T100000Z")},
            None,
            core_recurrence.extract_component_blocks,
            core_time.first_ical_line_value,
            core_time.first_ical_line,
            core_time.format_value_date_or_datetime,
        )
        self.assertIn("RECURRENCE-ID;VALUE=DATE:20260221", shifted)

    def test_ensure_shifted_first_occurrence_recurrence_id_guard_paths(self):
        unchanged = core_calendar_data.ensure_shifted_first_occurrence_recurrence_id(
            "BEGIN:VEVENT\r\nUID:evt\r\nRECURRENCE-ID:20260220T100000Z\r\nEND:VEVENT\r\n",
            {"evt": core_time.parse_ical_datetime("20260219T100000Z")},
            None,
            core_recurrence.extract_component_blocks,
            core_time.first_ical_line_value,
            core_time.first_ical_line,
            core_time.format_value_date_or_datetime,
        )
        self.assertIn("RECURRENCE-ID:20260220T100000Z", unchanged)

        unchanged = core_calendar_data.ensure_shifted_first_occurrence_recurrence_id(
            "BEGIN:VEVENT\r\nDTSTART:20260220T100000Z\r\nEND:VEVENT\r\n",
            {"evt": core_time.parse_ical_datetime("20260219T100000Z")},
            None,
            core_recurrence.extract_component_blocks,
            core_time.first_ical_line_value,
            core_time.first_ical_line,
            core_time.format_value_date_or_datetime,
        )
        self.assertNotIn("RECURRENCE-ID", unchanged)

        stub_extract = lambda _ical, _name: [
            "BEGIN:VEVENT\r\nUID:evt\r\nEND:VEVENT\r\n"
        ]
        unchanged = core_calendar_data.ensure_shifted_first_occurrence_recurrence_id(
            "dummy",
            {"evt": core_time.parse_ical_datetime("20260219T100000Z")},
            None,
            stub_extract,
            lambda _block, _key: "evt",
            lambda _block, _key: "DTSTART",
            core_time.format_value_date_or_datetime,
        )
        self.assertEqual(unchanged, "dummy")

        unchanged = core_calendar_data.ensure_shifted_first_occurrence_recurrence_id(
            "BEGIN:VEVENT\r\nUID:evt\r\nDTSTART:20260220T100000Z\r\nEND:VEVENT\r\n",
            {"evt": core_time.parse_ical_datetime("20260220T100000Z")},
            None,
            core_recurrence.extract_component_blocks,
            core_time.first_ical_line_value,
            core_time.first_ical_line,
            core_time.format_value_date_or_datetime,
        )
        self.assertNotIn("RECURRENCE-ID", unchanged)

        unchanged = core_calendar_data.ensure_shifted_first_occurrence_recurrence_id(
            "no-dtstart-here",
            {"evt": core_time.parse_ical_datetime("20260219T100000Z")},
            None,
            lambda _ical, _name: ["no-dtstart-here"],
            lambda _block, _key: "evt",
            lambda _block, _key: "DTSTART:20260220T100000Z",
            core_time.format_value_date_or_datetime,
        )
        self.assertEqual(unchanged, "no-dtstart-here")

    def test_filter_calendar_data_for_response_strips_dtstamp(self):
        calendar_data_request = ET.fromstring(
            '<C:calendar-data xmlns:C="urn:ietf:params:xml:ns:caldav"><C:prop name="SUMMARY"/></C:calendar-data>'
        )
        filtered = core_calendar_data.filter_calendar_data_for_response(
            "BEGIN:VCALENDAR\r\nDTSTAMP:20260220T100000Z\r\nEND:VCALENDAR\r\n",
            calendar_data_request,
            None,
            core_time.parse_ical_datetime,
            core_time.as_utc_datetime,
            lambda *args: args[0],
            lambda ical, *_: ical,
        )
        self.assertNotIn("DTSTAMP", filtered)
        self.assertTrue(filtered.endswith("\r\n"))

    def test_filter_calendar_data_for_response_no_request(self):
        ical_blob = "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"
        self.assertEqual(
            core_calendar_data.filter_calendar_data_for_response(
                ical_blob,
                None,
                None,
                core_time.parse_ical_datetime,
                core_time.as_utc_datetime,
                lambda *args: args[0],
                lambda ical, *_: ical,
            ),
            ical_blob,
        )

    def test_filter_calendar_data_for_response_expand_flow(self):
        calendar_data_request = ET.fromstring(
            '<C:calendar-data xmlns:C="urn:ietf:params:xml:ns:caldav">'
            '<C:expand start="20260220T000000Z" end="20260223T000000Z"/>'
            "</C:calendar-data>"
        )
        ical_blob = (
            "BEGIN:VCALENDAR\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:u1\r\n"
            "DTSTART:20260220T100000Z\r\n"
            "RRULE:FREQ=DAILY;COUNT=2\r\n"
            "EXDATE:20260220T100000Z\r\n"
            "END:VEVENT\r\n"
            "BEGIN:VTODO\r\n"
            "UID:t1\r\n"
            "DUE:20260221T120000Z\r\n"
            "END:VTODO\r\n"
            "END:VCALENDAR\r\n"
        )

        captured = {}

        class _FakeQuery:
            keep_recurrence_attributes = False

            def between(self, _start, _end):
                return ["expanded-a", "expanded-b"]

        original_recurring_of = core_calendar_data.recurring_of
        core_calendar_data.recurring_of = lambda _cal: _FakeQuery()
        try:
            filtered = core_calendar_data.filter_calendar_data_for_response(
                ical_blob,
                calendar_data_request,
                None,
                core_time.parse_ical_datetime,
                core_time.as_utc_datetime,
                lambda expanded, _tz, master_starts, excluded: (
                    captured.update(
                        {
                            "expanded": expanded,
                            "master_starts": master_starts,
                            "excluded": excluded,
                        }
                    )
                    or "BEGIN:VCALENDAR\r\nDTSTAMP:20260220T100000Z\r\nEND:VCALENDAR\r\n"
                ),
                lambda blob, *_: blob,
            )
        finally:
            core_calendar_data.recurring_of = original_recurring_of

        self.assertEqual(captured["expanded"], ["expanded-a", "expanded-b"])
        self.assertIn("u1", captured["master_starts"])
        self.assertIn("t1", captured["master_starts"])
        self.assertIn("u1", captured["excluded"])
        self.assertNotIn("DTSTAMP", filtered)
        self.assertTrue(filtered.endswith("\r\n"))

    def test_filter_calendar_data_for_response_expand_parse_error_is_tolerated(self):
        calendar_data_request = ET.fromstring(
            '<C:calendar-data xmlns:C="urn:ietf:params:xml:ns:caldav">'
            '<C:expand start="20260220T000000Z" end="20260223T000000Z"/>'
            "</C:calendar-data>"
        )

        filtered = core_calendar_data.filter_calendar_data_for_response(
            "broken-ical",
            calendar_data_request,
            None,
            core_time.parse_ical_datetime,
            core_time.as_utc_datetime,
            lambda *args: args[0],
            lambda ical, *_: ical,
        )
        self.assertTrue(filtered.endswith("\r\n"))
