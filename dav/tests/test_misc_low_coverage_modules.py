from types import SimpleNamespace
from xml.etree import ElementTree as ET

from django.contrib.auth.models import AnonymousUser, User
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase

from calendars.models import Calendar
from dav.cbv import root as cbv_root
from dav.core import davxml as core_davxml
from dav.core import filters as core_filters
from dav.core import payloads as core_payloads
from dav.core import propmap as core_propmap
from dav.core import report as core_report
from dav.core import report_dispatch as core_report_dispatch
from dav.core import time as core_time
from dav.core.contracts import ProtocolError
from dav.middleware import (
    DavBasicAuthMiddleware,
    _allow_values,
    _client_ip,
)
from dav.reports.engine import parse_report_request
from dav.shell.http import (
    protocol_error_to_http_response,
    write_precondition_from_request,
)
from dav.shell.repository import list_calendar_object_data_for_calendars
from dav.views.mixins import GuidToUsernameDispatchMixin
from dav.xml import NS_CALDAV, NS_DAV, qname


class DavCommonAndCoreHelpersTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="u1", password="pw-test-12345")

    def test_common_home_etag_empty_and_propfind_payload_errors(self):
        from dav.views.calendar_collection import CalendarCollectionView  # noqa: F401
        from dav import common as dav_common

        etag, ts = dav_common._home_etag_and_timestamp(self.user, self.user)
        self.assertEqual(etag, '"home-empty"')
        self.assertIsInstance(ts, float)

        rf = RequestFactory()
        request = rf.generic(
            "PROPFIND", "/dav/", data=b"", content_type="application/xml"
        )

        with self.settings():
            pass

        with self.subTest("payload parse error"):
            orig = dav_common.parse_propfind_request
            dav_common.parse_propfind_request = lambda _body: {"error": "bad"}
            try:
                parsed, error = dav_common._parse_propfind_payload(request)
            finally:
                dav_common.parse_propfind_request = orig
            self.assertIsNone(parsed)
            self.assertEqual(error.status_code, 400)

        with self.subTest("invalid depth"):
            request_bad_depth = rf.generic(
                "PROPFIND",
                "/dav/",
                data=b"",
                content_type="application/xml",
                HTTP_DEPTH="2",
            )
            orig = dav_common.parse_propfind_request
            dav_common.parse_propfind_request = lambda _body: {
                "mode": "allprop",
                "requested": None,
            }
            try:
                parsed, error = dav_common._parse_propfind_payload(request_bad_depth)
            finally:
                dav_common.parse_propfind_request = orig
            self.assertIsNone(parsed)
            self.assertEqual(error.status_code, 400)

    def test_conditional_not_modified_if_modified_since_true(self):
        from dav.views.calendar_collection import CalendarCollectionView  # noqa: F401
        from dav import common as dav_common

        request = RequestFactory().get(
            "/dav/", HTTP_IF_MODIFIED_SINCE="Wed, 21 Oct 2015 07:28:00 GMT"
        )
        result = dav_common._conditional_not_modified(request, '"etag"', 1445412480)
        self.assertTrue(result)

    def test_davxml_and_filter_payload_helpers(self):
        self.assertFalse(core_davxml.if_modified_since_not_modified("not-a-date", 1))
        self.assertFalse(core_davxml.if_modified_since_not_modified(None, 1))
        self.assertTrue(
            core_davxml.if_modified_since_not_modified(
                "Wed, 21 Oct 2015 07:28:00", 1445412480
            )
        )

        matcher = ET.fromstring(
            '<C:text-match xmlns:C="urn:ietf:params:xml:ns:caldav" match-type="equals">abc</C:text-match>'
        )
        self.assertTrue(core_filters.text_match("abc", matcher))

        self.assertIsNone(
            core_payloads.component_kind_from_payload(
                "BEGIN:VCALENDAR\nEND:VCALENDAR\n"
            )
        )
        self.assertEqual(
            core_payloads.validate_generic_payload(b"\xff")[1],
            "Generic DAV payload must be UTF-8 text.",
        )

        req = RequestFactory().put("/dav/", HTTP_IF_MATCH='"x"')
        self.assertTrue(core_payloads.precondition_failed_for_write(req, None))

    def test_propmap_object_and_root_unauthenticated(self):
        unauth_map = core_propmap.build_root_unauthenticated_prop_map()
        elem = unauth_map[qname(NS_DAV, "current-user-principal")]()
        self.assertIsNotNone(elem.find(qname(NS_DAV, "unauthenticated")))

        obj = SimpleNamespace(
            ical_blob="abc",
            content_type="text/plain",
            dead_properties={"{DAV:}x": "<broken"},
        )
        prop_map = core_propmap.build_object_prop_map(
            obj=obj,
            etag_for_object=lambda _obj: '"e"',
            getlastmodified_text="Mon, 01 Jan 2024 00:00:00 GMT",
            calendar_data_element=ET.Element(qname(NS_CALDAV, "calendar-data")),
        )
        length = prop_map[qname(NS_DAV, "getcontentlength")]()
        self.assertEqual(length.text, "3")
        dead = prop_map["{DAV:}x"]()
        self.assertEqual(dead.tag, qname(NS_DAV, "invalid-dead-property"))


