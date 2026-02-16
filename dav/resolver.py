from django.contrib.auth.models import User

from calendars.models import Calendar, CalendarObject
from calendars.permissions import can_view_calendar


def get_principal(username):
    try:
        return User.objects.get(username=username)
    except User.DoesNotExist:
        return None


def get_calendar_for_user(user, username, slug):
    try:
        calendar = Calendar.objects.get(owner__username=username, slug=slug)
    except Calendar.DoesNotExist:
        return None

    if not can_view_calendar(calendar, user):
        return None

    return calendar


def get_calendar_object_for_user(user, username, slug, filename):
    calendar = get_calendar_for_user(user, username, slug)
    if calendar is None:
        return None

    try:
        obj = CalendarObject.objects.get(calendar=calendar, filename=filename)
    except CalendarObject.DoesNotExist:
        return None

    return obj
