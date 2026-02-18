# pyright: reportGeneralTypeIssues=false, reportAttributeAccessIssue=false, reportArgumentType=false, reportCallIssue=false, reportOperatorIssue=false

import base64
from xml.etree import ElementTree as ET

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from calendars.models import (
    Calendar,
    CalendarObject,
    CalendarObjectChange,
    CalendarShare,
)


class DavDiscoveryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(username="owner", password="pw-test-12345")
        cls.member = User.objects.create_user(
            username="member",
            password="pw-test-12345",
        )
        cls.writer = User.objects.create_user(
            username="writer",
            password="pw-test-12345",
        )
        cls.calendar = Calendar.objects.create(
            owner=cls.owner,
            slug="family",
            name="Family",
            timezone="UTC",
        )
        CalendarShare.objects.create(
            calendar=cls.calendar,
            user=cls.member,
            role=CalendarShare.READ,
            accepted_at=timezone.now(),
        )
        CalendarShare.objects.create(
            calendar=cls.calendar,
            user=cls.writer,
            role=CalendarShare.WRITE,
            accepted_at=timezone.now(),
        )
        cls.object = CalendarObject.objects.create(
            calendar=cls.calendar,
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

    def test_well_known_redirects_with_trailing_slash(self):
        response = self.client.get("/.well-known/caldav/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers.get("Location"), "/dav/")

    def test_dav_root_propfind_requires_authentication(self):
        response = self.client.generic(
            "PROPFIND", "/dav/", data="", content_type="application/xml"
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn(
            'Basic realm="davhome"', response.headers.get("WWW-Authenticate", "")
        )

        response_depth0 = self.client.generic(
            "PROPFIND",
            "/dav/",
            data="",
            content_type="application/xml",
            HTTP_DEPTH="0",
        )
        self.assertEqual(response_depth0.status_code, 401)
        self.assertIn(
            'Basic realm="davhome"',
            response_depth0.headers.get("WWW-Authenticate", ""),
        )

    def test_dav_root_no_trailing_slash_propfind_requires_authentication(self):
        response = self.client.generic(
            "PROPFIND", "/dav", data="", content_type="application/xml"
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn(
            'Basic realm="davhome"', response.headers.get("WWW-Authenticate", "")
        )

    def test_dav_root_options_advertises_dav(self):
        response = self.client.options("/dav/")
        self.assertEqual(response.status_code, 204)
        self.assertIn("calendar-access", response.headers.get("DAV", ""))

    def test_dav_root_no_trailing_slash_options_advertises_dav(self):
        response = self.client.options("/dav")
        self.assertEqual(response.status_code, 204)
        self.assertIn("calendar-access", response.headers.get("DAV", ""))

    def test_principal_propfind_includes_calendar_home_set(self):
        response = self.client.generic(
            "PROPFIND",
            f"/dav/principals/{self.owner.username}/",
            data="",
            content_type="application/xml",
            HTTP_DEPTH="0",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 207)
        xml = self._xml(response.content)
        self.assertIn(
            f"/dav/calendars/users/{self.owner.username}/",
            ET.tostring(xml, encoding="unicode"),
        )

    def test_principal_propfind_without_trailing_slash(self):
        response = self.client.generic(
            "PROPFIND",
            f"/dav/principals/{self.owner.username}",
            data="",
            content_type="application/xml",
            HTTP_DEPTH="0",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 207)

    def test_principals_users_collection_exists(self):
        response = self.client.generic(
            "PROPFIND",
            "/dav/principals/users/",
            data="",
            content_type="application/xml",
            HTTP_DEPTH="0",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 207)

    def test_calendar_home_without_trailing_slash_exists(self):
        response = self.client.generic(
            "PROPFIND",
            f"/dav/calendars/users/{self.owner.username}",
            data="",
            content_type="application/xml",
            HTTP_DEPTH="0",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 207)

    def test_depth_infinity_returns_propfind_finite_depth_error(self):
        body = """<?xml version=\"1.0\" encoding=\"utf-8\"?>
<D:propfind xmlns:D=\"DAV:\"><D:prop><D:resourcetype/></D:prop></D:propfind>"""
        response = self.client.generic(
            "PROPFIND",
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/",
            data=body,
            content_type="application/xml",
            HTTP_DEPTH="infinity",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("propfind-finite-depth", response.content.decode("utf-8"))

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

    def test_collection_conditional_get_returns_304(self):
        first = self.client.get(
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(first.status_code, 200)

        second = self.client.get(
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/",
            HTTP_IF_NONE_MATCH=first.headers.get("ETag"),
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(second.status_code, 304)

    def test_home_conditional_get_returns_304(self):
        first = self.client.get(
            f"/dav/calendars/{self.owner.username}/",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(first.status_code, 200)

        second = self.client.get(
            f"/dav/calendars/{self.owner.username}/",
            HTTP_IF_NONE_MATCH=first.headers.get("ETag"),
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(second.status_code, 304)

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
            HTTP_DEPTH="0",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 207)
        xml_text = response.content.decode("utf-8")
        self.assertIn("404 Not Found", xml_text)

    def test_calendar_collection_propfind_includes_supported_report_set(self):
        body = """<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:">
  <D:prop>
    <D:supported-report-set/>
  </D:prop>
</D:propfind>"""
        response = self.client.generic(
            "PROPFIND",
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/",
            data=body,
            content_type="application/xml",
            HTTP_DEPTH="0",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 207)
        xml_text = response.content.decode("utf-8")
        self.assertIn("calendar-query", xml_text)
        self.assertIn("calendar-multiget", xml_text)
        self.assertIn("sync-collection", xml_text)

    def test_calendar_collection_propfind_includes_sync_token(self):
        body = """<?xml version=\"1.0\" encoding=\"utf-8\"?>
<D:propfind xmlns:D=\"DAV:\">
  <D:prop>
    <D:sync-token/>
  </D:prop>
</D:propfind>"""
        response = self.client.generic(
            "PROPFIND",
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/",
            data=body,
            content_type="application/xml",
            HTTP_DEPTH="0",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 207)
        self.assertIn(
            f"data:,{self.calendar.id}/0",
            response.content.decode("utf-8"),
        )

    def test_calendar_home_supported_report_set_does_not_include_sync_collection(self):
        body = """<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:">
  <D:prop>
    <D:supported-report-set/>
  </D:prop>
</D:propfind>"""
        response = self.client.generic(
            "PROPFIND",
            f"/dav/calendars/{self.owner.username}/",
            data=body,
            content_type="application/xml",
            HTTP_DEPTH="0",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 207)
        self.assertNotIn("sync-collection", response.content.decode("utf-8"))

    def test_calendar_collection_propfind_includes_owner(self):
        body = """<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:"><D:prop><D:owner/></D:prop></D:propfind>"""
        response = self.client.generic(
            "PROPFIND",
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/",
            data=body,
            content_type="application/xml",
            HTTP_DEPTH="0",
            **self._basic_auth("member", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 207)
        self.assertIn(
            f"/dav/principals/users/{self.owner.username}/",
            response.content.decode("utf-8"),
        )

    def test_current_user_privileges_read_share_is_read_only(self):
        body = """<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:">
  <D:prop><D:current-user-privilege-set/></D:prop>
</D:propfind>"""
        response = self.client.generic(
            "PROPFIND",
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/",
            data=body,
            content_type="application/xml",
            HTTP_DEPTH="0",
            **self._basic_auth("member", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 207)
        xml_text = response.content.decode("utf-8")
        self.assertIn("read-current-user-privilege-set", xml_text)
        self.assertNotIn("write-content", xml_text)

    def test_current_user_privileges_write_share_includes_write(self):
        body = """<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:">
  <D:prop><D:current-user-privilege-set/></D:prop>
</D:propfind>"""
        response = self.client.generic(
            "PROPFIND",
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/",
            data=body,
            content_type="application/xml",
            HTTP_DEPTH="0",
            **self._basic_auth("writer", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 207)
        xml_text = response.content.decode("utf-8")
        self.assertIn("write-content", xml_text)
        self.assertIn("bind", xml_text)


class DavWriteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(username="owner", password="pw-test-12345")
        cls.writer = User.objects.create_user(
            username="writer",
            password="pw-test-12345",
        )
        cls.reader = User.objects.create_user(
            username="reader",
            password="pw-test-12345",
        )
        cls.calendar = Calendar.objects.create(
            owner=cls.owner,
            slug="family",
            name="Family",
            timezone="UTC",
        )
        CalendarShare.objects.create(
            calendar=cls.calendar,
            user=cls.writer,
            role=CalendarShare.WRITE,
            accepted_at=timezone.now(),
        )
        CalendarShare.objects.create(
            calendar=cls.calendar,
            user=cls.reader,
            role=CalendarShare.READ,
            accepted_at=timezone.now(),
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
        change = CalendarObjectChange.objects.get(
            calendar=self.calendar,
            revision=1,
        )
        self.assertEqual(change.filename, "event-1.ics")
        self.assertEqual(change.uid, "event-1")
        self.assertFalse(change.is_deleted)

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

    def test_owner_mkcalendar_creates_calendar(self):
        body = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<B:mkcalendar xmlns:B=\"urn:ietf:params:xml:ns:caldav\" xmlns:A=\"DAV:\" xmlns:D=\"http://apple.com/ns/ical/\">
  <A:set>
    <A:prop>
      <A:displayname>Tasks</A:displayname>
      <B:supported-calendar-component-set>
        <B:comp name=\"VTODO\"/>
      </B:supported-calendar-component-set>
      <D:calendar-color>#007AFF</D:calendar-color>
      <D:calendar-order>23</D:calendar-order>
    </A:prop>
  </A:set>
</B:mkcalendar>
"""
        response = self.client.generic(
            "MKCALENDAR",
            f"/dav/calendars/{self.owner.username}/newcal/",
            data=body,
            content_type="application/xml",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 201)
        created = Calendar.objects.get(owner=self.owner, slug="newcal")
        self.assertEqual(created.name, "Tasks")
        self.assertEqual(created.component_kind, Calendar.COMPONENT_VTODO)
        self.assertEqual(created.color, "#007AFF")
        self.assertEqual(created.sort_order, 23)

    def test_shared_writer_cannot_mkcalendar(self):
        response = self.client.generic(
            "MKCALENDAR",
            f"/dav/calendars/{self.owner.username}/newcal-writer/",
            data="",
            content_type="application/xml",
            **self._basic_auth("writer", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 403)

    def test_mkcol_can_create_top_level_calendar_for_webdav_compatibility(self):
        response = self.client.generic(
            "MKCOL",
            f"/dav/calendars/{self.owner.username}/newcal/",
            data="",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            Calendar.objects.filter(owner=self.owner, slug="newcal").exists()
        )

    def test_proppatch_updates_calendar_color_and_order(self):
        body = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<A:propertyupdate xmlns:A=\"DAV:\" xmlns:G=\"http://apple.com/ns/ical/\">
  <A:set>
    <A:prop>
      <G:calendar-color>#CB30E0</G:calendar-color>
      <G:calendar-order>7</G:calendar-order>
    </A:prop>
  </A:set>
</A:propertyupdate>
"""
        response = self.client.generic(
            "PROPPATCH",
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/",
            data=body,
            content_type="application/xml",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 207)
        self.calendar.refresh_from_db()
        self.assertEqual(self.calendar.color, "#CB30E0")
        self.assertEqual(self.calendar.sort_order, 7)

    def test_put_vtodo_rejected_in_vevent_calendar(self):
        response = self._put_event(
            "owner",
            "pw-test-12345",
            "todo-1.ics",
            "BEGIN:VCALENDAR\nBEGIN:VTODO\nUID:todo-1\nEND:VTODO\nEND:VCALENDAR\n",
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("supported-calendar-component", response.content.decode("utf-8"))

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
        changes = list(
            CalendarObjectChange.objects.filter(calendar=self.calendar).order_by(
                "revision"
            )
        )
        self.assertEqual([change.revision for change in changes], [1, 2])
        self.assertEqual(changes[-1].filename, "event-6.ics")
        self.assertTrue(changes[-1].is_deleted)

    def test_put_without_uid_fails(self):
        response = self._put_event(
            "owner",
            "pw-test-12345",
            "event-7.ics",
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\nEND:VEVENT\nEND:VCALENDAR\n",
        )
        self.assertEqual(response.status_code, 400)

    def test_put_preserves_content_type_parameters(self):
        response = self.client.generic(
            "PUT",
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/ctype.ics",
            data="BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:ctype\nEND:VEVENT\nEND:VCALENDAR\n",
            content_type="text/calendar; charset=utf-8",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 201)
        obj = CalendarObject.objects.get(calendar=self.calendar, filename="ctype.ics")
        self.assertEqual(obj.content_type, "text/calendar;charset=utf-8")

    def test_put_deduplicates_duplicate_valarms(self):
        ical = (
            "BEGIN:VCALENDAR\n"
            "BEGIN:VEVENT\n"
            "UID:dupe-1\n"
            "BEGIN:VALARM\nACTION:DISPLAY\nDESCRIPTION:Test\nTRIGGER:-PT10M\nEND:VALARM\n"
            "BEGIN:VALARM\nACTION:DISPLAY\nDESCRIPTION:Test\nTRIGGER:-PT10M\nEND:VALARM\n"
            "END:VEVENT\n"
            "END:VCALENDAR\n"
        )
        response = self._put_event("owner", "pw-test-12345", "dupe.ics", ical)
        self.assertEqual(response.status_code, 201)
        obj = CalendarObject.objects.get(calendar=self.calendar, filename="dupe.ics")
        self.assertEqual(obj.ical_blob.count("BEGIN:VALARM"), 1)


class DavReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(username="owner", password="pw-test-12345")
        cls.writer = User.objects.create_user(
            username="writer", password="pw-test-12345"
        )
        cls.calendar = Calendar.objects.create(
            owner=cls.owner,
            slug="family",
            name="Family",
            timezone="UTC",
        )
        CalendarShare.objects.create(
            calendar=cls.calendar,
            user=cls.writer,
            role=CalendarShare.WRITE,
            accepted_at=timezone.now(),
        )
        cls.event = CalendarObject.objects.create(
            calendar=cls.calendar,
            uid="uid-event",
            filename="event.ics",
            etag='"etag-event"',
            ical_blob="BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:uid-event\nDTSTART:20260215T120000Z\nDTEND:20260215T130000Z\nEND:VEVENT\nEND:VCALENDAR\n",
            size=120,
        )
        cls.todo = CalendarObject.objects.create(
            calendar=cls.calendar,
            uid="uid-todo",
            filename="todo.ics",
            etag='"etag-todo"',
            ical_blob="BEGIN:VCALENDAR\nBEGIN:VTODO\nUID:uid-todo\nDTSTART:20260216\nEND:VTODO\nEND:VCALENDAR\n",
            size=96,
        )

    def _basic_auth(self, username, password):
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode(
            "ascii"
        )
        return {"HTTP_AUTHORIZATION": f"Basic {token}"}

    def _sync_collection_body(self, sync_token=None, limit=None):
        sync_token_xml = ""
        if sync_token is not None:
            sync_token_xml = f"<D:sync-token>{sync_token}</D:sync-token>"
        limit_xml = ""
        if limit is not None:
            limit_xml = f"<D:limit><D:nresults>{limit}</D:nresults></D:limit>"
        return f"""<?xml version=\"1.0\" encoding=\"utf-8\"?>
<D:sync-collection xmlns:D=\"DAV:\" xmlns:C=\"urn:ietf:params:xml:ns:caldav\">
  <D:sync-level>1</D:sync-level>
  {sync_token_xml}
  {limit_xml}
  <D:prop>
    <D:getetag/>
    <C:calendar-data/>
  </D:prop>
</D:sync-collection>"""

    def _sync_collection_report(
        self,
        path,
        username="owner",
        password="pw-test-12345",
        sync_token=None,
        limit=None,
    ):
        return self.client.generic(
            "REPORT",
            path,
            data=self._sync_collection_body(sync_token=sync_token, limit=limit),
            content_type="application/xml",
            **self._basic_auth(username, password),
        )

    def test_calendar_multiget_returns_calendar_data(self):
        body = f"""<?xml version=\"1.0\" encoding=\"utf-8\"?>
<C:calendar-multiget xmlns:D=\"DAV:\" xmlns:C=\"urn:ietf:params:xml:ns:caldav\">
  <D:prop>
    <D:getetag/>
    <C:calendar-data/>
  </D:prop>
  <D:href>/dav/calendars/{self.owner.username}/{self.calendar.slug}/{self.event.filename}</D:href>
</C:calendar-multiget>"""
        response = self.client.generic(
            "REPORT",
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/",
            data=body,
            content_type="application/xml",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 207)
        xml_text = response.content.decode("utf-8")
        self.assertIn(self.event.etag, xml_text)
        self.assertIn("BEGIN:VEVENT", xml_text)

    def test_calendar_query_filters_component(self):
        body = """<?xml version=\"1.0\" encoding=\"utf-8\"?>
<C:calendar-query xmlns:D=\"DAV:\" xmlns:C=\"urn:ietf:params:xml:ns:caldav\">
  <D:prop>
    <D:getetag/>
    <C:calendar-data/>
  </D:prop>
  <C:filter>
    <C:comp-filter name=\"VCALENDAR\">
      <C:comp-filter name=\"VEVENT\" />
    </C:comp-filter>
  </C:filter>
</C:calendar-query>"""
        response = self.client.generic(
            "REPORT",
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/",
            data=body,
            content_type="application/xml",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 207)
        xml_text = response.content.decode("utf-8")
        self.assertIn(self.event.filename, xml_text)
        self.assertNotIn(self.todo.filename, xml_text)

    def test_calendar_query_time_range_limits_results(self):
        body = """<?xml version=\"1.0\" encoding=\"utf-8\"?>
<C:calendar-query xmlns:D=\"DAV:\" xmlns:C=\"urn:ietf:params:xml:ns:caldav\">
  <D:prop>
    <D:getetag/>
    <C:calendar-data/>
  </D:prop>
  <C:filter>
    <C:comp-filter name=\"VCALENDAR\">
      <C:comp-filter name=\"VEVENT\">
        <C:time-range start=\"20260215T110000Z\" end=\"20260215T140000Z\" />
      </C:comp-filter>
    </C:comp-filter>
  </C:filter>
</C:calendar-query>"""
        response = self.client.generic(
            "REPORT",
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/",
            data=body,
            content_type="application/xml",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 207)
        xml_text = response.content.decode("utf-8")
        self.assertIn(self.event.filename, xml_text)

    def test_report_unknown_type_returns_501(self):
        body = """<?xml version=\"1.0\" encoding=\"utf-8\"?>
<D:expand-property xmlns:D=\"DAV:\" />"""
        response = self.client.generic(
            "REPORT",
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/",
            data=body,
            content_type="application/xml",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 501)

    def test_calendar_home_sync_collection_report_returns_501(self):
        body = """<?xml version="1.0" encoding="utf-8"?>
<D:sync-collection xmlns:D="DAV:">
  <D:sync-level>1</D:sync-level>
  <D:prop>
    <D:getetag/>
  </D:prop>
</D:sync-collection>"""
        response = self.client.generic(
            "REPORT",
            f"/dav/calendars/{self.owner.username}/",
            data=body,
            content_type="application/xml",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 501)

    def test_report_supported_on_calendar_home(self):
        body = f"""<?xml version=\"1.0\" encoding=\"utf-8\"?>
<C:calendar-multiget xmlns:D=\"DAV:\" xmlns:C=\"urn:ietf:params:xml:ns:caldav\">
  <D:prop>
    <D:getetag/>
    <C:calendar-data/>
  </D:prop>
  <D:href>/dav/calendars/{self.owner.username}/{self.calendar.slug}/{self.event.filename}</D:href>
</C:calendar-multiget>"""
        response = self.client.generic(
            "REPORT",
            f"/dav/calendars/{self.owner.username}/",
            data=body,
            content_type="application/xml",
            **self._basic_auth("writer", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 207)
        self.assertIn(self.event.filename, response.content.decode("utf-8"))

    def test_multiget_missing_href_returns_response_404_status(self):
        body = """<?xml version=\"1.0\" encoding=\"utf-8\"?>
<C:calendar-multiget xmlns:D=\"DAV:\" xmlns:C=\"urn:ietf:params:xml:ns:caldav\">
  <D:prop>
    <D:getetag/>
  </D:prop>
  <D:href>/dav/calendars/owner/family/missing.ics</D:href>
</C:calendar-multiget>"""
        response = self.client.generic(
            "REPORT",
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/",
            data=body,
            content_type="application/xml",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 207)
        self.assertIn("404 Not Found", response.content.decode("utf-8"))

    def test_query_uses_uids_href_style_when_requested_on_uids_path(self):
        user01 = User.objects.create_user(username="user01", password="user01")
        calendar = Calendar.objects.create(
            owner=user01,
            slug="family",
            name="Family",
            timezone="UTC",
        )
        CalendarObject.objects.create(
            calendar=calendar,
            uid="uid-u1",
            filename="u1.ics",
            etag='"etag-u1"',
            ical_blob="BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:uid-u1\nDTSTART:20260215T120000Z\nEND:VEVENT\nEND:VCALENDAR\n",
            size=100,
        )

        body = """<?xml version=\"1.0\" encoding=\"utf-8\"?>
<C:calendar-query xmlns:D=\"DAV:\" xmlns:C=\"urn:ietf:params:xml:ns:caldav\">
  <D:prop>
    <D:getetag/>
    <C:calendar-data/>
  </D:prop>
</C:calendar-query>"""
        response = self.client.generic(
            "REPORT",
            "/dav/calendars/__uids__/10000000-0000-0000-0000-000000000001/family/",
            data=body,
            content_type="application/xml",
            **self._basic_auth("user01", "user01"),
        )
        self.assertEqual(response.status_code, 207)
        xml_text = response.content.decode("utf-8")
        self.assertIn(
            "/dav/calendars/__uids__/10000000-0000-0000-0000-000000000001/family/",
            xml_text,
        )

    def test_sync_collection_initial_sync_returns_members_and_token(self):
        response = self._sync_collection_report(
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/"
        )
        self.assertEqual(response.status_code, 207)
        xml_text = response.content.decode("utf-8")
        self.assertIn(self.event.filename, xml_text)
        self.assertIn(self.todo.filename, xml_text)
        self.assertIn(
            f"data:,{self.calendar.id}/0",
            xml_text,
        )

    def test_sync_collection_incremental_after_put_returns_changed_item(self):
        create = self.client.generic(
            "PUT",
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/sync-new.ics",
            data="BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:sync-new\nEND:VEVENT\nEND:VCALENDAR\n",
            content_type="text/calendar; charset=utf-8",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(create.status_code, 201)

        response = self._sync_collection_report(
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/",
            sync_token=f"data:,{self.calendar.id}/0",
        )
        self.assertEqual(response.status_code, 207)
        xml_text = response.content.decode("utf-8")
        self.assertIn("sync-new.ics", xml_text)
        self.assertIn(f"data:,{self.calendar.id}/1", xml_text)

    def test_sync_collection_delete_returns_404_response(self):
        create = self.client.generic(
            "PUT",
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/sync-delete.ics",
            data="BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:sync-delete\nEND:VEVENT\nEND:VCALENDAR\n",
            content_type="text/calendar; charset=utf-8",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(create.status_code, 201)

        delete = self.client.generic(
            "DELETE",
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/sync-delete.ics",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(delete.status_code, 204)

        response = self._sync_collection_report(
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/",
            sync_token=f"data:,{self.calendar.id}/1",
        )
        self.assertEqual(response.status_code, 207)
        xml_text = response.content.decode("utf-8")
        self.assertIn("sync-delete.ics", xml_text)
        self.assertIn("404 Not Found", xml_text)

    def test_sync_collection_invalid_token_returns_valid_sync_token_error(self):
        response = self._sync_collection_report(
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/",
            sync_token="not-a-token",
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("valid-sync-token", response.content.decode("utf-8"))

    def test_sync_collection_wrong_calendar_token_returns_valid_sync_token_error(self):
        other_calendar = Calendar.objects.create(
            owner=self.owner,
            slug="other",
            name="Other",
            timezone="UTC",
        )
        response = self._sync_collection_report(
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/",
            sync_token=f"data:,{other_calendar.id}/0",
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("valid-sync-token", response.content.decode("utf-8"))

    def test_sync_collection_future_revision_token_returns_valid_sync_token_error(self):
        response = self._sync_collection_report(
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/",
            sync_token=f"data:,{self.calendar.id}/99",
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("valid-sync-token", response.content.decode("utf-8"))

    def test_sync_collection_hrefs_follow_users_and_uids_paths(self):
        user01 = User.objects.create_user(username="user01", password="user01")
        calendar = Calendar.objects.create(
            owner=user01,
            slug="family",
            name="Family",
            timezone="UTC",
        )
        CalendarObject.objects.create(
            calendar=calendar,
            uid="uid-style",
            filename="style.ics",
            etag='"etag-style"',
            ical_blob="BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:uid-style\nEND:VEVENT\nEND:VCALENDAR\n",
            size=96,
        )

        users_response = self._sync_collection_report(
            "/dav/calendars/users/user01/family/",
            username="user01",
            password="user01",
        )
        self.assertEqual(users_response.status_code, 207)
        self.assertIn(
            "/dav/calendars/users/user01/family/style.ics",
            users_response.content.decode("utf-8"),
        )

        uids_response = self._sync_collection_report(
            "/dav/calendars/__uids__/10000000-0000-0000-0000-000000000001/family/",
            username="user01",
            password="user01",
        )
        self.assertEqual(uids_response.status_code, 207)
        self.assertIn(
            "/dav/calendars/__uids__/10000000-0000-0000-0000-000000000001/family/style.ics",
            uids_response.content.decode("utf-8"),
        )

    def test_sync_collection_limit_is_supported(self):
        first = self.client.generic(
            "PUT",
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/limit-a.ics",
            data="BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:limit-a\nEND:VEVENT\nEND:VCALENDAR\n",
            content_type="text/calendar; charset=utf-8",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(first.status_code, 201)
        second = self.client.generic(
            "PUT",
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/limit-b.ics",
            data="BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:limit-b\nEND:VEVENT\nEND:VCALENDAR\n",
            content_type="text/calendar; charset=utf-8",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(second.status_code, 201)

        response = self._sync_collection_report(
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/",
            sync_token=f"data:,{self.calendar.id}/0",
            limit=1,
        )
        self.assertEqual(response.status_code, 207)
        xml_text = response.content.decode("utf-8")
        self.assertNotIn("number-of-matches-within-limits", xml_text)
        root = ET.fromstring(response.content)
        token = root.find("{DAV:}sync-token")
        self.assertIsNotNone(token)
        token_value = "" if token is None else (token.text or "")
        self.assertTrue(token_value.startswith(f"data:,{self.calendar.id}/"))

    def test_sync_collection_limit_with_filter_returns_207(self):
        body = f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<sync-collection xmlns=\"DAV:\">
  <sync-token />
  <sync-level>1</sync-level>
  <prop>
    <getcontenttype />
  </prop>
  <filter xmlns=\"urn:ietf:params:xml:ns:caldav\">
    <comp-filter name=\"VCALENDAR\">
      <comp-filter name=\"VEVENT\" />
    </comp-filter>
  </filter>
  <limit>
    <nresults>100</nresults>
  </limit>
</sync-collection>"""
        response = self.client.generic(
            "REPORT",
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/",
            data=body,
            content_type="application/xml",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(response.status_code, 207)
        self.assertNotIn(
            "number-of-matches-within-limits",
            response.content.decode("utf-8"),
        )


class DavWebdavCompatibilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="user01", password="user01")
        cls.calendar = Calendar.objects.create(
            owner=cls.user,
            slug="litmus",
            name="litmus",
            timezone="UTC",
        )

    def _basic_auth(self):
        token = base64.b64encode(b"user01:user01").decode("ascii")
        return {"HTTP_AUTHORIZATION": f"Basic {token}"}

    def test_generic_put_create_returns_201(self):
        response = self.client.generic(
            "PUT",
            "/dav/calendars/user01/litmus/res",
            data="simple dav payload",
            content_type="text/plain",
            **self._basic_auth(),
        )
        self.assertEqual(response.status_code, 201)

        fetch = self.client.get(
            "/dav/calendars/user01/litmus/res",
            **self._basic_auth(),
        )
        self.assertEqual(fetch.status_code, 200)
        self.assertEqual(fetch.content.decode("utf-8"), "simple dav payload")

    def test_put_with_missing_parent_returns_409(self):
        response = self.client.generic(
            "PUT",
            "/dav/calendars/user01/litmus/missing/res",
            data="payload",
            content_type="text/plain",
            **self._basic_auth(),
        )
        self.assertEqual(response.status_code, 409)

    def test_nested_mkcol_is_supported_for_litmus_collection(self):
        mkcol = self.client.generic(
            "MKCOL",
            "/dav/calendars/user01/litmus/coll/",
            data="",
            **self._basic_auth(),
        )
        self.assertEqual(mkcol.status_code, 201)

    def test_mkcol_missing_parent_returns_409(self):
        response = self.client.generic(
            "MKCOL",
            "/dav/calendars/user01/litmus/nope/coll/",
            data="",
            **self._basic_auth(),
        )
        self.assertEqual(response.status_code, 409)

    def test_mkcol_with_body_returns_415(self):
        response = self.client.generic(
            "MKCOL",
            "/dav/calendars/user01/litmus/bodycoll/",
            data="not allowed",
            content_type="text/plain",
            **self._basic_auth(),
        )
        self.assertEqual(response.status_code, 415)

    def test_copy_and_move_methods_work_for_litmus_collection(self):
        put = self.client.generic(
            "PUT",
            "/dav/calendars/user01/litmus/source",
            data="copy me",
            content_type="text/plain",
            **self._basic_auth(),
        )
        self.assertEqual(put.status_code, 201)

        copy = self.client.generic(
            "COPY",
            "/dav/calendars/user01/litmus/source",
            HTTP_DESTINATION="/dav/calendars/user01/litmus/copied",
            HTTP_OVERWRITE="F",
            **self._basic_auth(),
        )
        self.assertEqual(copy.status_code, 201)

        copied = self.client.get(
            "/dav/calendars/user01/litmus/copied",
            **self._basic_auth(),
        )
        self.assertEqual(copied.status_code, 200)
        self.assertEqual(copied.content.decode("utf-8"), "copy me")

        move = self.client.generic(
            "MOVE",
            "/dav/calendars/user01/litmus/copied",
            HTTP_DESTINATION="/dav/calendars/user01/litmus/moved",
            **self._basic_auth(),
        )
        self.assertEqual(move.status_code, 201)

        old = self.client.get(
            "/dav/calendars/user01/litmus/copied",
            **self._basic_auth(),
        )
        self.assertEqual(old.status_code, 404)
        moved = self.client.get(
            "/dav/calendars/user01/litmus/moved",
            **self._basic_auth(),
        )
        self.assertEqual(moved.status_code, 200)
        self.assertEqual(moved.content.decode("utf-8"), "copy me")

    def test_proppatch_sets_and_reads_dead_property_on_litmus_resource(self):
        put = self.client.generic(
            "PUT",
            "/dav/calendars/user01/litmus/prop",
            data="body",
            content_type="text/plain",
            **self._basic_auth(),
        )
        self.assertEqual(put.status_code, 201)

        patch_body = """<?xml version=\"1.0\" encoding=\"utf-8\"?>
<D:propertyupdate xmlns:D=\"DAV:\" xmlns:Z=\"http://example.com/ns\">
  <D:set>
    <D:prop>
      <Z:color>blue</Z:color>
    </D:prop>
  </D:set>
</D:propertyupdate>"""
        patch_response = self.client.generic(
            "PROPPATCH",
            "/dav/calendars/user01/litmus/prop",
            data=patch_body,
            content_type="application/xml",
            **self._basic_auth(),
        )
        self.assertEqual(patch_response.status_code, 207)

        propfind_body = """<?xml version=\"1.0\" encoding=\"utf-8\"?>
<D:propfind xmlns:D=\"DAV:\" xmlns:Z=\"http://example.com/ns\">
  <D:prop>
    <Z:color/>
  </D:prop>
</D:propfind>"""
        propfind = self.client.generic(
            "PROPFIND",
            "/dav/calendars/user01/litmus/prop",
            data=propfind_body,
            content_type="application/xml",
            HTTP_DEPTH="0",
            **self._basic_auth(),
        )
        self.assertEqual(propfind.status_code, 207)
        self.assertIn("blue", propfind.content.decode("utf-8"))


class DavPrincipalAliasTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user1 = User.objects.create_user(username="user01", password="user01")
        Calendar.objects.create(owner=cls.user1, slug="calendar", name="calendar")

    def _basic_auth(self):
        token = base64.b64encode(b"user01:user01").decode("ascii")
        return {"HTTP_AUTHORIZATION": f"Basic {token}"}

    def test_root_current_user_principal_uses_uids_href(self):
        body = """<?xml version=\"1.0\" encoding=\"utf-8\"?>
<D:propfind xmlns:D=\"DAV:\"><D:prop><D:current-user-principal/></D:prop></D:propfind>"""
        response = self.client.generic(
            "PROPFIND",
            "/dav/",
            data=body,
            content_type="application/xml",
            HTTP_DEPTH="0",
            **self._basic_auth(),
        )
        self.assertEqual(response.status_code, 207)
        self.assertIn(
            "/dav/principals/__uids__/10000000-0000-0000-0000-000000000001/",
            response.content.decode("utf-8"),
        )

    def test_principal_uids_alias_resolves(self):
        response = self.client.generic(
            "PROPFIND",
            "/dav/principals/__uids__/10000000-0000-0000-0000-000000000001/",
            data="",
            content_type="application/xml",
            HTTP_DEPTH="0",
            **self._basic_auth(),
        )
        self.assertEqual(response.status_code, 207)

    def test_calendar_uids_alias_resolves(self):
        response = self.client.generic(
            "PROPFIND",
            "/dav/calendars/__uids__/10000000-0000-0000-0000-000000000001/",
            data="",
            content_type="application/xml",
            HTTP_DEPTH="0",
            **self._basic_auth(),
        )
        self.assertEqual(response.status_code, 207)
