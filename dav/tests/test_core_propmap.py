from datetime import datetime, timezone
from xml.etree import ElementTree as ET

from django.test import SimpleTestCase

import dav.core.propmap as core_propmap
from dav.xml import NS_CALDAV, NS_DAV, qname


class _User:
    def __init__(self, username, user_id=1):
        self.username = username
        self.id = user_id


class _Calendar:
    def __init__(self):
        self.name = "Family"
        self.slug = "family"
        self.owner = _User("owner")
        self.owner_id = self.owner.id
        self.updated_at = datetime(2026, 2, 19, tzinfo=timezone.utc)
        self.component_kind = "VEVENT"
        self.timezone = "UTC"
        self.description = "desc"
        self.color = "#112233"
        self.sort_order = 1


class _Object:
    def __init__(self):
        self.etag = '"etag"'
        self.content_type = "text/calendar"
        self.ical_blob = "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"
        self.size = len(self.ical_blob)
        self.dead_properties = {}


class DavCorePropmapTests(SimpleTestCase):
    def test_root_prop_map(self):
        user = _User("owner")
        prop_map = core_propmap.build_root_prop_map(
            user,
            lambda principal_user: f"/dav/principals/{principal_user.username}/",
        )
        current = prop_map[qname(NS_DAV, "current-user-principal")]()
        href = current.find(qname(NS_DAV, "href"))
        self.assertIsNotNone(href)
        self.assertEqual(href.text, "/dav/principals/owner/")

    def test_calendar_collection_prop_map_includes_sync_token(self):
        calendar = _Calendar()
        auth_user = calendar.owner
        prop_map = core_propmap.build_calendar_collection_prop_map(
            calendar,
            auth_user,
            lambda owner: f"/dav/principals/{owner.username}/",
            lambda _calendar: "data:,token/1",
        )
        token = prop_map[qname(NS_DAV, "sync-token")]()
        self.assertEqual(token.text, "data:,token/1")

    def test_object_prop_map_contains_calendar_data(self):
        obj = _Object()
        calendar_data = core_propmap.build_object_prop_map(
            obj=obj,
            etag_for_object=lambda value: value.etag,
            getlastmodified_text="Thu, 19 Feb 2026 00:00:00 GMT",
            calendar_data_element=ET.Element(qname(NS_CALDAV, "calendar-data")),
        )
        self.assertIn(qname(NS_CALDAV, "calendar-data"), calendar_data)
        self.assertIn(qname(NS_DAV, "getetag"), calendar_data)
