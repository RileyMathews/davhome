from calendars.models import Calendar, CalendarObject
from dav.core.contracts import CalendarObjectData


def calendar_object_to_data(obj: CalendarObject) -> CalendarObjectData:
    return CalendarObjectData(
        calendar_id=str(obj.calendar_id),
        owner_username=obj.calendar.owner.username,
        slug=obj.calendar.slug,
        filename=obj.filename,
        etag=obj.etag,
        content_type=obj.content_type,
        ical_blob=obj.ical_blob,
        last_modified=obj.updated_at,
    )


def list_calendar_object_data(calendar: Calendar) -> list[CalendarObjectData]:
    objects = (
        CalendarObject.objects.select_related("calendar", "calendar__owner")
        .filter(calendar=calendar)
        .order_by("filename")
    )
    return [calendar_object_to_data(obj) for obj in objects]
