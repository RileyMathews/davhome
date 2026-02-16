from django.db.models import Q

from .models import Calendar, CalendarShare


def calendars_for_user(user):
    return Calendar.objects.filter(Q(owner=user) | Q(shares__user=user)).distinct()


def _share_for_user(calendar, user):
    return calendar.shares.filter(user=user).first()


def can_view_calendar(calendar, user):
    if calendar.owner_id == user.id:
        return True
    return _share_for_user(calendar, user) is not None


def can_manage_calendar(calendar, user):
    if calendar.owner_id == user.id:
        return True
    share = _share_for_user(calendar, user)
    return share is not None and share.role == CalendarShare.ADMIN


def can_write_calendar(calendar, user):
    if calendar.owner_id == user.id:
        return True
    share = _share_for_user(calendar, user)
    return share is not None and share.role in (
        CalendarShare.ADMIN,
        CalendarShare.WRITE,
    )
