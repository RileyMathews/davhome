# pyright: reportAttributeAccessIssue=false

from django.test import TestCase
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.urls import reverse
from django.utils import timezone

from .models import Calendar, CalendarObject, CalendarShare


class CalendarModelTests(TestCase):
    def test_calendar_share_unique_per_user(self):
        owner = User.objects.create_user(username="owner", password="pw-test-12345")
        member = User.objects.create_user(username="member", password="pw-test-12345")
        calendar = Calendar.objects.create(owner=owner, slug="family", name="Family")

        CalendarShare.objects.create(
            calendar=calendar,
            user=member,
            role=CalendarShare.READ,
        )

        with self.assertRaises(IntegrityError):
            CalendarShare.objects.create(
                calendar=calendar,
                user=member,
                role=CalendarShare.WRITE,
            )

    def test_calendar_object_uniqueness_constraints(self):
        owner = User.objects.create_user(username="owner", password="pw-test-12345")
        calendar = Calendar.objects.create(owner=owner, slug="family", name="Family")

        CalendarObject.objects.create(
            calendar=calendar,
            uid="uid-1",
            filename="one.ics",
            etag="abc",
            ical_blob="BEGIN:VCALENDAR\nEND:VCALENDAR\n",
            size=30,
        )

        with self.assertRaises(IntegrityError):
            CalendarObject.objects.create(
                calendar=calendar,
                uid="uid-1",
                filename="two.ics",
                etag="def",
                ical_blob="BEGIN:VCALENDAR\nEND:VCALENDAR\n",
                size=30,
            )


class CalendarViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(username="owner", password="pw-test-12345")
        cls.reader = User.objects.create_user(
            username="reader", password="pw-test-12345"
        )
        cls.admin = User.objects.create_user(
            username="adminu", password="pw-test-12345"
        )
        cls.other = User.objects.create_user(username="other", password="pw-test-12345")

        cls.calendar = Calendar.objects.create(
            owner=cls.owner,
            slug="family",
            name="Family",
            timezone="UTC",
        )
        CalendarShare.objects.create(
            calendar=cls.calendar,
            user=cls.reader,
            role=CalendarShare.READ,
            accepted_at=timezone.now(),
        )
        CalendarShare.objects.create(
            calendar=cls.calendar,
            user=cls.admin,
            role=CalendarShare.ADMIN,
            accepted_at=timezone.now(),
        )

    def test_calendar_list_requires_auth(self):
        response = self.client.get(reverse("calendars:list"))
        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('calendars:list')}",
        )

    def test_calendar_create(self):
        self.client.login(username="owner", password="pw-test-12345")
        response = self.client.post(
            reverse("calendars:create"),
            {
                "slug": "work",
                "name": "Work",
                "description": "Work events",
                "color": "#334455",
                "timezone": "UTC",
            },
        )
        created = Calendar.objects.get(owner=self.owner, slug="work")
        self.assertRedirects(response, reverse("calendars:edit", args=[created.id]))
        self.assertTrue(Calendar.objects.filter(owner=self.owner, slug="work").exists())

    def test_calendar_list_shows_shared(self):
        self.client.login(username="reader", password="pw-test-12345")
        response = self.client.get(reverse("calendars:list"))
        self.assertContains(response, "Family")
        self.assertContains(response, "Shared with me")

    def test_read_user_cannot_access_sharing_page(self):
        self.client.login(username="reader", password="pw-test-12345")
        response = self.client.get(
            reverse("calendars:sharing", args=[self.calendar.id])
        )
        self.assertEqual(response.status_code, 403)

    def test_owner_can_add_share(self):
        self.client.login(username="owner", password="pw-test-12345")
        response = self.client.post(
            reverse("calendars:share-add", args=[self.calendar.id]),
            {
                "username": "other",
                "role": CalendarShare.WRITE,
            },
        )
        self.assertRedirects(
            response, reverse("calendars:sharing", args=[self.calendar.id])
        )
        self.assertTrue(
            CalendarShare.objects.filter(
                calendar=self.calendar,
                user=self.other,
                role=CalendarShare.WRITE,
            ).exists()
        )
        self.assertTrue(
            CalendarShare.objects.filter(
                calendar=self.calendar,
                user=self.other,
                accepted_at__isnull=True,
            ).exists()
        )

    def test_cannot_share_with_owner(self):
        self.client.login(username="owner", password="pw-test-12345")
        response = self.client.post(
            reverse("calendars:share-add", args=[self.calendar.id]),
            {
                "username": "owner",
                "role": CalendarShare.READ,
            },
        )
        self.assertTemplateUsed(response, "calendars/sharing.html")
        self.assertContains(response, "Calendar owner already has full access.")

    def test_admin_can_update_share(self):
        self.client.login(username="adminu", password="pw-test-12345")
        share = CalendarShare.objects.get(calendar=self.calendar, user=self.reader)
        response = self.client.post(
            reverse("calendars:share-update", args=[self.calendar.id, share.id]),
            {"role": CalendarShare.WRITE},
        )
        self.assertRedirects(
            response, reverse("calendars:sharing", args=[self.calendar.id])
        )
        share.refresh_from_db()
        self.assertEqual(share.role, CalendarShare.WRITE)

    def test_pending_invite_not_in_shared_list_until_accept(self):
        CalendarShare.objects.create(
            calendar=self.calendar,
            user=self.other,
            role=CalendarShare.READ,
        )
        self.client.login(username="other", password="pw-test-12345")
        response = self.client.get(reverse("calendars:list"))
        self.assertNotContains(response, "Family by owner")
        self.assertContains(response, "Pending invitations")

    def test_user_can_accept_invite(self):
        invite = CalendarShare.objects.create(
            calendar=self.calendar,
            user=self.other,
            role=CalendarShare.READ,
        )
        self.client.login(username="other", password="pw-test-12345")
        response = self.client.post(
            reverse("calendars:invite-accept", args=[invite.id])
        )
        self.assertRedirects(response, reverse("calendars:list"))
        invite.refresh_from_db()
        self.assertIsNotNone(invite.accepted_at)

    def test_user_can_decline_invite(self):
        invite = CalendarShare.objects.create(
            calendar=self.calendar,
            user=self.other,
            role=CalendarShare.READ,
        )
        self.client.login(username="other", password="pw-test-12345")
        response = self.client.post(
            reverse("calendars:invite-decline", args=[invite.id])
        )
        self.assertRedirects(response, reverse("calendars:list"))
        self.assertFalse(CalendarShare.objects.filter(id=invite.id).exists())
