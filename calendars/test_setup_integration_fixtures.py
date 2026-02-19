from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from calendars.management.commands.setup_integration_fixtures import _fixture_usernames
from calendars.models import Calendar


class SetupIntegrationFixturesCommandTests(TestCase):
    def test_fixture_usernames_shape(self):
        usernames = _fixture_usernames()
        self.assertEqual(usernames[:3], ["admin", "apprentice", "superuser"])
        self.assertIn("user01", usernames)
        self.assertIn("user40", usernames)
        self.assertEqual(len(usernames), 43)

    def test_command_creates_and_resets_expected_fixtures(self):
        user = User.objects.create_user(username="user01", password="old")
        calendar = Calendar.objects.create(owner=user, slug="calendar", name="Old")
        extra = Calendar.objects.create(owner=user, slug="calendar-none", name="Remove")
        calendar.calendar_objects.create(
            uid="u1",
            filename="one.ics",
            etag='"e1"',
            ical_blob="BEGIN:VCALENDAR\nEND:VCALENDAR\n",
            size=30,
        )
        self.assertTrue(Calendar.objects.filter(pk=extra.pk).exists())

        out = StringIO()
        call_command("setup_integration_fixtures", stdout=out)
        message = out.getvalue()
        self.assertIn("Integration fixtures are ready.", message)

        self.assertEqual(User.objects.filter(username="user01").count(), 1)
        u1 = User.objects.get(username="user01")
        self.assertTrue(u1.check_password("user01"))

        required = {"calendar", "tasks", "inbox", "outbox"}
        self.assertEqual(
            set(u1.owned_calendars.values_list("slug", flat=True)), required
        )

        self.assertFalse(Calendar.objects.filter(pk=extra.pk).exists())
        self.assertEqual(
            Calendar.objects.filter(owner__username="user01", slug="calendar")
            .first()
            .calendar_objects.count(),
            0,
        )
