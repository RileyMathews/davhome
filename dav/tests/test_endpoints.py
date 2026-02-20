# pyright: reportGeneralTypeIssues=false, reportAttributeAccessIssue=false, reportArgumentType=false, reportCallIssue=false, reportOperatorIssue=false

import base64
from datetime import date, datetime, timedelta, timezone as datetime_timezone
from types import SimpleNamespace
from uuid import UUID
from xml.etree import ElementTree as ET

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from calendars.models import (
    Calendar,
    CalendarObject,
    CalendarObjectChange,
    CalendarShare,
)
from dav import entrypoints
from dav.core import calendar_data as core_calendar_data
from dav.core import filters as core_filters
from dav.core import freebusy as core_freebusy
from dav.core import paths as core_paths
from dav.core import payloads as core_payloads
from dav.core import query as core_query
from dav.core import recurrence as core_recurrence
from dav.core import time as core_time
from dav.view_helpers.calendar_mutation_payloads import (
    _calendar_collection_proppatch_plan,
)
from dav.view_helpers.copy_move import _remap_uid_for_copied_object
from dav.view_helpers.freebusy import _build_freebusy_response_lines
from dav.view_helpers.ical import _dedupe_duplicate_alarms
from dav.view_helpers.identity import (
    _calendar_home_href_for_user,
    _dav_guid_for_username,
    _dav_username_for_guid,
    _principal_href_for_user,
)
from dav.view_helpers.parsing import _calendar_default_tzinfo, _parse_xml_body
from dav.view_helpers.recurrence_serialization import (
    _append_date_or_datetime_line,
    _resolved_recurrence_text,
    _uid_drop_recurrence_map,
)
from dav.view_helpers.report_paths import _all_object_hrefs, _report_href_style
from dav.view_helpers import sync_tokens as sync_token_helpers
from dav.view_helpers.sync_tokens import _build_sync_token
from dav.common import (
    _parse_sync_token_for_calendar,
    _remote_ip,
    _sync_token_revision_from_parts,
)
from dav.report_handlers import (
    _sync_collection_limit,
    _sync_collection_multistatus_document,
    _tzinfo_from_report,
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

    def test_method_not_allowed_is_logged_by_middleware(self):
        with self.assertLogs("dav.audit", level="WARNING") as captured:
            response = self.client.generic(
                "POST",
                f"/dav/principals/{self.owner.username}/",
                data="payload",
                content_type="text/plain",
            )

        self.assertEqual(response.status_code, 405)
        log_output = "\n".join(captured.output)
        self.assertIn("dav_method_not_allowed", log_output)
        self.assertIn("status=405", log_output)
        self.assertIn("allowed=['GET', 'HEAD', 'OPTIONS', 'PROPFIND']", log_output)
        self.assertIn("extra={}", log_output)

    def test_dav_root_options_advertises_dav(self):
        response = self.client.options("/dav/")
        self.assertEqual(response.status_code, 204)
        self.assertIn("calendar-access", response.headers.get("DAV", ""))

    def test_dav_root_options_includes_allow_and_dav_guardrails(self):
        response = self.client.options("/dav/")
        self.assertEqual(response.status_code, 204)
        self.assertNotIn(response.status_code, (301, 302, 307, 308))
        allow = {part.strip() for part in response.headers.get("Allow", "").split(",")}
        self.assertTrue({"OPTIONS", "PROPFIND", "GET", "HEAD"}.issubset(allow))
        dav_header = response.headers.get("DAV", "")
        self.assertTrue(dav_header)
        self.assertIn("calendar-access", dav_header)

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

    def test_authenticated_propfind_and_report_dispatch_on_calendar_collection(self):
        path = f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/"
        propfind = self.client.generic(
            "PROPFIND",
            path,
            data="",
            content_type="application/xml",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertNotIn(propfind.status_code, (301, 302, 307, 308))
        self.assertNotEqual(propfind.status_code, 405)

        report_body = """<?xml version=\"1.0\" encoding=\"utf-8\"?>
<C:calendar-query xmlns:D=\"DAV:\" xmlns:C=\"urn:ietf:params:xml:ns:caldav\">
  <D:prop>
    <D:getetag/>
  </D:prop>
</C:calendar-query>"""
        report = self.client.generic(
            "REPORT",
            path,
            data=report_body,
            content_type="application/xml",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertNotIn(report.status_code, (301, 302, 307, 308))
        self.assertNotEqual(report.status_code, 405)

    def test_sync_collection_logs_request_revisions_and_selected_items(self):
        create = self.client.generic(
            "PUT",
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/sync-log.ics",
            data="BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:sync-log\nEND:VEVENT\nEND:VCALENDAR\n",
            content_type="text/calendar; charset=utf-8",
            **self._basic_auth("owner", "pw-test-12345"),
        )
        self.assertEqual(create.status_code, 201)

        with self.assertLogs("dav.audit", level="INFO") as captured:
            response = self._sync_collection_report(
                f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/",
                sync_token=f"data:,{self.calendar.id}/0",
            )

        self.assertEqual(response.status_code, 207)
        log_output = "\n".join(captured.output)
        self.assertIn("dav_sync_collection_request", log_output)
        self.assertIn("dav_sync_collection_token_parsed", log_output)
        self.assertIn("dav_sync_collection_selection", log_output)
        self.assertIn("dav_sync_collection_response", log_output)
        self.assertIn("token_revision=0", log_output)
        self.assertIn("latest_revision=1", log_output)
        self.assertIn("sync-log.ics", log_output)


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

    def test_copy_with_duplicate_uid_returns_409_instead_of_500(self):
        put = self.client.generic(
            "PUT",
            "/dav/calendars/user01/litmus/a.ics",
            data="BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:a\nEND:VEVENT\nEND:VCALENDAR\n",
            content_type="text/calendar; charset=utf-8",
            **self._basic_auth(),
        )
        self.assertEqual(put.status_code, 201)

        copy = self.client.generic(
            "COPY",
            "/dav/calendars/user01/litmus/a.ics",
            HTTP_DESTINATION="/dav/calendars/user01/litmus/b.ics",
            HTTP_OVERWRITE="F",
            **self._basic_auth(),
        )
        self.assertEqual(copy.status_code, 409)
        self.assertFalse(
            self.calendar.calendar_objects.filter(filename="b.ics").exists()
        )

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

    def test_principal_uids_alias_unknown_guid_returns_404(self):
        response = self.client.generic(
            "PROPFIND",
            "/dav/principals/__uids__/10000000-0000-0000-0000-000000000999/",
            data="",
            content_type="application/xml",
            HTTP_DEPTH="0",
            **self._basic_auth(),
        )
        self.assertEqual(response.status_code, 404)

    def test_calendar_collection_uids_alias_unknown_guid_returns_404(self):
        response = self.client.generic(
            "PROPFIND",
            "/dav/calendars/__uids__/10000000-0000-0000-0000-000000000999/calendar/",
            data="",
            content_type="application/xml",
            HTTP_DEPTH="0",
            **self._basic_auth(),
        )
        self.assertEqual(response.status_code, 404)


class DavPureFunctionTests(SimpleTestCase):
    @staticmethod
    def _matches_time_range(component_text, time_range):
        return core_query.matches_time_range(
            component_text,
            time_range,
            core_time.parse_ical_datetime,
            core_recurrence.matches_time_range_recurrence,
            core_recurrence.parse_line_datetime_with_tz,
            core_time.first_ical_line,
            core_time.parse_ical_duration,
            core_time.first_ical_line_value,
        )

    @classmethod
    def _matches_comp_filter(cls, context_text, comp_filter):
        return core_query.matches_comp_filter(
            context_text,
            comp_filter,
            core_recurrence.extract_component_blocks,
            cls._matches_time_range,
            cls._matches_prop_filter,
            core_recurrence.alarm_matches_time_range,
            core_filters.combine_filter_results,
        )

    @staticmethod
    def _matches_prop_filter(component_text, prop_filter):
        return core_filters.matches_prop_filter(
            component_text,
            prop_filter,
            core_recurrence.line_matches_time_range,
        )

    def test_validate_ical_and_generic_payloads(self):
        valid_payload = (
            b"BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:test-uid\nEND:VEVENT\nEND:VCALENDAR\n"
        )
        parsed, error = core_payloads.validate_ical_payload(valid_payload)
        self.assertIsNone(error)
        if parsed is None:
            self.fail("Expected parsed payload")
        self.assertEqual(parsed["uid"], "test-uid")

        parsed, error = core_payloads.validate_ical_payload(b"\xff")
        self.assertIsNone(parsed)
        self.assertEqual(error, "Calendar payload must be UTF-8 text.")

        parsed, error = core_payloads.validate_ical_payload(
            b"BEGIN:VEVENT\nUID:x\nEND:VEVENT"
        )
        self.assertIsNone(parsed)
        self.assertEqual(
            error,
            "Calendar payload must contain VCALENDAR boundaries.",
        )

        parsed, error = core_payloads.validate_generic_payload(b"hello")
        self.assertIsNone(error)
        self.assertEqual(parsed, {"text": "hello", "uid": None})

    def test_if_match_and_precondition_helpers(self):
        self.assertEqual(
            core_payloads.if_match_values('"a", "b" ,'),
            ['"a"', '"b"'],
        )

        request = type("Request", (), {"headers": {"If-None-Match": "*"}})()
        existing = type("Obj", (), {"etag": '"etag-1"'})()
        self.assertTrue(core_payloads.precondition_failed_for_write(request, existing))

        request = type("Request", (), {"headers": {"If-Match": '"etag-2"'}})()
        self.assertTrue(core_payloads.precondition_failed_for_write(request, None))
        self.assertTrue(core_payloads.precondition_failed_for_write(request, existing))

        request = type("Request", (), {"headers": {"If-Match": "*"}})()
        self.assertFalse(core_payloads.precondition_failed_for_write(request, existing))

    def test_collection_and_path_helpers(self):
        self.assertEqual(core_paths.collection_marker(""), "")
        self.assertEqual(core_paths.collection_marker("foo/bar"), "foo/bar/")
        self.assertEqual(
            core_paths.split_filename_path("foo/bar.ics"),
            ("foo", "bar.ics"),
        )
        self.assertEqual(core_paths.split_filename_path("/"), ("", ""))

    def test_destination_filename_from_header(self):
        filename = core_paths.destination_filename_from_header(
            "https://example.com/dav/calendars/alice/home/file.ics",
            "alice",
            "home",
        )
        self.assertEqual(filename, "file.ics")

        filename = core_paths.destination_filename_from_header(
            "/dav/calendars/users/alice/home/folder/item.ics",
            "alice",
            "home",
        )
        self.assertEqual(filename, "folder/item.ics")

        filename = core_paths.destination_filename_from_header(
            "/dav/calendars/bob/home/file.ics",
            "alice",
            "home",
        )
        self.assertIsNone(filename)

    def test_content_type_and_href_helpers(self):
        self.assertTrue(
            core_paths.is_ical_resource("event.ics", "application/octet-stream")
        )
        self.assertTrue(
            core_paths.is_ical_resource("event.txt", "text/calendar; charset=utf-8")
        )
        self.assertFalse(core_paths.is_ical_resource("event.txt", "text/plain"))

        self.assertEqual(
            core_paths.normalize_content_type("text/calendar; charset=utf-8"),
            "text/calendar;charset=utf-8",
        )
        self.assertEqual(
            core_paths.normalize_content_type(None),
            "application/octet-stream",
        )

        root = ET.fromstring(b"<root><child/></root>")
        if root is None:
            self.fail("Expected parsed xml root")
        self.assertEqual(root.tag, "root")
        with self.assertRaises(ET.ParseError):
            ET.fromstring(b"<root>")

        self.assertEqual(
            core_paths.normalize_href_path("calendar/file.ics"),
            "/calendar/file.ics",
        )
        self.assertEqual(
            core_paths.normalize_href_path("https://example.com/a/b"),
            "/a/b",
        )

    def test_datetime_and_duration_helpers(self):
        parsed = core_time.parse_ical_datetime("20260220")
        self.assertEqual(parsed, datetime(2026, 2, 20, tzinfo=datetime_timezone.utc))

        parsed = core_time.parse_ical_datetime("20260220T101112Z")
        self.assertEqual(
            parsed,
            datetime(2026, 2, 20, 10, 11, 12, tzinfo=datetime_timezone.utc),
        )
        self.assertIsNone(core_time.parse_ical_datetime("not-a-date"))

        duration = core_time.parse_ical_duration("-P1DT2H3M4S")
        self.assertEqual(duration, -timedelta(days=1, hours=2, minutes=3, seconds=4))
        self.assertEqual(core_time.format_ical_duration(timedelta(0)), "PT0S")
        self.assertEqual(
            core_time.format_ical_duration(timedelta(days=1, minutes=5)),
            "P1DT5M",
        )

    def test_ical_line_and_filter_helpers(self):
        ical_text = (
            "BEGIN:VEVENT\r\n"
            "SUMMARY;LANGUAGE=en:Hello\r\n"
            "DTSTART;TZID=UTC:20260220T101112\r\n"
            "END:VEVENT\r\n"
        )
        self.assertEqual(
            core_time.first_ical_line_value(ical_text, "SUMMARY"),
            "Hello",
        )
        dtstart_line = core_time.first_ical_line(ical_text, "DTSTART")
        if dtstart_line is None:
            self.fail("Expected DTSTART line")
        self.assertEqual(dtstart_line.rstrip("\r"), "DTSTART;TZID=UTC:20260220T101112")

        dt = core_recurrence.parse_line_datetime_with_tz(
            "DTSTART;TZID=UTC:20260220T101112"
        )
        self.assertEqual(
            dt,
            datetime(2026, 2, 20, 10, 11, 12, tzinfo=datetime_timezone.utc),
        )

        self.assertTrue(
            core_recurrence.line_matches_time_range(
                "DTSTART:20260220T101112Z",
                {"start": "20260220T090000Z", "end": "20260220T110000Z"},
            )
        )
        self.assertFalse(
            core_recurrence.line_matches_time_range(
                "DTSTART:20260220T101112Z",
                {"start": "20260220T120000Z", "end": "20260220T130000Z"},
            )
        )

    def test_utc_and_property_helpers(self):
        naive = datetime(2026, 2, 20, 10, 11, 12)
        aware = datetime(2026, 2, 20, 10, 11, 12, tzinfo=datetime_timezone.utc)
        day = date(2026, 2, 20)
        self.assertEqual(
            core_time.as_utc_datetime(naive),
            datetime(2026, 2, 20, 10, 11, 12, tzinfo=datetime_timezone.utc),
        )
        self.assertEqual(core_time.as_utc_datetime(aware), aware)
        self.assertEqual(
            core_time.as_utc_datetime(day),
            datetime(2026, 2, 20, 0, 0, tzinfo=datetime_timezone.utc),
        )

        unfolded = core_time.unfold_ical("SUMMARY:Hello\r\n World")
        self.assertEqual(unfolded, "SUMMARY:HelloWorld")

        blocks = core_recurrence.extract_component_blocks(
            "BEGIN:VEVENT\nUID:1\nEND:VEVENT\nBEGIN:VTODO\nUID:2\nEND:VTODO\n",
            "VEVENT",
        )
        self.assertEqual(len(blocks), 1)

        lines = core_filters.property_lines(
            "SUMMARY:One\nSUMMARY;LANG=en:Two\nUID:1\n",
            "summary",
        )
        self.assertEqual(lines, ["SUMMARY:One", "SUMMARY;LANG=en:Two"])

        params = core_filters.parse_property_params(
            "ATTENDEE;ROLE=REQ-PARTICIPANT;CUTYPE=INDIVIDUAL:mailto:a@example.com"
        )
        self.assertEqual(params["ROLE"], ["REQ-PARTICIPANT"])
        self.assertEqual(params["CUTYPE"], ["INDIVIDUAL"])

    def test_text_match_and_combine_filter_results(self):
        matcher = ET.fromstring(
            '<C:text-match xmlns:C="urn:ietf:params:xml:ns:caldav" match-type="starts-with">hel</C:text-match>'
        )
        self.assertTrue(core_filters.text_match("Hello", matcher))

        negate_matcher = ET.fromstring(
            '<C:text-match xmlns:C="urn:ietf:params:xml:ns:caldav" negate-condition="yes">zzz</C:text-match>'
        )
        self.assertTrue(core_filters.text_match("Hello", negate_matcher))

        self.assertTrue(core_filters.combine_filter_results([True, False], "anyof"))
        self.assertFalse(core_filters.combine_filter_results([True, False], "allof"))

    def test_matches_param_and_prop_filters(self):
        component_text = (
            "BEGIN:VEVENT\n"
            "SUMMARY:Planning\n"
            "ATTENDEE;ROLE=REQ-PARTICIPANT:mailto:a@example.com\n"
            "DTSTART:20260220T100000Z\n"
            "END:VEVENT\n"
        )
        prop_lines = core_filters.property_lines(component_text, "ATTENDEE")

        role_filter = ET.fromstring(
            '<C:param-filter xmlns:C="urn:ietf:params:xml:ns:caldav" name="ROLE"><C:text-match>REQ</C:text-match></C:param-filter>'
        )
        self.assertTrue(core_filters.matches_param_filter(prop_lines, role_filter))

        missing_filter = ET.fromstring(
            '<C:param-filter xmlns:C="urn:ietf:params:xml:ns:caldav" name="CUTYPE"><C:is-not-defined/></C:param-filter>'
        )
        self.assertTrue(core_filters.matches_param_filter(prop_lines, missing_filter))

        summary_filter = ET.fromstring(
            '<C:prop-filter xmlns:C="urn:ietf:params:xml:ns:caldav" name="SUMMARY"><C:text-match>plan</C:text-match></C:prop-filter>'
        )
        self.assertTrue(self._matches_prop_filter(component_text, summary_filter))

        description_missing = ET.fromstring(
            '<C:prop-filter xmlns:C="urn:ietf:params:xml:ns:caldav" name="DESCRIPTION"><C:is-not-defined/></C:prop-filter>'
        )
        self.assertTrue(self._matches_prop_filter(component_text, description_missing))

    def test_matches_time_range_and_comp_filters(self):
        event = "BEGIN:VEVENT\nDTSTART;VALUE=DATE:20260220\nEND:VEVENT\n"
        in_range = {"start": "20260220T000000Z", "end": "20260221T000000Z"}
        out_of_range = {"start": "20260222T000000Z", "end": "20260223T000000Z"}
        self.assertTrue(self._matches_time_range(event, in_range))
        self.assertFalse(self._matches_time_range(event, out_of_range))

        calendar_without_master_alarm = (
            "BEGIN:VCALENDAR\n"
            "BEGIN:VEVENT\nUID:evt-1\nDTSTART:20260220T100000Z\nEND:VEVENT\n"
            "BEGIN:VEVENT\nUID:evt-1\nRECURRENCE-ID:20260221T100000Z\nBEGIN:VALARM\nTRIGGER:-PT15M\nEND:VALARM\nEND:VEVENT\n"
            "END:VCALENDAR\n"
        )
        no_alarm_filter = ET.fromstring(
            '<C:comp-filter xmlns:C="urn:ietf:params:xml:ns:caldav" name="VEVENT"><C:comp-filter name="VALARM"><C:is-not-defined/></C:comp-filter></C:comp-filter>'
        )
        self.assertTrue(
            self._matches_comp_filter(calendar_without_master_alarm, no_alarm_filter)
        )

    def test_recurrence_alarm_shift_and_filtered_data_helpers(self):
        recurring_event = (
            "BEGIN:VEVENT\n"
            "UID:evt-2\n"
            "DTSTART:20260220T100000Z\n"
            "RRULE:FREQ=DAILY;COUNT=2\n"
            "BEGIN:VALARM\n"
            "TRIGGER:-PT15M\n"
            "END:VALARM\n"
            "END:VEVENT\n"
        )
        self.assertTrue(
            core_recurrence.matches_time_range_recurrence(
                recurring_event,
                datetime(2026, 2, 20, 0, 0, tzinfo=datetime_timezone.utc),
                datetime(2026, 2, 22, 0, 0, tzinfo=datetime_timezone.utc),
                "VEVENT",
            )
        )
        self.assertTrue(
            core_recurrence.alarm_matches_time_range(
                recurring_event,
                {
                    "start": "20260220T094000Z",
                    "end": "20260220T095000Z",
                },
            )
        )

        shifted = core_calendar_data.ensure_shifted_first_occurrence_recurrence_id(
            "BEGIN:VEVENT\r\nUID:evt-3\r\nDTSTART:20260221T100000Z\r\nEND:VEVENT\r\n",
            {"evt-3": datetime(2026, 2, 20, 10, 0, tzinfo=datetime_timezone.utc)},
            None,
            core_recurrence.extract_component_blocks,
            core_time.first_ical_line_value,
            core_time.first_ical_line,
            core_time.format_value_date_or_datetime,
        )
        self.assertIn("RECURRENCE-ID:20260221T100000Z", shifted)

        calendar_data_request = ET.fromstring(
            '<C:calendar-data xmlns:C="urn:ietf:params:xml:ns:caldav"><C:prop name="SUMMARY"/></C:calendar-data>'
        )
        filtered = core_calendar_data.filter_calendar_data_for_response(
            "BEGIN:VCALENDAR\r\nDTSTAMP:20260220T100000Z\r\nEND:VCALENDAR\r\n",
            calendar_data_request,
            None,
            core_time.parse_ical_datetime,
            core_time.as_utc_datetime,
            lambda *args, **kwargs: "",
            lambda ical_blob, _master_starts, _tzinfo: ical_blob,
        )
        self.assertNotIn("DTSTAMP", filtered)
        self.assertTrue(filtered.endswith("\r\n"))

    def test_freebusy_and_object_query_helpers(self):
        parsed = core_freebusy.parse_freebusy_value(
            "20260220T100000Z/PT1H",
            core_time.parse_ical_datetime,
            core_time.parse_ical_duration,
            core_time.as_utc_datetime,
        )
        self.assertIsNotNone(parsed)
        self.assertIsNone(
            core_freebusy.parse_freebusy_value(
                "bad-value",
                core_time.parse_ical_datetime,
                core_time.parse_ical_duration,
                core_time.as_utc_datetime,
            )
        )

        query_filter = ET.fromstring(
            '<C:comp-filter xmlns:C="urn:ietf:params:xml:ns:caldav" name="VEVENT" />'
        )
        obj = type(
            "Obj",
            (),
            {
                "ical_blob": "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:ok\nEND:VEVENT\nEND:VCALENDAR\n"
            },
        )()
        self.assertTrue(
            core_query.object_matches_query(
                obj.ical_blob,
                query_filter,
                core_time.unfold_ical,
                self._matches_comp_filter,
            )
        )

    def test_remap_uid_for_copied_object(self):
        self.assertEqual(
            _remap_uid_for_copied_object("collection:old/", "new/"),
            "collection:new/",
        )
        self.assertEqual(
            _remap_uid_for_copied_object("dav:old.ics", "new.ics"),
            "dav:new.ics",
        )
        self.assertEqual(
            _remap_uid_for_copied_object("uid-123", "new.ics"),
            "uid-123",
        )

    def test_sync_token_revision_from_parts(self):
        calendar_id = UUID("11111111-1111-1111-1111-111111111111")
        self.assertEqual(
            _sync_token_revision_from_parts(
                ["11111111-1111-1111-1111-111111111111", "7"],
                calendar_id,
            ),
            7,
        )
        self.assertIsNone(_sync_token_revision_from_parts(["bad", "7"], calendar_id))
        self.assertIsNone(
            _sync_token_revision_from_parts(
                ["22222222-2222-2222-2222-222222222222", "7"],
                calendar_id,
            )
        )
        self.assertIsNone(
            _sync_token_revision_from_parts(
                ["11111111-1111-1111-1111-111111111111", "-1"],
                calendar_id,
            )
        )

    def test_build_freebusy_response_lines(self):
        start = datetime(2026, 2, 20, 10, 0, tzinfo=datetime_timezone.utc)
        end = datetime(2026, 2, 20, 11, 0, tzinfo=datetime_timezone.utc)
        lines = _build_freebusy_response_lines(
            start,
            end,
            [(start, end)],
            [],
            [],
        )
        self.assertEqual(lines[0], "BEGIN:VCALENDAR")
        self.assertIn("BEGIN:VFREEBUSY", lines)
        self.assertTrue(any(line.startswith("FREEBUSY:") for line in lines))
        self.assertEqual(lines[-2], "END:VCALENDAR")

    def test_calendar_collection_proppatch_plan(self):
        root = ET.fromstring(
            """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<A:propertyupdate xmlns:A=\"DAV:\" xmlns:C=\"urn:ietf:params:xml:ns:caldav\" xmlns:G=\"http://apple.com/ns/ical/\">
  <A:set>
    <A:prop>
      <A:displayname>Renamed</A:displayname>
      <C:calendar-description>Desc</C:calendar-description>
      <G:calendar-color>#112233</G:calendar-color>
      <G:calendar-order>5</G:calendar-order>
    </A:prop>
  </A:set>
</A:propertyupdate>"""
        )
        current_values = {
            "name": "Family",
            "description": "",
            "timezone": "UTC",
            "color": "",
            "sort_order": None,
        }
        pending_values, update_fields, ok_tags, bad_tags = (
            _calendar_collection_proppatch_plan(
                root,
                "family",
                current_values,
            )
        )
        self.assertEqual(pending_values["name"], "Renamed")
        self.assertEqual(pending_values["description"], "Desc")
        self.assertEqual(pending_values["color"], "#112233")
        self.assertEqual(pending_values["sort_order"], 5)
        self.assertIn("name", update_fields)
        self.assertIn("description", update_fields)
        self.assertIn("color", update_fields)
        self.assertIn("sort_order", update_fields)
        self.assertTrue(ok_tags)
        self.assertEqual(bad_tags, [])

    def test_calendar_collection_proppatch_plan_rejects_bad_values(self):
        root = ET.fromstring(
            """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<A:propertyupdate xmlns:A=\"DAV:\" xmlns:C=\"urn:ietf:params:xml:ns:caldav\" xmlns:G=\"http://apple.com/ns/ical/\">
  <A:set>
    <A:prop>
      <C:calendar-timezone>NOT_A_TZ</C:calendar-timezone>
      <G:calendar-order>not-int</G:calendar-order>
    </A:prop>
  </A:set>
</A:propertyupdate>"""
        )
        pending_values, update_fields, ok_tags, bad_tags = (
            _calendar_collection_proppatch_plan(
                root,
                "family",
                {
                    "name": "Family",
                    "description": "",
                    "timezone": "UTC",
                    "color": "",
                    "sort_order": None,
                },
            )
        )
        self.assertEqual(pending_values["timezone"], "UTC")
        self.assertEqual(pending_values["sort_order"], None)
        self.assertEqual(update_fields, set())
        self.assertEqual(ok_tags, [])
        self.assertEqual(len(bad_tags), 2)

    def test_parse_sync_token_for_calendar_data_and_path_forms(self):
        calendar = SimpleNamespace(id=UUID("11111111-1111-1111-1111-111111111111"))
        revision, error = _parse_sync_token_for_calendar(
            "data:,11111111-1111-1111-1111-111111111111/3",
            calendar,
        )
        self.assertEqual(revision, 3)
        self.assertIsNone(error)

        revision, error = _parse_sync_token_for_calendar(
            "/sync/11111111-1111-1111-1111-111111111111/4",
            calendar,
        )
        self.assertEqual(revision, 4)
        self.assertIsNone(error)

        revision, error = _parse_sync_token_for_calendar(
            "/sync/22222222-2222-2222-2222-222222222222/4",
            calendar,
        )
        self.assertIsNone(revision)
        self.assertIsNotNone(error)

    def test_parse_xml_body_helper(self):
        self.assertIsNotNone(_parse_xml_body(b"<root/>"))
        self.assertIsNone(_parse_xml_body(b"<root>"))

    def test_remote_ip_helper(self):
        self.assertEqual(
            _remote_ip("203.0.113.9, 10.0.0.1", "127.0.0.1"),
            "203.0.113.9",
        )
        self.assertEqual(
            _remote_ip("", "127.0.0.1"),
            "127.0.0.1",
        )
        self.assertEqual(
            _remote_ip(None, None),
            "",
        )

    def test_calendar_default_tzinfo_helper(self):
        self.assertEqual(
            _calendar_default_tzinfo(SimpleNamespace(timezone="")),
            datetime_timezone.utc,
        )
        self.assertEqual(
            getattr(
                _calendar_default_tzinfo(SimpleNamespace(timezone="UTC")),
                "key",
                "",
            ),
            "UTC",
        )
        self.assertEqual(
            _calendar_default_tzinfo(SimpleNamespace(timezone="Bad/Timezone")),
            datetime_timezone.utc,
        )

    def test_sync_collection_limit_helper(self):
        root = ET.fromstring('<D:sync-collection xmlns:D="DAV:" />')
        self.assertIsNone(_sync_collection_limit(root))

        root = ET.fromstring(
            '<D:sync-collection xmlns:D="DAV:"><D:limit><D:nresults>2</D:nresults></D:limit></D:sync-collection>'
        )
        self.assertEqual(_sync_collection_limit(root), 2)

        root = ET.fromstring(
            '<D:sync-collection xmlns:D="DAV:"><D:limit><D:nresults>0</D:nresults></D:limit></D:sync-collection>'
        )
        self.assertIsNone(_sync_collection_limit(root))

    def test_collection_and_object_href_style_helpers(self):
        owner = SimpleNamespace(username="user01")
        calendar = SimpleNamespace(owner=owner, slug="family")
        obj = SimpleNamespace(filename="item.ics")
        self.assertEqual(
            _report_href_style("/dav/calendars/__uids__/x/family/"),
            "uids",
        )
        self.assertEqual(
            _report_href_style("/dav/calendars/users/user01/family/"),
            "users",
        )
        self.assertEqual(
            _report_href_style("/dav/calendars/user01/family/"),
            "username",
        )
        self.assertIn(
            "/dav/calendars/users/user01/family/item.ics",
            _all_object_hrefs(calendar, obj),
        )

    def test_sync_token_helpers(self):
        calendar_id = UUID("11111111-1111-1111-1111-111111111111")
        self.assertEqual(
            _build_sync_token(calendar_id, 9),
            "data:,11111111-1111-1111-1111-111111111111/9",
        )

    def test_calendar_collection_proppatch_plan_remove_operations(self):
        root = ET.fromstring(
            """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<A:propertyupdate xmlns:A=\"DAV:\" xmlns:C=\"urn:ietf:params:xml:ns:caldav\" xmlns:G=\"http://apple.com/ns/ical/\">
  <A:remove>
    <A:prop>
      <A:displayname />
      <C:calendar-timezone />
      <G:calendar-order />
      <A:unsupported />
    </A:prop>
  </A:remove>
</A:propertyupdate>"""
        )
        pending_values, update_fields, ok_tags, bad_tags = (
            _calendar_collection_proppatch_plan(
                root,
                "family",
                {
                    "name": "Family Name",
                    "description": "Desc",
                    "timezone": "America/Chicago",
                    "color": "#000000",
                    "sort_order": 10,
                },
            )
        )
        self.assertEqual(pending_values["name"], "family")
        self.assertEqual(pending_values["timezone"], "UTC")
        self.assertEqual(pending_values["sort_order"], None)
        self.assertIn("name", update_fields)
        self.assertIn("timezone", update_fields)
        self.assertIn("sort_order", update_fields)
        self.assertEqual(len(ok_tags), 3)
        self.assertEqual(len(bad_tags), 1)

    def test_dedupe_duplicate_alarms_helper(self):
        input_ical = (
            "BEGIN:VCALENDAR\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:dup\r\n"
            "BEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER:-PT10M\r\nEND:VALARM\r\n"
            "BEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER:-PT10M\r\nEND:VALARM\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )
        output_ical = _dedupe_duplicate_alarms(input_ical)
        self.assertEqual(output_ical.count("BEGIN:VALARM"), 1)

    def test_guid_and_href_helpers(self):
        self.assertEqual(
            _dav_guid_for_username("user01"),
            "10000000-0000-0000-0000-000000000001",
        )
        self.assertIsNone(_dav_guid_for_username("owner"))
        self.assertEqual(
            _dav_username_for_guid("10000000-0000-0000-0000-000000000099"),
            "user99",
        )
        self.assertIsNone(
            _dav_username_for_guid("10000000-0000-0000-0000-000000000100")
        )

        user01 = SimpleNamespace(username="user01")
        owner = SimpleNamespace(username="owner")
        self.assertEqual(
            _principal_href_for_user(user01),
            "/dav/principals/__uids__/10000000-0000-0000-0000-000000000001/",
        )
        self.assertEqual(
            _principal_href_for_user(owner),
            "/dav/principals/users/owner/",
        )
        self.assertEqual(
            _calendar_home_href_for_user(user01),
            "/dav/calendars/__uids__/10000000-0000-0000-0000-000000000001/",
        )

    def test_tzinfo_from_report_helper(self):
        root = ET.fromstring(
            '<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav"><C:timezone-id>UTC</C:timezone-id></C:calendar-query>'
        )
        tzinfo, error = _tzinfo_from_report(root)
        self.assertIsNone(error)
        self.assertEqual(getattr(tzinfo, "key", ""), "UTC")

        bad = ET.fromstring(
            '<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav"><C:timezone-id>Bad/Timezone</C:timezone-id></C:calendar-query>'
        )
        tzinfo, error = _tzinfo_from_report(bad)
        self.assertIsNone(tzinfo)
        self.assertIsNotNone(error)

    def test_sync_collection_multistatus_document_helper(self):
        response = ET.Element("{DAV:}response")
        response.append(ET.Element("{DAV:}href"))
        xml_bytes = _sync_collection_multistatus_document(
            [response],
            "data:,11111111-1111-1111-1111-111111111111/1",
        )
        root = ET.fromstring(xml_bytes)
        token = root.find("{DAV:}sync-token")
        token_text = token.text if token is not None else None
        self.assertEqual(
            token_text,
            "data:,11111111-1111-1111-1111-111111111111/1",
        )

    def test_append_date_or_datetime_line_helper(self):
        lines = []
        _append_date_or_datetime_line(lines, "RECURRENCE-ID", "20260220", True)
        _append_date_or_datetime_line(
            lines,
            "RECURRENCE-ID",
            "20260220T100000Z",
            False,
        )
        self.assertEqual(lines[0], "RECURRENCE-ID;VALUE=DATE:20260220")
        self.assertEqual(lines[1], "RECURRENCE-ID:20260220T100000Z")

    def test_uid_drop_recurrence_map_helper(self):
        class FakeComponent:
            def __init__(self, uid, recurrence_id):
                self._uid = uid
                self._recurrence_id = recurrence_id

            def get(self, name):
                if name == "UID":
                    return self._uid
                return None

            def decoded(self, name, default=None):
                if name == "RECURRENCE-ID":
                    return self._recurrence_id
                return default

        uid = "series-1"
        components = [
            FakeComponent(
                uid, datetime(2026, 2, 21, 10, 0, tzinfo=datetime_timezone.utc)
            ),
            FakeComponent(
                uid, datetime(2026, 2, 20, 10, 0, tzinfo=datetime_timezone.utc)
            ),
        ]
        drop_map = _uid_drop_recurrence_map(components, None)
        self.assertEqual(drop_map[uid], "20260220T100000Z")

        components_with_master = [
            FakeComponent(uid, None),
            FakeComponent(
                uid, datetime(2026, 2, 20, 10, 0, tzinfo=datetime_timezone.utc)
            ),
            FakeComponent(
                uid, datetime(2026, 2, 21, 10, 0, tzinfo=datetime_timezone.utc)
            ),
        ]
        drop_map = _uid_drop_recurrence_map(components_with_master, None)
        self.assertNotIn(uid, drop_map)

    def test_resolved_recurrence_text_helper(self):
        class FakeComponent:
            def __init__(self, recurrence_id=None, rrule=None, exdate=None):
                self._recurrence_id = recurrence_id
                self._rrule = rrule
                self._exdate = exdate

            def decoded(self, name, default=None):
                if name == "RECURRENCE-ID":
                    return self._recurrence_id
                return default

            def get(self, name):
                if name == "RRULE":
                    return self._rrule
                if name == "EXDATE":
                    return self._exdate
                return None

        component = FakeComponent(
            recurrence_id=datetime(2026, 2, 20, 10, 0, tzinfo=datetime_timezone.utc)
        )
        rec_text, rec_is_date = _resolved_recurrence_text(
            component,
            "uid-1",
            None,
            "20260220T100000Z",
            False,
            None,
            None,
            {},
        )
        self.assertEqual(rec_text, "20260220T100000Z")
        self.assertFalse(rec_is_date)

        component = FakeComponent()
        rec_text, _ = _resolved_recurrence_text(
            component,
            "uid-1",
            None,
            "20260221T100000Z",
            False,
            {"uid-1": datetime(2026, 2, 20, 10, 0, tzinfo=datetime_timezone.utc)},
            None,
            {},
        )
        self.assertEqual(rec_text, "20260221T100000Z")

        component = FakeComponent(rrule="FREQ=DAILY", exdate="20260220T100000Z")
        rec_text, _ = _resolved_recurrence_text(
            component,
            "uid-2",
            None,
            "20260222T100000Z",
            False,
            None,
            None,
            {},
        )
        self.assertEqual(rec_text, "20260222T100000Z")

        component = FakeComponent()
        rec_text, _ = _resolved_recurrence_text(
            component,
            "uid-3",
            None,
            "20260223T100000Z",
            False,
            None,
            {"uid-3"},
            {},
        )
        self.assertEqual(rec_text, "20260223T100000Z")

        component = FakeComponent(
            recurrence_id=datetime(2026, 2, 24, 10, 0, tzinfo=datetime_timezone.utc)
        )
        rec_text, _ = _resolved_recurrence_text(
            component,
            "uid-4",
            None,
            "20260224T100000Z",
            False,
            None,
            None,
            {"uid-4": "20260224T100000Z"},
        )
        self.assertIsNone(rec_text)

    def test_users_alias_bindings_and_sync_token_helper_re_export(self):
        self.assertIs(
            entrypoints.principal_users_view,
            entrypoints.principal_view,
        )
        self.assertIs(
            entrypoints.calendar_home_users_view,
            entrypoints.calendar_home_view,
        )
        self.assertIs(
            entrypoints.calendar_collection_users_view,
            entrypoints.calendar_collection_view,
        )
        self.assertIs(
            entrypoints.calendar_object_users_view,
            entrypoints.calendar_object_view,
        )
        self.assertIs(
            _sync_token_revision_from_parts,
            sync_token_helpers._sync_token_revision_from_parts,
        )
