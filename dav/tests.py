# pyright: reportGeneralTypeIssues=false, reportAttributeAccessIssue=false, reportArgumentType=false, reportCallIssue=false, reportOperatorIssue=false

import base64
from xml.etree import ElementTree as ET

from django.contrib.auth.models import User
from django.test import TestCase

from calendars.models import Calendar, CalendarObject, CalendarShare


class DavDiscoveryTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner", password="pw-test-12345"
        )
        self.member = User.objects.create_user(
            username="member",
            password="pw-test-12345",
        )
        self.calendar = Calendar.objects.create(
            owner=self.owner,
            slug="family",
            name="Family",
            timezone="UTC",
        )
        CalendarShare.objects.create(
            calendar=self.calendar,
            user=self.member,
            role=CalendarShare.READ,
        )
        self.object = CalendarObject.objects.create(
            calendar=self.calendar,
            uid="uid-1",
            filename="event-1.ics",
            etag='"etag-1"',
            ical_blob="BEGIN:VCALENDAR\nEND:VCALENDAR\n",
            size=30,
        )

    def _basic_auth(self, username, password):
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode(
            "ascii"
        )
        return {"HTTP_AUTHORIZATION": f"Basic {token}"}

    def _xml(self, content):
        return ET.fromstring(content)

    def test_well_known_redirects(self):
        response = self.client.get("/.well-known/caldav")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers.get("Location"), "/dav/")

    def test_dav_root_requires_auth_for_propfind(self):
        response = self.client.generic(
            "PROPFIND", "/dav/", data="", content_type="application/xml"
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("Basic", response.headers.get("WWW-Authenticate", ""))

    def test_dav_root_options_advertises_dav(self):
        response = self.client.options("/dav/")
        self.assertEqual(response.status_code, 204)
        self.assertIn("calendar-access", response.headers.get("DAV", ""))

    def test_principal_propfind_includes_calendar_home_set(self):
        response = self.client.generic(
            "PROPFIND",
            f"/dav/principals/{self.owner.username}/",
            data="",
            content_type="application/xml",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 207)
        xml = self._xml(response.content)
        self.assertIn(
            f"/dav/calendars/{self.owner.username}/",
            ET.tostring(xml, encoding="unicode"),
        )

    def test_member_cannot_access_other_principal(self):
        response = self.client.generic(
            "PROPFIND",
            f"/dav/principals/{self.owner.username}/",
            data="",
            content_type="application/xml",
            **self._basic_auth("member", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 403)

    def test_shared_user_sees_shared_calendar_on_owner_home_depth1(self):
        response = self.client.generic(
            "PROPFIND",
            f"/dav/calendars/{self.owner.username}/",
            data="",
            content_type="application/xml",
            HTTP_DEPTH="1",
            **self._basic_auth("member", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 207)
        xml_text = response.content.decode("utf-8")
        self.assertIn(
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/", xml_text
        )

    def test_get_calendar_object_returns_ics_and_headers(self):
        response = self.client.get(
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/{self.object.filename}",
            **self._basic_auth("member", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("BEGIN:VCALENDAR", response.content.decode("utf-8"))
        self.assertEqual(response.headers.get("ETag"), self.object.etag)

    def test_propfind_requested_unknown_prop_returns_404_propstat(self):
        body = """<?xml version=\"1.0\" encoding=\"utf-8\"?>
<D:propfind xmlns:D=\"DAV:\">
  <D:prop>
    <D:displayname/>
    <D:made-up-prop/>
  </D:prop>
</D:propfind>"""
        response = self.client.generic(
            "PROPFIND",
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/",
            data=body,
            content_type="application/xml",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 207)
        xml_text = response.content.decode("utf-8")
        self.assertIn("404 Not Found", xml_text)


class DavWriteTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner", password="pw-test-12345"
        )
        self.writer = User.objects.create_user(
            username="writer",
            password="pw-test-12345",
        )
        self.reader = User.objects.create_user(
            username="reader",
            password="pw-test-12345",
        )
        self.calendar = Calendar.objects.create(
            owner=self.owner,
            slug="family",
            name="Family",
            timezone="UTC",
        )
        CalendarShare.objects.create(
            calendar=self.calendar,
            user=self.writer,
            role=CalendarShare.WRITE,
        )
        CalendarShare.objects.create(
            calendar=self.calendar,
            user=self.reader,
            role=CalendarShare.READ,
        )

    def _basic_auth(self, username, password):
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode(
            "ascii"
        )
        return {"HTTP_AUTHORIZATION": f"Basic {token}"}

    def _put_event(self, username, password, filename, body, **extra):
        return self.client.generic(
            "PUT",
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/{filename}",
            data=body,
            content_type="text/calendar; charset=utf-8",
            **self._basic_auth(username, password),
            **extra,
        )

    def test_owner_put_create_returns_201(self):
        body = "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:event-1\nEND:VEVENT\nEND:VCALENDAR\n"
        response = self._put_event("owner", "pw-test-12345", "event-1.ics", body)
        self.assertEqual(response.status_code, 201)

        obj = CalendarObject.objects.get(calendar=self.calendar, filename="event-1.ics")
        self.assertEqual(obj.uid, "event-1")
        self.assertEqual(response.headers.get("ETag"), obj.etag)

    def test_write_share_can_update_with_if_match(self):
        first = self._put_event(
            "owner",
            "pw-test-12345",
            "event-2.ics",
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:event-2\nEND:VEVENT\nEND:VCALENDAR\n",
        )
        self.assertEqual(first.status_code, 201)
        etag = first.headers.get("ETag")

        second = self._put_event(
            "writer",
            "pw-test-12345",
            "event-2.ics",
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:event-2\nSUMMARY:Updated\nEND:VEVENT\nEND:VCALENDAR\n",
            HTTP_IF_MATCH=etag,
        )
        self.assertEqual(second.status_code, 204)

    def test_put_rejects_stale_if_match(self):
        self._put_event(
            "owner",
            "pw-test-12345",
            "event-3.ics",
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:event-3\nEND:VEVENT\nEND:VCALENDAR\n",
        )
        stale = self._put_event(
            "writer",
            "pw-test-12345",
            "event-3.ics",
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:event-3\nSUMMARY:Bad\nEND:VEVENT\nEND:VCALENDAR\n",
            HTTP_IF_MATCH='"stale-etag"',
        )
        self.assertEqual(stale.status_code, 412)

    def test_put_rejects_if_none_match_on_existing(self):
        self._put_event(
            "owner",
            "pw-test-12345",
            "event-4.ics",
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:event-4\nEND:VEVENT\nEND:VCALENDAR\n",
        )
        response = self._put_event(
            "owner",
            "pw-test-12345",
            "event-4.ics",
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:event-4\nEND:VEVENT\nEND:VCALENDAR\n",
            HTTP_IF_NONE_MATCH="*",
        )
        self.assertEqual(response.status_code, 412)

    def test_read_share_cannot_put(self):
        response = self._put_event(
            "reader",
            "pw-test-12345",
            "event-5.ics",
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:event-5\nEND:VEVENT\nEND:VCALENDAR\n",
        )
        self.assertEqual(response.status_code, 403)

    def test_write_share_can_delete(self):
        self._put_event(
            "owner",
            "pw-test-12345",
            "event-6.ics",
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:event-6\nEND:VEVENT\nEND:VCALENDAR\n",
        )
        response = self.client.generic(
            "DELETE",
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/event-6.ics",
            **self._basic_auth("writer", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(
            CalendarObject.objects.filter(
                calendar=self.calendar,
                filename="event-6.ics",
            ).exists()
        )

    def test_put_without_uid_fails(self):
        response = self._put_event(
            "owner",
            "pw-test-12345",
            "event-7.ics",
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\nEND:VEVENT\nEND:VCALENDAR\n",
        )
        self.assertEqual(response.status_code, 400)
