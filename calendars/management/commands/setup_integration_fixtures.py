from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from calendars.models import Calendar


def _fixture_usernames() -> list[str]:
    return ["admin", "apprentice", "superuser"] + [f"user{i:02d}" for i in range(1, 41)]


class Command(BaseCommand):
    help = "Create and reset deterministic integration fixtures"

    def handle(self, *args, **options):
        users = _fixture_usernames()

        for username in users:
            user, _ = User.objects.update_or_create(
                username=username,
                defaults={
                    "email": f"{username}@example.com",
                    "is_active": True,
                },
            )
            user.set_password(username)
            user.save(update_fields=["password", "is_active", "email"])

            Calendar.objects.update_or_create(
                owner=user,
                slug="calendar",
                defaults={
                    "name": "calendar",
                    "timezone": "UTC",
                    "component_kind": "VEVENT",
                },
            )
            Calendar.objects.update_or_create(
                owner=user,
                slug="tasks",
                defaults={
                    "name": "tasks",
                    "timezone": "UTC",
                    "component_kind": "VTODO",
                },
            )
            Calendar.objects.update_or_create(
                owner=user,
                slug="inbox",
                defaults={
                    "name": "inbox",
                    "timezone": "UTC",
                    "component_kind": "VEVENT",
                },
            )
            Calendar.objects.update_or_create(
                owner=user,
                slug="outbox",
                defaults={
                    "name": "outbox",
                    "timezone": "UTC",
                    "component_kind": "VEVENT",
                },
            )

            Calendar.objects.filter(
                owner__username=username,
                slug__in=[
                    "calendar-none",
                    "calendar-us",
                    "litmus",
                    "synccalendar1",
                    "synccalendar2",
                ],
            ).delete()

        for calendar in Calendar.objects.filter(
            owner__username__in=users,
            slug__in=["calendar", "tasks", "inbox", "outbox"],
        ):
            calendar.calendar_objects.all().delete()

        self.stdout.write(self.style.SUCCESS("Integration fixtures are ready."))
