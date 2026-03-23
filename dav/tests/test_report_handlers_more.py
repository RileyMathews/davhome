from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree as ET

from django.http import HttpResponse
from django.test import SimpleTestCase

import dav.reports.handlers as report_handlers
import dav.views.entrypoints as entrypoints  # noqa: F401
from dav.core import sync as core_sync


class DavReportHandlersMoreTests(SimpleTestCase):
    def test_tzinfo_from_report_timezone_text_paths(self):
        bad_payload = ET.fromstring(
            '<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav"><C:timezone>not-an-ical</C:timezone></C:calendar-query>'
        )
        tzinfo, error = report_handlers._tzinfo_from_report(bad_payload)
        self.assertIsNone(tzinfo)
        self.assertIsNotNone(error)
        self.assertIn("valid-calendar-data", error.content.decode("utf-8"))

        missing_tzid = ET.fromstring(
            '<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav"><C:timezone>BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR</C:timezone></C:calendar-query>'
        )
        tzinfo, error = report_handlers._tzinfo_from_report(missing_tzid)
        self.assertIsNone(tzinfo)
        self.assertIsNotNone(error)
        self.assertIn("valid-calendar-data", error.content.decode("utf-8"))

        bad_tzid = ET.fromstring(
            '<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav"><C:timezone>BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VTIMEZONE\nTZID:Bad/Timezone\nEND:VTIMEZONE\nEND:VCALENDAR</C:timezone></C:calendar-query>'
        )
        tzinfo, error = report_handlers._tzinfo_from_report(bad_tzid)
        self.assertIsNone(tzinfo)
        self.assertIsNotNone(error)
        self.assertIn("valid-calendar-data", error.content.decode("utf-8"))

    def test_object_matches_query_with_active_tz_invokes_wrappers(self):
        report_handlers._set_active_report_tzinfo(timezone.utc)

        query_filter = ET.fromstring(
            '<C:comp-filter xmlns:C="urn:ietf:params:xml:ns:caldav" name="VEVENT" />'
        )
        prop_filter = ET.fromstring(
            '<C:prop-filter xmlns:C="urn:ietf:params:xml:ns:caldav" name="SUMMARY"><C:text-match>x</C:text-match></C:prop-filter>'
        )

        def fake_matches_time_range(
            _component_text,
            _time_range,
            _parse_ical_datetime,
            matches_time_range_recurrence,
            parse_line_datetime_with_tz,
            _first_ical_line,
            _parse_ical_duration,
            _first_ical_line_value,
        ):
            parse_line_datetime_with_tz("DTSTART:20260220T100000Z")
            matches_time_range_recurrence(
                "BEGIN:VEVENT\nUID:x\nDTSTART:20260220T100000Z\nRRULE:FREQ=DAILY;COUNT=1\nEND:VEVENT\n",
                datetime(2026, 2, 20, 0, 0, tzinfo=timezone.utc),
                datetime(2026, 2, 21, 0, 0, tzinfo=timezone.utc),
                "VEVENT",
            )
            return True

        def fake_matches_comp_filter(
            _context_text,
            _comp_filter,
            _extract_component_blocks,
            matches_time_range,
            matches_prop_filter,
            alarm_matches_time_range,
            _combine_filter_results,
        ):
            matches_time_range(
                "BEGIN:VEVENT\nSUMMARY:x\nDTSTART:20260220T100000Z\nEND:VEVENT\n",
                {"start": "20260220T000000Z", "end": "20260221T000000Z"},
            )
            matches_prop_filter(
                "BEGIN:VEVENT\nSUMMARY:x\nEND:VEVENT\n",
                prop_filter,
            )
            alarm_matches_time_range(
                "BEGIN:VEVENT\nDTSTART:20260220T100000Z\nBEGIN:VALARM\nTRIGGER:-PT5M\nEND:VALARM\nEND:VEVENT\n",
                {"start": "20260220T095000Z", "end": "20260220T100000Z"},
            )
            return True

        def fake_object_matches_query(_ical_blob, _query_filter, _unfold_ical, matcher):
            return matcher("BEGIN:VEVENT\nUID:x\nEND:VEVENT\n", query_filter)

        with (
            patch.object(
                report_handlers.core_query,
                "matches_time_range",
                side_effect=fake_matches_time_range,
            ),
            patch.object(
                report_handlers.core_query,
                "matches_comp_filter",
                side_effect=fake_matches_comp_filter,
            ),
            patch.object(
                report_handlers.core_query,
                "object_matches_query",
                side_effect=fake_object_matches_query,
            ),
        ):
            obj = SimpleNamespace(ical_blob="BEGIN:VEVENT\nUID:x\nEND:VEVENT\n")
            self.assertTrue(
                report_handlers._object_matches_query_with_active_tz(obj, query_filter)
            )

        report_handlers._set_active_report_tzinfo(None)

    def test_render_freebusy_report_paths(self):
        missing_time_range = ET.fromstring(
            '<C:free-busy-query xmlns:C="urn:ietf:params:xml:ns:caldav" />'
        )
        self.assertEqual(
            report_handlers._render_freebusy_report([], missing_time_range).status_code,
            400,
        )

        bad_time_range = ET.fromstring(
            '<C:free-busy-query xmlns:C="urn:ietf:params:xml:ns:caldav"><C:time-range start="bad" end="also-bad"/></C:free-busy-query>'
        )
        self.assertEqual(
            report_handlers._render_freebusy_report([], bad_time_range).status_code,
            400,
        )

        class _Manager:
            def all(self):
                return [SimpleNamespace(ical_blob="BEGIN:VCALENDAR\nEND:VCALENDAR\n")]

        calendar = SimpleNamespace(timezone="UTC", calendar_objects=_Manager())
        ok_root = ET.fromstring(
            '<C:free-busy-query xmlns:C="urn:ietf:params:xml:ns:caldav"><C:time-range start="20260220T100000Z" end="20260220T110000Z"/></C:free-busy-query>'
        )
        response = report_handlers._render_freebusy_report([calendar], ok_root)
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/calendar", response.headers.get("Content-Type", ""))
        self.assertIn("BEGIN:VCALENDAR", response.content.decode("utf-8"))

    def test_handle_report_guardrail_paths(self):
        request = SimpleNamespace(body=b"<xml />", path="/dav/calendars/owner/family/")

        with patch.object(report_handlers, "parse_report_request", return_value=None):
            response = report_handlers._handle_report([], request)
        self.assertEqual(response.status_code, 400)

        root = ET.fromstring('<D:sync-collection xmlns:D="DAV:"/>')
        parsed = SimpleNamespace(
            root=root,
            requested_props=(),
            calendar_data_request=None,
            query_filter=None,
            hrefs=(),
        )

        with (
            patch.object(report_handlers, "parse_report_request", return_value=parsed),
            patch.object(
                report_handlers,
                "_tzinfo_from_report",
                return_value=(None, HttpResponse(status=403)),
            ),
        ):
            tz_response = report_handlers._handle_report([], request)
        self.assertEqual(tz_response.status_code, 403)

        with (
            patch.object(report_handlers, "parse_report_request", return_value=parsed),
            patch.object(
                report_handlers, "_tzinfo_from_report", return_value=(None, None)
            ),
            patch.object(
                report_handlers.core_report,
                "validate_time_range_payloads",
                return_value="bad-range",
            ),
        ):
            time_range_response = report_handlers._handle_report([], request)
        self.assertEqual(time_range_response.status_code, 400)

        with (
            patch.object(report_handlers, "parse_report_request", return_value=parsed),
            patch.object(
                report_handlers, "_tzinfo_from_report", return_value=(None, None)
            ),
            patch.object(
                report_handlers.core_report,
                "validate_time_range_payloads",
                return_value=None,
            ),
            patch.object(
                report_handlers.core_report,
                "validate_comp_filter_range_bounds",
                return_value="min-date-time",
            ),
        ):
            bounds_response = report_handlers._handle_report([], request)
        self.assertEqual(bounds_response.status_code, 403)

    def test_handle_report_dispatch_to_freebusy_and_sync_guardrails(self):
        freebusy_root = ET.fromstring(
            '<C:free-busy-query xmlns:C="urn:ietf:params:xml:ns:caldav"><C:time-range start="20260220T100000Z" end="20260220T110000Z"/></C:free-busy-query>'
        )
        parsed = SimpleNamespace(
            root=freebusy_root,
            requested_props=(),
            calendar_data_request=None,
            query_filter=None,
            hrefs=(),
        )
        request = SimpleNamespace(body=b"<xml />", path="/dav/calendars/owner/family/")

        freebusy_context = SimpleNamespace(
            calendars=[],
            requested_props=(),
            parsed_report=parsed,
            calendar_data_request=None,
            request_path=request.path,
            root=freebusy_root,
        )

        with (
            patch.object(report_handlers, "parse_report_request", return_value=parsed),
            patch.object(
                report_handlers, "_tzinfo_from_report", return_value=(None, None)
            ),
            patch.object(
                report_handlers.core_report,
                "validate_time_range_payloads",
                return_value=None,
            ),
            patch.object(
                report_handlers.core_report,
                "validate_comp_filter_range_bounds",
                return_value=None,
            ),
            patch.object(
                report_handlers.core_report_dispatch,
                "build_report_execution_context",
                return_value=freebusy_context,
            ),
            patch.object(
                report_handlers.core_report_dispatch,
                "dispatch_report",
                side_effect=lambda **kwargs: kwargs["handle_freebusy"](
                    freebusy_context
                ),
            ),
            patch.object(
                report_handlers,
                "_render_freebusy_report",
                return_value=HttpResponse(status=200),
            ) as render_freebusy,
        ):
            freebusy_response = report_handlers._handle_report([], request)
        self.assertEqual(freebusy_response.status_code, 200)
        self.assertTrue(render_freebusy.called)

        sync_root = ET.fromstring(
            '<D:sync-collection xmlns:D="DAV:"><D:sync-level>2</D:sync-level></D:sync-collection>'
        )
        sync_parsed = SimpleNamespace(
            root=sync_root,
            requested_props=(),
            calendar_data_request=None,
            query_filter=None,
            hrefs=(),
        )
        one_calendar_context = SimpleNamespace(
            calendars=[
                SimpleNamespace(
                    id="1", owner=SimpleNamespace(username="owner"), slug="family"
                )
            ],
            requested_props=(),
            parsed_report=sync_parsed,
            calendar_data_request=None,
            request_path=request.path,
            root=sync_root,
        )

        with (
            patch.object(
                report_handlers, "parse_report_request", return_value=sync_parsed
            ),
            patch.object(
                report_handlers, "_tzinfo_from_report", return_value=(None, None)
            ),
            patch.object(
                report_handlers.core_report,
                "validate_time_range_payloads",
                return_value=None,
            ),
            patch.object(
                report_handlers.core_report,
                "validate_comp_filter_range_bounds",
                return_value=None,
            ),
            patch.object(
                report_handlers.core_report_dispatch,
                "build_report_execution_context",
                return_value=one_calendar_context,
            ),
            patch.object(
                report_handlers.core_report_dispatch,
                "dispatch_report",
                side_effect=lambda **kwargs: kwargs["handle_sync_collection"](
                    one_calendar_context
                ),
            ),
        ):
            sync_level_response = report_handlers._handle_report(
                one_calendar_context.calendars,
                request,
            )
        self.assertEqual(sync_level_response.status_code, 400)

        sync_root_one = ET.fromstring(
            '<D:sync-collection xmlns:D="DAV:"><D:sync-level>1</D:sync-level></D:sync-collection>'
        )
        sync_parsed_one = SimpleNamespace(
            root=sync_root_one,
            requested_props=(),
            calendar_data_request=None,
            query_filter=None,
            hrefs=(),
        )
        two_calendar_context = SimpleNamespace(
            calendars=[
                SimpleNamespace(
                    id="1", owner=SimpleNamespace(username="owner"), slug="family"
                ),
                SimpleNamespace(
                    id="2", owner=SimpleNamespace(username="owner"), slug="work"
                ),
            ],
            requested_props=(),
            parsed_report=sync_parsed_one,
            calendar_data_request=None,
            request_path=request.path,
            root=sync_root_one,
        )

        with (
            patch.object(
                report_handlers, "parse_report_request", return_value=sync_parsed_one
            ),
            patch.object(
                report_handlers, "_tzinfo_from_report", return_value=(None, None)
            ),
            patch.object(
                report_handlers.core_report,
                "validate_time_range_payloads",
                return_value=None,
            ),
            patch.object(
                report_handlers.core_report,
                "validate_comp_filter_range_bounds",
                return_value=None,
            ),
            patch.object(
                report_handlers.core_report_dispatch,
                "build_report_execution_context",
                return_value=two_calendar_context,
            ),
            patch.object(
                report_handlers.core_report_dispatch,
                "dispatch_report",
                side_effect=lambda **kwargs: kwargs["handle_sync_collection"](
                    two_calendar_context
                ),
            ),
        ):
            many_calendar_response = report_handlers._handle_report(
                two_calendar_context.calendars,
                request,
            )
        self.assertEqual(many_calendar_response.status_code, 501)

    def test_sync_collection_limit_and_prop_map_edge_paths(self):
        empty_nresults = ET.fromstring(
            '<D:sync-collection xmlns:D="DAV:"><D:limit><D:nresults>   </D:nresults></D:limit></D:sync-collection>'
        )
        self.assertIsNone(report_handlers._sync_collection_limit(empty_nresults))

        bad_nresults = ET.fromstring(
            '<D:sync-collection xmlns:D="DAV:"><D:limit><D:nresults>NaN</D:nresults></D:limit></D:sync-collection>'
        )
        self.assertIsNone(report_handlers._sync_collection_limit(bad_nresults))

        obj = SimpleNamespace(
            ical_blob="BEGIN:VCALENDAR\nEND:VCALENDAR\n",
            etag='"etag"',
            content_type="text/calendar",
            uid="u1",
            filename="u1.ics",
        )
        prop_map = report_handlers._build_prop_map_for_object(obj)
        self.assertIn("{DAV:}getcontentlength", prop_map)
        self.assertIn("{DAV:}getlastmodified", prop_map)

    def test_filter_calendar_data_with_active_tz_calls_shift_helper(self):
        report_handlers._set_active_report_tzinfo(timezone.utc)

        def fake_filter(
            _ical_blob,
            _calendar_data_request,
            _active_tz,
            _parse_ical_datetime,
            _as_utc_datetime,
            _serialize_expanded_components,
            ensure_shifted_recurrence_id,
        ):
            ensure_shifted_recurrence_id(
                "BEGIN:VEVENT\nUID:u1\nEND:VEVENT\n",
                {},
                timezone.utc,
            )
            return "BEGIN:VCALENDAR\nEND:VCALENDAR\n"

        with patch.object(
            report_handlers.core_calendar_data,
            "filter_calendar_data_for_response",
            side_effect=fake_filter,
        ):
            output = report_handlers._filter_calendar_data_with_active_tz(
                "BEGIN:VCALENDAR\nEND:VCALENDAR\n",
                None,
            )
        self.assertIn("BEGIN:VCALENDAR", output)
        report_handlers._set_active_report_tzinfo(None)

    def test_sync_collection_response_handles_empty_and_missing_items(self):
        class _Manager:
            def __init__(self, objs):
                self._objs = objs

            def all(self):
                return self._objs

            def filter(self, filename__in):
                return [obj for obj in self._objs if obj.filename in filename__in]

        existing = SimpleNamespace(
            filename="present.ics",
            ical_blob="BEGIN:VCALENDAR\nEND:VCALENDAR\n",
            etag='"etag"',
            content_type="text/calendar",
            uid="u-present",
            updated_at=None,
            size=20,
        )
        calendar = SimpleNamespace(
            id="11111111-1111-1111-1111-111111111111",
            owner=SimpleNamespace(username="owner"),
            slug="family",
            calendar_objects=_Manager([existing]),
        )

        selection_initial = core_sync.SyncSelection(
            source="initial-current-objects",
            next_revision=0,
            items=(
                core_sync.SyncSelectedItem(
                    revision=None,
                    filename="deleted.ics",
                    is_deleted=True,
                ),
                core_sync.SyncSelectedItem(
                    revision=None,
                    filename="present.ics",
                    is_deleted=False,
                ),
            ),
        )

        with (
            patch.object(report_handlers, "_latest_sync_revision", return_value=0),
            patch.object(
                report_handlers.CalendarObjectChange.objects,
                "filter",
                return_value=SimpleNamespace(order_by=lambda *_: []),
            ),
            patch.object(
                report_handlers.core_propmap,
                "build_calendar_collection_prop_map",
                return_value={"{DAV:}getetag": '"cal"'},
            ),
            patch.object(
                report_handlers.core_sync,
                "select_sync_collection_items",
                return_value=selection_initial,
            ),
        ):
            initial_response = report_handlers._sync_collection_response(
                calendar,
                "/dav/calendars/owner/family/",
                [],
                None,
                None,
                None,
            )
        self.assertEqual(initial_response.status_code, 207)
        self.assertIn("200 OK", initial_response.content.decode("utf-8"))

        selection_incremental = core_sync.SyncSelection(
            source="incremental-latest-by-filename",
            next_revision=2,
            items=(
                core_sync.SyncSelectedItem(
                    revision=2,
                    filename="missing.ics",
                    is_deleted=False,
                ),
            ),
        )
        with (
            patch.object(report_handlers, "_latest_sync_revision", return_value=2),
            patch.object(
                report_handlers.CalendarObjectChange.objects,
                "filter",
                return_value=SimpleNamespace(order_by=lambda *_: []),
            ),
            patch.object(
                report_handlers.core_sync,
                "select_sync_collection_items",
                return_value=selection_incremental,
            ),
        ):
            incremental_response = report_handlers._sync_collection_response(
                calendar,
                "/dav/calendars/owner/family/",
                ["{DAV:}getetag"],
                None,
                1,
                None,
            )
        self.assertEqual(incremental_response.status_code, 207)
        self.assertIn("404 Not Found", incremental_response.content.decode("utf-8"))
