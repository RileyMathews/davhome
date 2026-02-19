from .identity import _dav_guid_for_username


def _report_href_style(request_path):
    if "/calendars/__uids__/" in request_path:
        return "uids"
    if "/calendars/users/" in request_path:
        return "users"
    return "username"


def _object_href_for_style(calendar, obj, style):
    if style == "uids":
        guid = _dav_guid_for_username(calendar.owner.username)
        if guid is not None:
            return f"/dav/calendars/__uids__/{guid}/{calendar.slug}/{obj.filename}"

    if style == "users":
        return f"/dav/calendars/users/{calendar.owner.username}/{calendar.slug}/{obj.filename}"

    return f"/dav/calendars/{calendar.owner.username}/{calendar.slug}/{obj.filename}"


def _all_object_hrefs(calendar, obj):
    hrefs = {
        f"/dav/calendars/{calendar.owner.username}/{calendar.slug}/{obj.filename}",
        f"/dav/calendars/users/{calendar.owner.username}/{calendar.slug}/{obj.filename}",
    }
    guid = _dav_guid_for_username(calendar.owner.username)
    if guid is not None:
        hrefs.add(f"/dav/calendars/__uids__/{guid}/{calendar.slug}/{obj.filename}")
    return hrefs


def _object_href_for_style_data(obj_data, style):
    if style == "uids":
        guid = _dav_guid_for_username(obj_data.owner_username)
        if guid is not None:
            return f"/dav/calendars/__uids__/{guid}/{obj_data.slug}/{obj_data.filename}"

    if style == "users":
        return f"/dav/calendars/users/{obj_data.owner_username}/{obj_data.slug}/{obj_data.filename}"

    return (
        f"/dav/calendars/{obj_data.owner_username}/{obj_data.slug}/{obj_data.filename}"
    )


def _all_object_hrefs_for_data(obj_data):
    hrefs = {
        f"/dav/calendars/{obj_data.owner_username}/{obj_data.slug}/{obj_data.filename}",
        f"/dav/calendars/users/{obj_data.owner_username}/{obj_data.slug}/{obj_data.filename}",
    }
    guid = _dav_guid_for_username(obj_data.owner_username)
    if guid is not None:
        hrefs.add(f"/dav/calendars/__uids__/{guid}/{obj_data.slug}/{obj_data.filename}")
    return hrefs


def _object_href_for_filename(calendar, filename, style):
    if style == "uids":
        guid = _dav_guid_for_username(calendar.owner.username)
        if guid is not None:
            return f"/dav/calendars/__uids__/{guid}/{calendar.slug}/{filename}"

    if style == "users":
        return (
            f"/dav/calendars/users/{calendar.owner.username}/{calendar.slug}/{filename}"
        )

    return f"/dav/calendars/{calendar.owner.username}/{calendar.slug}/{filename}"


def _collection_href_for_style(calendar, style):
    if style == "uids":
        guid = _dav_guid_for_username(calendar.owner.username)
        if guid is not None:
            return f"/dav/calendars/__uids__/{guid}/{calendar.slug}"

    if style == "users":
        return f"/dav/calendars/users/{calendar.owner.username}/{calendar.slug}"

    return f"/dav/calendars/{calendar.owner.username}/{calendar.slug}"
