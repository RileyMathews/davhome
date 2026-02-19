from xml.etree import ElementTree as ET

from django.test import SimpleTestCase

from dav.core import calendar_data as core_calendar_data
from dav.core import recurrence as core_recurrence
from dav.core import time as core_time


class DavCoreCalendarDataTests(SimpleTestCase):
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
