# pyright: reportAttributeAccessIssue=false, reportGeneralTypeIssues=false

from typing import Any, cast

from calendars.models import Calendar, CalendarObject
from dav.core.contracts import CalendarObjectData


def calendar_object_to_data(obj: CalendarObject) -> CalendarObjectData:
    obj_any = cast(Any, obj)
    dead_properties = obj_any.dead_properties or {}
    return CalendarObjectData(
        calendar_id=str(obj_any.calendar_id),
        owner_username=str(obj_any.calendar.owner.username),
        slug=str(obj_any.calendar.slug),
        filename=str(obj_any.filename),
        etag=str(obj_any.etag),
        content_type=str(obj_any.content_type),
        ical_blob=str(obj_any.ical_blob),
        size=int(obj_any.size),
        dead_properties={str(k): str(v) for k, v in dict(dead_properties).items()},
        last_modified=obj_any.updated_at,
    )


def list_calendar_object_data(calendar: Calendar) -> list[CalendarObjectData]:
    objects = (
        CalendarObject.objects.select_related("calendar", "calendar__owner")
        .filter(calendar=calendar)
        .order_by("filename")
    )
    return [calendar_object_to_data(obj) for obj in objects]


def list_calendar_object_data_for_calendars(
    calendars: list[Calendar],
) -> list[CalendarObjectData]:
    calendar_ids = [calendar.id for calendar in calendars]
    if not calendar_ids:
        return []
    objects = (
        CalendarObject.objects.select_related("calendar", "calendar__owner")
        .filter(calendar_id__in=calendar_ids)
        .order_by("calendar_id", "filename")
    )
    return [calendar_object_to_data(obj) for obj in objects]