class DavDispatchAndShellTests(SimpleTestCase):
    def test_report_helpers_and_dispatch(self):
        self.assertEqual(
            core_report.classify_report_kind(qname(NS_CALDAV, "free-busy-query")),
            core_report.REPORT_KIND_FREEBUSY,
        )

        root = ET.fromstring(
            '<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav"><C:time-range end="bad"/></C:calendar-query>'
        )
        self.assertEqual(
            core_report.validate_time_range_payloads(
                root, core_time.parse_ical_datetime
            ),
            "bad-request",
        )

        bounds = ET.fromstring(
            '<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav"><C:comp-filter name="VEVENT"><C:time-range end="19000101T000000Z"/></C:comp-filter></C:calendar-query>'
        )
        self.assertEqual(
            core_report.validate_comp_filter_range_bounds(
                bounds, core_time.parse_ical_datetime, 2026
            ),
            "min-date-time",
        )

        parsed = SimpleNamespace(
            root=SimpleNamespace(tag=qname(NS_CALDAV, "free-busy-query")),
            requested_props=None,
            calendar_data_request=None,
        )
        context = core_report_dispatch.build_report_execution_context(
            parsed_report=parsed,
            calendars=[],
            request_path="/dav/",
            classify_report_kind=core_report.classify_report_kind,
        )
        got = core_report_dispatch.dispatch_report(
            context=context,
            report_kind_multiget=core_report.REPORT_KIND_MULTIGET,
            report_kind_query=core_report.REPORT_KIND_QUERY,
            report_kind_freebusy=core_report.REPORT_KIND_FREEBUSY,
            report_kind_sync_collection=core_report.REPORT_KIND_SYNC_COLLECTION,
            handle_multiget=lambda _ctx: "m",
            handle_query=lambda _ctx: "q",
            handle_freebusy=lambda _ctx: "f",
            handle_sync_collection=lambda _ctx: "s",
            handle_unknown=lambda _ctx: "u",
        )
        self.assertEqual(got, "f")

    def test_shell_and_identity_helpers(self):
        req = RequestFactory().put("/dav/", HTTP_IF_NONE_MATCH="   ")
        precondition = write_precondition_from_request(req, None)
        self.assertIsNone(precondition.if_none_match)

        response = protocol_error_to_http_response(
            ProtocolError(code="x", http_status=409, namespace="dav")
        )
        self.assertEqual(response.status_code, 409)

        self.assertIsNone(GuidToUsernameDispatchMixin().guid_to_username("not-a-guid"))

    def test_middleware_helpers_and_basic_auth(self):
        req = RequestFactory().get("/dav/", HTTP_X_FORWARDED_FOR="1.1.1.1, 2.2.2.2")
        self.assertEqual(_client_ip(req), "1.1.1.1")
        self.assertIsNone(_allow_values(None))

        middleware = DavBasicAuthMiddleware(lambda request: HttpResponse(status=200))

        req_auth = RequestFactory().get("/dav/")
        req_auth.user = SimpleNamespace(is_authenticated=True)
        resolved = middleware._resolve_dav_user(req_auth)
        self.assertTrue(resolved.is_authenticated)

        req_bad = RequestFactory().get("/dav/", HTTP_AUTHORIZATION="Basic ###")
        req_bad.user = AnonymousUser()
        self.assertIsNone(middleware._resolve_dav_user(req_bad))

    def test_report_engine_and_exports_modules(self):
        self.assertIsNone(parse_report_request(b"<not xml"))
        self.assertIn("DavRootView", cbv_root.__all__)


class DavRepositoryTests(TestCase):
    def test_repository_empty_calendar_list(self):
        self.assertEqual(list_calendar_object_data_for_calendars([]), [])
