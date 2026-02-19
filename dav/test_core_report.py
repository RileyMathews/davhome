from xml.etree import ElementTree as ET

from django.test import SimpleTestCase

from dav.core import report as core_report


class DavCoreReportTests(SimpleTestCase):
    def test_classify_report_kind(self):
        self.assertEqual(
            core_report.classify_report_kind(
                "{urn:ietf:params:xml:ns:caldav}calendar-query"
            ),
            core_report.REPORT_KIND_QUERY,
        )
        self.assertEqual(
            core_report.classify_report_kind("{DAV:}sync-collection"),
            core_report.REPORT_KIND_SYNC_COLLECTION,
        )
        self.assertEqual(
            core_report.classify_report_kind("{DAV:}nope"),
            core_report.REPORT_KIND_UNKNOWN,
        )

    def test_validate_time_range_payloads(self):
        root = ET.fromstring(
            '<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav"><C:filter><C:comp-filter name="VCALENDAR"><C:comp-filter name="VEVENT"><C:time-range start="20260220T000000Z" end="20260221T000000Z"/></C:comp-filter></C:comp-filter></C:filter></C:calendar-query>'
        )
        self.assertIsNone(
            core_report.validate_time_range_payloads(
                root,
                lambda value: value,
            )
        )

        bad = ET.fromstring(
            '<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav"><C:filter><C:comp-filter name="VCALENDAR"><C:comp-filter name="VEVENT"><C:time-range/></C:comp-filter></C:comp-filter></C:filter></C:calendar-query>'
        )
        self.assertEqual(
            core_report.validate_time_range_payloads(bad, lambda value: value),
            "bad-request",
        )

    def test_parse_sync_collection_request(self):
        root = ET.fromstring(
            '<D:sync-collection xmlns:D="DAV:"><D:sync-level>1</D:sync-level><D:sync-token>data:,abc/1</D:sync-token></D:sync-collection>'
        )
        value = core_report.parse_sync_collection_request(root, lambda _: 25)
        self.assertEqual(value.sync_level, "1")
        self.assertEqual(value.sync_token, "data:,abc/1")
        self.assertEqual(value.requested_limit, 25)

    def test_multiget_resolution_helpers(self):
        objects = [
            {"id": "a", "hrefs": ["/x/a", "/y/a"]},
            {"id": "b", "hrefs": ["/x/b"]},
        ]

        index = core_report.build_href_index(objects, lambda obj: obj["hrefs"])
        resolved = core_report.resolve_multiget_hrefs(
            ["x/a", "/x/missing"],
            index,
            lambda href: href if href.startswith("/") else f"/{href}",
        )
        self.assertEqual(resolved[0][0], "/x/a")
        self.assertEqual(resolved[0][1]["id"], "a")
        self.assertEqual(resolved[1][0], "/x/missing")
        self.assertIsNone(resolved[1][1])

    def test_select_query_objects(self):
        objects = [{"id": "a"}, {"id": "b"}]
        selected = core_report.select_query_objects(
            objects,
            "filter",
            lambda obj, query_filter: obj["id"] == "b" and query_filter == "filter",
        )
        self.assertEqual(selected, [{"id": "b"}])
