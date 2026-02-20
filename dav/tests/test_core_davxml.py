from datetime import datetime, timezone
from xml.etree import ElementTree as ET

from django.test import SimpleTestCase

from dav.core import davxml as core_davxml
from dav.xml import NS_CALDAV, NS_DAV, qname


class DavCoreDavXmlTests(SimpleTestCase):
    def test_error_responses(self):
        calls = []

        def xml_response(status, body):
            calls.append((status, body))
            return {"status": status}

        result = core_davxml.caldav_error_response(
            xml_response,
            qname,
            NS_DAV,
            NS_CALDAV,
            "valid-timezone",
        )
        self.assertEqual(result["status"], 403)
        self.assertEqual(calls[0][0], 403)

        result = core_davxml.dav_error_response(
            xml_response,
            qname,
            NS_DAV,
            "valid-sync-token",
            status=409,
        )
        self.assertEqual(result["status"], 409)

        result = core_davxml.valid_sync_token_error_response(
            xml_response,
            qname,
            NS_DAV,
        )
        self.assertEqual(result["status"], 403)

    def test_prop_helpers(self):
        owner = type("User", (), {"username": "alice"})()
        owner_prop = core_davxml.owner_prop(
            qname,
            NS_DAV,
            lambda user: f"/p/{user.username}/",
            owner,
        )
        self.assertEqual(owner_prop.tag, qname(NS_DAV, "owner"))

        privilege = core_davxml.current_user_privilege_set_prop(qname, NS_DAV, True)
        xml = ET.tostring(privilege, encoding="unicode")
        self.assertIn("current-user-privilege-set", xml)

        reports = core_davxml.supported_report_set_prop(
            qname,
            NS_DAV,
            NS_CALDAV,
            include_freebusy=True,
            include_sync_collection=True,
        )
        self.assertEqual(reports.tag, qname(NS_DAV, "supported-report-set"))

    def test_conditional_helpers(self):
        values = core_davxml.if_none_match_matches(
            '"a", "b"',
            lambda header: [item.strip() for item in header.split(",")],
            '"b"',
        )
        self.assertTrue(values)

        not_modified = core_davxml.if_modified_since_not_modified(
            "Thu, 20 Feb 2026 10:00:00 GMT",
            datetime(2026, 2, 20, 9, 0, tzinfo=timezone.utc).timestamp(),
        )
        self.assertTrue(not_modified)
