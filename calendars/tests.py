# pyright: reportAttributeAccessIssue=false

from django.test import TestCase
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.urls import reverse
from django.utils import timezone

from .models import Calendar, CalendarObject, CalendarObjectChange, CalendarShare
from .forms import ShareCreateForm
from .permissions import (
    calendars_for_user,
    can_manage_calendar,
    can_view_calendar,
    can_write_calendar,
)


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

    def test_model_string_representations(self):
        owner = User.objects.create_user(username="owner-str", password="pw-test-12345")
        member = User.objects.create_user(
            username="member-str",
            password="pw-test-12345",
        )
        calendar = Calendar.objects.create(owner=owner, slug="sluggy", name="Name")
        share = CalendarShare.objects.create(
            calendar=calendar,
            user=member,
            role=CalendarShare.READ,
        )
        obj = CalendarObject.objects.create(
            calendar=calendar,
            uid="u",
            filename="f.ics",
            etag="e",
            ical_blob="BEGIN:VCALENDAR\nEND:VCALENDAR\n",
            size=1,
        )
        change = CalendarObjectChange.objects.create(
            calendar=calendar,
            revision=1,
            filename="f.ics",
            uid="u",
            is_deleted=False,
        )

        self.assertEqual(str(calendar), "sluggy")
        self.assertIn("sluggy", str(share))
        self.assertIn("f.ics", str(obj))
        self.assertIn(":1:", str(change))


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

    def test_calendar_create_get_renders_form(self):
        self.client.login(username="owner", password="pw-test-12345")

        response = self.client.get(reverse("calendars:create"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "calendars/form.html")

    def test_calendar_create_invalid_post_renders_form(self):
        self.client.login(username="owner", password="pw-test-12345")

        response = self.client.post(
            reverse("calendars:create"),
            {
                "slug": "",
                "name": "",
                "timezone": "UTC",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "calendars/form.html")

    def test_calendar_edit_get_and_post(self):
        self.client.login(username="owner", password="pw-test-12345")

        get_response = self.client.get(
            reverse("calendars:edit", args=[self.calendar.id])
        )
        self.assertEqual(get_response.status_code, 200)
        self.assertTemplateUsed(get_response, "calendars/form.html")

        post_response = self.client.post(
            reverse("calendars:edit", args=[self.calendar.id]),
            {
                "slug": "family",
                "name": "Family Updated",
                "description": "Updated",
                "color": "#102030",
                "timezone": "UTC",
            },
        )
        self.assertRedirects(
            post_response,
            reverse("calendars:edit", args=[self.calendar.id]),
        )
        self.calendar.refresh_from_db()
        self.assertEqual(self.calendar.name, "Family Updated")

    def test_calendar_delete_get_and_post(self):
        self.client.login(username="owner", password="pw-test-12345")
        calendar = Calendar.objects.create(owner=self.owner, slug="temp", name="Temp")

        get_response = self.client.get(reverse("calendars:delete", args=[calendar.id]))
        self.assertEqual(get_response.status_code, 200)
        self.assertTemplateUsed(get_response, "calendars/delete_confirm.html")

        post_response = self.client.post(
            reverse("calendars:delete", args=[calendar.id])
        )
        self.assertRedirects(post_response, reverse("calendars:list"))
        self.assertFalse(Calendar.objects.filter(id=calendar.id).exists())

    def test_calendar_share_add_non_post_redirects(self):
        self.client.login(username="owner", password="pw-test-12345")

        response = self.client.get(
            reverse("calendars:share-add", args=[self.calendar.id])
        )

        self.assertRedirects(
            response, reverse("calendars:sharing", args=[self.calendar.id])
        )

    def test_calendar_share_add_invalid_post_renders_sharing(self):
        self.client.login(username="owner", password="pw-test-12345")

        response = self.client.post(
            reverse("calendars:share-add", args=[self.calendar.id]),
            {
                "username": "",
                "role": CalendarShare.READ,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "calendars/sharing.html")

    def test_calendar_share_update_non_post_redirects(self):
        share = CalendarShare.objects.get(calendar=self.calendar, user=self.reader)
        self.client.login(username="adminu", password="pw-test-12345")

        response = self.client.get(
            reverse("calendars:share-update", args=[self.calendar.id, share.id])
        )

        self.assertRedirects(
            response, reverse("calendars:sharing", args=[self.calendar.id])
        )

    def test_calendar_share_update_invalid_post_keeps_role(self):
        share = CalendarShare.objects.get(calendar=self.calendar, user=self.reader)
        self.client.login(username="adminu", password="pw-test-12345")

        response = self.client.post(
            reverse("calendars:share-update", args=[self.calendar.id, share.id]),
            {"role": "invalid-role"},
        )

        self.assertRedirects(
            response, reverse("calendars:sharing", args=[self.calendar.id])
        )
        share.refresh_from_db()
        self.assertEqual(share.role, CalendarShare.READ)

    def test_calendar_share_delete_non_post_and_post(self):
        share = CalendarShare.objects.create(
            calendar=self.calendar,
            user=self.other,
            role=CalendarShare.READ,
        )
        self.client.login(username="owner", password="pw-test-12345")

        get_response = self.client.get(
            reverse("calendars:share-delete", args=[self.calendar.id, share.id])
        )
        self.assertRedirects(
            get_response, reverse("calendars:sharing", args=[self.calendar.id])
        )
        self.assertTrue(CalendarShare.objects.filter(id=share.id).exists())

        post_response = self.client.post(
            reverse("calendars:share-delete", args=[self.calendar.id, share.id])
        )
        self.assertRedirects(
            post_response, reverse("calendars:sharing", args=[self.calendar.id])
        )
        self.assertFalse(CalendarShare.objects.filter(id=share.id).exists())

    def test_share_invite_accept_and_decline_get_only_redirect(self):
        invite = CalendarShare.objects.create(
            calendar=self.calendar,
            user=self.other,
            role=CalendarShare.READ,
        )
        self.client.login(username="other", password="pw-test-12345")

        accept_response = self.client.get(
            reverse("calendars:invite-accept", args=[invite.id])
        )
        decline_response = self.client.get(
            reverse("calendars:invite-decline", args=[invite.id])
        )

        self.assertRedirects(accept_response, reverse("calendars:list"))
        self.assertRedirects(decline_response, reverse("calendars:list"))
        invite.refresh_from_db()
        self.assertIsNone(invite.accepted_at)
        self.assertTrue(CalendarShare.objects.filter(id=invite.id).exists())


class CalendarFormsAndPermissionsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username="owner2", password="pw-test-12345"
        )
        cls.member = User.objects.create_user(
            username="member2",
            password="pw-test-12345",
        )
        cls.writer = User.objects.create_user(
            username="writer2",
            password="pw-test-12345",
        )
        cls.admin = User.objects.create_user(
            username="admin2", password="pw-test-12345"
        )
        cls.calendar = Calendar.objects.create(
            owner=cls.owner,
            slug="team",
            name="Team",
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
        CalendarShare.objects.create(
            calendar=cls.calendar,
            user=cls.admin,
            role=CalendarShare.ADMIN,
            accepted_at=timezone.now(),
        )

    def test_share_create_form_unknown_user(self):
        form = ShareCreateForm(
            data={"username": "no-such-user", "role": CalendarShare.READ},
            calendar=self.calendar,
        )

        self.assertFalse(form.is_valid())
        self.assertIsNotNone(form.errors)
        self.assertIn("username", form.errors or {})

    def test_share_create_form_rejects_existing_share(self):
        form = ShareCreateForm(
            data={"username": self.member.username, "role": CalendarShare.READ},
            calendar=self.calendar,
        )

        self.assertFalse(form.is_valid())
        self.assertIsNotNone(form.errors)
        self.assertIn("username", form.errors or {})

    def test_permission_helpers(self):
        self.assertTrue(can_view_calendar(self.calendar, self.owner))
        self.assertTrue(can_view_calendar(self.calendar, self.member))
        self.assertTrue(can_write_calendar(self.calendar, self.owner))
        self.assertTrue(can_write_calendar(self.calendar, self.writer))
        self.assertTrue(can_manage_calendar(self.calendar, self.admin))

        outsider = User.objects.create_user(
            username="outsider", password="pw-test-12345"
        )
        self.assertFalse(can_view_calendar(self.calendar, outsider))
        self.assertFalse(can_write_calendar(self.calendar, outsider))
        self.assertFalse(can_manage_calendar(self.calendar, outsider))

    def test_calendars_for_user(self):
        owner_calendars = list(calendars_for_user(self.owner))
        member_calendars = list(calendars_for_user(self.member))

        self.assertIn(self.calendar, owner_calendars)
        self.assertIn(self.calendar, member_calendars)
