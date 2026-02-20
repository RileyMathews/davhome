from xml.etree import ElementTree as ET

from django.test import SimpleTestCase

from dav import xml


class DavXmlTests(SimpleTestCase):
    def test_qname_and_basic_response_builders(self):
        self.assertEqual(xml.qname(xml.NS_DAV, "href"), "{DAV:}href")

        response = xml.response_with_status("/dav/cal/", "404 Not Found")
        rendered = ET.tostring(response, encoding="unicode")

        self.assertIn("<D:href>/dav/cal/</D:href>", rendered)
        self.assertIn("<D:status>HTTP/1.1 404 Not Found</D:status>", rendered)

    def test_multistatus_document_wraps_responses(self):
        responses = [
            xml.response_with_status("/dav/a/", "200 OK"),
            xml.response_with_status("/dav/b/", "404 Not Found"),
        ]

        body = xml.multistatus_document(responses).decode("utf-8")
        self.assertIn("<?xml", body)
        self.assertIn("<D:multistatus", body)
        self.assertIn("/dav/a/", body)
        self.assertIn("/dav/b/", body)

    def test_response_with_props_emits_200_and_404_propstats(self):
        ok_prop = ET.Element(xml.qname(xml.NS_DAV, "displayname"))
        missing_prop = ET.Element(xml.qname(xml.NS_DAV, "getetag"))

        response = xml.response_with_props(
            "/dav/cal/",
            [ok_prop],
            [missing_prop],
        )
        rendered = ET.tostring(response, encoding="unicode")

        self.assertIn("HTTP/1.1 200 OK", rendered)
        self.assertIn("HTTP/1.1 404 Not Found", rendered)
        self.assertIn("displayname", rendered)
        self.assertIn("getetag", rendered)

    def test_parse_requested_properties_variants(self):
        self.assertIsNone(xml.parse_requested_properties(""))
        self.assertIsNone(xml.parse_requested_properties("<bad"))
        self.assertIsNone(
            xml.parse_requested_properties('<D:propfind xmlns:D="DAV:" />')
        )

        request = (
            '<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
            "<D:prop><D:getetag/><C:calendar-data/></D:prop>"
            "</D:propfind>"
        )
        self.assertEqual(
            xml.parse_requested_properties(request),
            [
                xml.qname(xml.NS_DAV, "getetag"),
                xml.qname(xml.NS_CALDAV, "calendar-data"),
            ],
        )

    def test_parse_propfind_request_variants(self):
        self.assertEqual(
            xml.parse_propfind_request(""),
            {"mode": "allprop", "requested": None},
        )
        self.assertEqual(xml.parse_propfind_request("<bad"), {"error": "malformed"})
        self.assertEqual(
            xml.parse_propfind_request('<D:not-propfind xmlns:D="DAV:" />'),
            {"error": "invalid-root"},
        )

        self.assertEqual(
            xml.parse_propfind_request(
                '<D:propfind xmlns:D="DAV:"><!--x--><D:allprop/></D:propfind>'
            ),
            {"mode": "allprop", "requested": None},
        )

        self.assertEqual(
            xml.parse_propfind_request(
                '<D:propfind xmlns:D="DAV:"><D:allprop/><D:propname/></D:propfind>'
            ),
            {"error": "invalid-body"},
        )
        self.assertEqual(
            xml.parse_propfind_request(
                '<D:propfind xmlns:D="DAV:"><D:propname/></D:propfind>'
            ),
            {"mode": "propname", "requested": None},
        )
        self.assertEqual(
            xml.parse_propfind_request(
                '<D:propfind xmlns:D="DAV:"><D:unknown/></D:propfind>'
            ),
            {"error": "invalid-body"},
        )

        result = xml.parse_propfind_request(
            '<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
            "<D:prop><D:getetag/><C:calendar-data/></D:prop>"
            "</D:propfind>"
        )
        self.assertEqual(
            result,
            {
                "mode": "prop",
                "requested": [
                    xml.qname(xml.NS_DAV, "getetag"),
                    xml.qname(xml.NS_CALDAV, "calendar-data"),
                ],
            },
        )
