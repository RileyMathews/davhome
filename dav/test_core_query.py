from xml.etree import ElementTree as ET

from django.test import SimpleTestCase

from dav.core import filters as core_filters
from dav.core import query as core_query
from dav.core import recurrence as core_recurrence
from dav.core import time as core_time


class DavCoreQueryTests(SimpleTestCase):
    def test_matches_time_range_nonrecurring(self):
        component = "BEGIN:VEVENT\nDTSTART:20260220T100000Z\nEND:VEVENT\n"
        self.assertTrue(
            core_query.matches_time_range(
                component,
                {"start": "20260220T090000Z", "end": "20260220T110000Z"},
                core_time.parse_ical_datetime,
                lambda *_: False,
                core_recurrence.parse_line_datetime_with_tz,
                core_time.first_ical_line,
                core_time.parse_ical_duration,
                core_time.first_ical_line_value,
            )
        )

    def test_matches_comp_filter_and_object_matches_query(self):
        query_filter = ET.fromstring(
            '<C:comp-filter xmlns:C="urn:ietf:params:xml:ns:caldav" name="VEVENT"><C:prop-filter name="SUMMARY"><C:text-match>plan</C:text-match></C:prop-filter></C:comp-filter>'
        )
        text = "BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:Planning\nEND:VEVENT\nEND:VCALENDAR\n"
        self.assertTrue(
            core_query.matches_comp_filter(
                text,
                query_filter,
                core_recurrence.extract_component_blocks,
                lambda *_: True,
                lambda component, prop_filter: core_filters.matches_prop_filter(
                    component,
                    prop_filter,
                    lambda *_: True,
                ),
                lambda *_: False,
                core_filters.combine_filter_results,
            )
        )

        self.assertTrue(
            core_query.object_matches_query(
                text,
                None,
                core_time.unfold_ical,
                lambda *_: False,
            )
        )
