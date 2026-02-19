from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from calendars.models import Calendar, CalendarObject, CalendarShare
from dav import resolver


class ResolverTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(username="owner", password="x")
        cls.viewer = User.objects.create_user(username="viewer", password="x")
        cls.writer = User.objects.create_user(username="writer", password="x")
        cls.outsider = User.objects.create_user(username="outsider", password="x")

        cls.calendar = Calendar.objects.create(
            owner=cls.owner,
            slug="work",
            name="Work",
        )
        CalendarShare.objects.create(
            calendar=cls.calendar,
            user=cls.viewer,
            role=CalendarShare.READ,
            accepted_at=timezone.now(),
        )
        CalendarShare.objects.create(
            calendar=cls.calendar,
            user=cls.writer,
            role=CalendarShare.WRITE,
            accepted_at=timezone.now(),
        )
        cls.obj = CalendarObject.objects.create(
            calendar=cls.calendar,
            uid="uid-1",
            filename="event.ics",
            etag='"e1"',
            ical_blob="BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n",
            size=32,
        )

    def test_get_principal(self):
        self.assertEqual(resolver.get_principal("owner"), self.owner)
        self.assertIsNone(resolver.get_principal("missing"))

    def test_get_calendar_for_user(self):
        self.assertEqual(
            resolver.get_calendar_for_user(self.owner, "owner", "work"),
            self.calendar,
        )
        self.assertEqual(
            resolver.get_calendar_for_user(self.viewer, "owner", "work"),
            self.calendar,
        )
        self.assertIsNone(
            resolver.get_calendar_for_user(self.outsider, "owner", "work")
        )
        self.assertIsNone(
            resolver.get_calendar_for_user(self.owner, "owner", "missing")
        )

    def test_get_calendar_for_write_user(self):
        self.assertEqual(
            resolver.get_calendar_for_write_user(self.owner, "owner", "work"),
            self.calendar,
        )
        self.assertEqual(
            resolver.get_calendar_for_write_user(self.writer, "owner", "work"),
            self.calendar,
        )
        self.assertFalse(
            resolver.get_calendar_for_write_user(self.viewer, "owner", "work")
        )
        self.assertIsNone(
            resolver.get_calendar_for_write_user(self.owner, "owner", "missing")
        )

    def test_get_calendar_object_for_user(self):
        self.assertEqual(
            resolver.get_calendar_object_for_user(
                self.owner, "owner", "work", "event.ics"
            ),
            self.obj,
        )
        self.assertEqual(
            resolver.get_calendar_object_for_user(
                self.viewer, "owner", "work", "event.ics"
            ),
            self.obj,
        )
        self.assertIsNone(
            resolver.get_calendar_object_for_user(
                self.owner,
                "owner",
                "work",
                "missing.ics",
            )
        )
        self.assertIsNone(
            resolver.get_calendar_object_for_user(
                self.outsider,
                "owner",
                "work",
                "event.ics",
            )
        )
