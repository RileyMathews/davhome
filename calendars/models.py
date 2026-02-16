import uuid

from django.contrib.auth.models import User
from django.db import models


class Calendar(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="owned_calendars",
    )
    slug = models.SlugField(max_length=64)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    color = models.CharField(max_length=16, blank=True)
    timezone = models.CharField(max_length=64, default="UTC")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "slug"],
                name="uniq_calendar_owner_slug",
            ),
        ]
        ordering = ["name", "slug"]

    def __str__(self):
        return str(self.slug)


class CalendarShare(models.Model):
    READ = "read"
    WRITE = "write"
    ADMIN = "admin"
    ROLE_CHOICES = (
        (READ, "Read"),
        (WRITE, "Write"),
        (ADMIN, "Admin"),
    )

    calendar = models.ForeignKey(
        Calendar,
        on_delete=models.CASCADE,
        related_name="shares",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="calendar_shares",
    )
    role = models.CharField(max_length=16, choices=ROLE_CHOICES, default=READ)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["calendar", "user"],
                name="uniq_calendar_share",
            ),
        ]

    def __str__(self):
        return f"{self.calendar} ({self.role})"


class CalendarObject(models.Model):
    calendar = models.ForeignKey(
        Calendar,
        on_delete=models.CASCADE,
        related_name="calendar_objects",
    )
    uid = models.CharField(max_length=255)
    filename = models.CharField(max_length=255)
    etag = models.CharField(max_length=128)
    ical_blob = models.TextField()
    content_type = models.CharField(
        max_length=128, default="text/calendar; charset=utf-8"
    )
    size = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["calendar", "uid"],
                name="uniq_calendar_object_uid",
            ),
            models.UniqueConstraint(
                fields=["calendar", "filename"],
                name="uniq_calendar_object_filename",
            ),
        ]

    def __str__(self):
        return f"{self.calendar}/{self.filename}"
