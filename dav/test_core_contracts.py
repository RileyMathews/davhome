from datetime import datetime, timezone
from xml.etree import ElementTree as ET

from django.test import SimpleTestCase

from dav.core.contracts import (
    CalendarObjectData,
    ProtocolError,
    ReportRequest,
    ReportResult,
    TimeRange,
    WriteDecision,
    WritePrecondition,
)


class DavCoreContractTests(SimpleTestCase):
    def test_time_range_validates_bounds(self):
        start = datetime(2026, 2, 20, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 2, 20, 11, 0, tzinfo=timezone.utc)
        value = TimeRange(start=start, end=end)
        self.assertEqual(value.start, start)
        self.assertEqual(value.end, end)

        with self.assertRaisesMessage(
            ValueError,
            "TimeRange.end must be greater than TimeRange.start",
        ):
            TimeRange(start=end, end=start)

    def test_protocol_error_defaults_and_validation(self):
        err = ProtocolError(code="valid-sync-token")
        self.assertEqual(err.http_status, 403)
        self.assertEqual(err.namespace, "caldav")

        with self.assertRaisesMessage(
            ValueError,
            "ProtocolError.namespace must be 'caldav' or 'dav'",
        ):
            ProtocolError(code="x", namespace="foo")

        with self.assertRaisesMessage(
            ValueError,
            "ProtocolError.http_status must be an HTTP status",
        ):
            ProtocolError(code="x", http_status=42)

    def test_calendar_object_data_requires_core_fields(self):
        obj = CalendarObjectData(
            calendar_id="abc",
            owner_username="owner",
            slug="home",
            filename="event.ics",
            etag='"abc"',
            content_type="text/calendar",
            ical_blob="BEGIN:VCALENDAR\nEND:VCALENDAR\n",
        )
        self.assertEqual(obj.filename, "event.ics")

        with self.assertRaisesMessage(
            ValueError,
            "CalendarObjectData.filename must be non-empty",
        ):
            CalendarObjectData(
                calendar_id="abc",
                owner_username="owner",
                slug="home",
                filename="",
                etag='"abc"',
                content_type="text/calendar",
                ical_blob="BEGIN:VCALENDAR\nEND:VCALENDAR\n",
            )

    def test_write_precondition_normalizes_if_match(self):
        precondition = WritePrecondition(if_match=['"a"', '"b"'])
        self.assertEqual(precondition.if_match, ('"a"', '"b"'))

        with self.assertRaisesMessage(
            ValueError,
            "WritePrecondition.if_none_match must be '*' or None",
        ):
            WritePrecondition(if_none_match='"etag"')

    def test_write_decision_enforces_error_consistency(self):
        allowed = WriteDecision(allowed=True)
        self.assertIsNone(allowed.error)

        denied = WriteDecision(
            allowed=False,
            error=ProtocolError(
                code="precondition-failed", namespace="dav", http_status=412
            ),
        )
        self.assertEqual(denied.error.http_status, 412)

        with self.assertRaisesMessage(
            ValueError,
            "WriteDecision cannot have an error when allowed=True",
        ):
            WriteDecision(allowed=True, error=ProtocolError(code="x"))

        with self.assertRaisesMessage(
            ValueError,
            "WriteDecision must include error when allowed=False",
        ):
            WriteDecision(allowed=False)

    def test_report_request_and_result_normalize_sequences(self):
        report = ReportRequest(
            report_name="calendar-query",
            requested_props=["{DAV:}getetag"],
            hrefs=["/dav/calendars/owner/home/event.ics"],
        )
        self.assertEqual(report.requested_props, ("{DAV:}getetag",))
        self.assertEqual(report.hrefs, ("/dav/calendars/owner/home/event.ics",))

        with self.assertRaisesMessage(
            ValueError,
            "ReportRequest.report_name must be non-empty",
        ):
            ReportRequest(report_name="")

        response_elem = ET.Element("response")
        result = ReportResult(responses=[response_elem], sync_token="data:,abc/1")
        self.assertEqual(len(result.responses), 1)
        self.assertEqual(result.sync_token, "data:,abc/1")
