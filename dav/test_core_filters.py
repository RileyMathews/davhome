from xml.etree import ElementTree as ET

from django.test import SimpleTestCase

from dav.core import filters as core_filters


class DavCoreFilterTests(SimpleTestCase):
    def test_parse_property_params_and_property_lines(self):
        line = "ATTENDEE;ROLE=REQ-PARTICIPANT;CUTYPE=INDIVIDUAL:mailto:a@example.com"
        params = core_filters.parse_property_params(line)
        self.assertEqual(params["ROLE"], ["REQ-PARTICIPANT"])
        self.assertEqual(params["CUTYPE"], ["INDIVIDUAL"])

        component = "SUMMARY:One\nSUMMARY;LANG=en:Two\nUID:1\n"
        lines = core_filters.property_lines(component, "summary")
        self.assertEqual(lines, ["SUMMARY:One", "SUMMARY;LANG=en:Two"])

        params = core_filters.parse_property_params("SUMMARY;LANGUAGE:Hello")
        self.assertEqual(params, {})

    def test_text_match_and_combine(self):
        matcher = ET.fromstring(
            '<C:text-match xmlns:C="urn:ietf:params:xml:ns:caldav" match-type="starts-with">hel</C:text-match>'
        )
        self.assertTrue(core_filters.text_match("Hello", matcher))

        negate = ET.fromstring(
            '<C:text-match xmlns:C="urn:ietf:params:xml:ns:caldav" negate-condition="yes">zzz</C:text-match>'
        )
        self.assertTrue(core_filters.text_match("Hello", negate))

        ends_with = ET.fromstring(
            '<C:text-match xmlns:C="urn:ietf:params:xml:ns:caldav" match-type="ends-with" collation="i;octet">lo</C:text-match>'
        )
        self.assertTrue(core_filters.text_match("Hello", ends_with))
        self.assertFalse(core_filters.text_match(None, ends_with))

        self.assertTrue(core_filters.combine_filter_results([True, False], "anyof"))
        self.assertFalse(core_filters.combine_filter_results([True, False], "allof"))

    def test_matches_param_filter(self):
        prop_lines = [
            "ATTENDEE;ROLE=REQ-PARTICIPANT:mailto:a@example.com",
            "ATTENDEE;ROLE=OPT-PARTICIPANT:mailto:b@example.com",
        ]
        role_filter = ET.fromstring(
            '<C:param-filter xmlns:C="urn:ietf:params:xml:ns:caldav" name="ROLE"><C:text-match>REQ</C:text-match></C:param-filter>'
        )
        self.assertTrue(core_filters.matches_param_filter(prop_lines, role_filter))

        missing_filter = ET.fromstring(
            '<C:param-filter xmlns:C="urn:ietf:params:xml:ns:caldav" name="CUTYPE"><C:is-not-defined/></C:param-filter>'
        )
        self.assertTrue(core_filters.matches_param_filter(prop_lines, missing_filter))

        no_name_filter = ET.fromstring(
            '<C:param-filter xmlns:C="urn:ietf:params:xml:ns:caldav" />'
        )
        self.assertTrue(core_filters.matches_param_filter(prop_lines, no_name_filter))

        no_text = ET.fromstring(
            '<C:param-filter xmlns:C="urn:ietf:params:xml:ns:caldav" name="CUTYPE" />'
        )
        self.assertFalse(core_filters.matches_param_filter(prop_lines, no_text))

    def test_matches_prop_filter(self):
        component = (
            "BEGIN:VEVENT\n"
            "SUMMARY:Planning\n"
            "ATTENDEE;ROLE=REQ-PARTICIPANT:mailto:a@example.com\n"
            "DTSTART:20260220T100000Z\n"
            "END:VEVENT\n"
        )

        summary_filter = ET.fromstring(
            '<C:prop-filter xmlns:C="urn:ietf:params:xml:ns:caldav" name="SUMMARY"><C:text-match>plan</C:text-match></C:prop-filter>'
        )
        self.assertTrue(
            core_filters.matches_prop_filter(component, summary_filter, lambda *_: True)
        )

        description_missing = ET.fromstring(
            '<C:prop-filter xmlns:C="urn:ietf:params:xml:ns:caldav" name="DESCRIPTION"><C:is-not-defined/></C:prop-filter>'
        )
        self.assertTrue(
            core_filters.matches_prop_filter(
                component,
                description_missing,
                lambda *_: False,
            )
        )

        allof_time_fail = ET.fromstring(
            '<C:prop-filter xmlns:C="urn:ietf:params:xml:ns:caldav" name="DTSTART" test="allof"><C:time-range start="20260220T120000Z" end="20260220T130000Z"/></C:prop-filter>'
        )
        self.assertFalse(
            core_filters.matches_prop_filter(
                component,
                allof_time_fail,
                lambda *_: False,
            )
        )
