import re


def _dav_guid_for_username(username):
    match = re.fullmatch(r"user(\d{2})", username)
    if match is None:
        return None
    return f"10000000-0000-0000-0000-000000000{int(match.group(1)):03d}"


def _principal_href_for_user(user):
    guid = _dav_guid_for_username(user.username)
    if guid is None:
        return f"/dav/principals/users/{user.username}/"
    return f"/dav/principals/__uids__/{guid}/"


def _calendar_home_href_for_user(user):
    guid = _dav_guid_for_username(user.username)
    if guid is None:
        return f"/dav/calendars/users/{user.username}/"
    return f"/dav/calendars/__uids__/{guid}/"
