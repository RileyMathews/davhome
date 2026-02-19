# pyright: reportAttributeAccessIssue=false, reportGeneralTypeIssues=false

from django.contrib.auth.models import User
from django.test import RequestFactory, SimpleTestCase, TestCase

from calendars.models import Calendar, CalendarObject
from dav.core.contracts import ProtocolError
from dav.shell.http import (
    protocol_error_to_http_response,
    write_precondition_from_request,
)
from dav.shell.repository import (
    calendar_object_to_data,
    list_calendar_object_data,
    list_calendar_object_data_for_calendars,
)


class DavShellHttpAdapterTests(SimpleTestCase):
    def test_write_precondition_from_request(self):
        request = RequestFactory().get(
            "/dav/",
            headers={
                "If-Match": '"a", "b"',
                "If-None-Match": "*",
            },
        )
        precondition = write_precondition_from_request(request, '"etag-1"')
        self.assertEqual(precondition.if_match, ('"a"', '"b"'))
        self.assertEqual(precondition.if_none_match, "*")
        self.assertEqual(precondition.existing_etag, '"etag-1"')

    def test_write_precondition_rejects_invalid_if_none_match(self):
        request = RequestFactory().get(
            "/dav/",
            headers={
                "If-None-Match": '"etag-2"',
            },
        )
        try:
            write_precondition_from_request(request, None)
            self.fail("Expected ValueError")
        except ValueError as exc:
            self.assertEqual(
                str(exc),
                "WritePrecondition.if_none_match must be '*' or None",
            )

    def test_protocol_error_to_http_response(self):
        response = protocol_error_to_http_response(
            ProtocolError(code="valid-sync-token", http_status=403),
        )
        self.assertEqual(response.status_code, 403)


class DavShellRepositoryAdapterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("owner", password="pw-test-12345")
        cls.calendar = Calendar.objects.create(
            owner=cls.owner,
            slug="home",
            name="Home",
        )
        cls.obj_a = CalendarObject.objects.create(
            calendar=cls.calendar,
            uid="a",
            filename="a.ics",
            etag='"etag-a"',
            ical_blob="BEGIN:VCALENDAR\nEND:VCALENDAR\n",
            content_type="text/calendar; charset=utf-8",
            size=34,
        )
        cls.obj_b = CalendarObject.objects.create(
            calendar=cls.calendar,
            uid="b",
            filename="b.ics",
            etag='"etag-b"',
            ical_blob="BEGIN:VCALENDAR\nEND:VCALENDAR\n",
            content_type="text/calendar; charset=utf-8",
            size=34,
        )

    def test_calendar_object_to_data_maps_fields(self):
        data = calendar_object_to_data(self.obj_a)
        self.assertEqual(data.calendar_id, str(self.calendar.id))
        self.assertEqual(data.owner_username, "owner")
        self.assertEqual(data.slug, "home")
        self.assertEqual(data.filename, "a.ics")
        self.assertEqual(data.etag, '"etag-a"')
        self.assertEqual(data.size, 34)

    def test_list_calendar_object_data_orders_by_filename(self):
        items = list_calendar_object_data(self.calendar)
        self.assertEqual([item.filename for item in items], ["a.ics", "b.ics"])

    def test_list_calendar_object_data_for_calendars(self):
        items = list_calendar_object_data_for_calendars([self.calendar])
        self.assertEqual(len(items), 2)
        self.assertEqual({item.slug for item in items}, {"home"})
