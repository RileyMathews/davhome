# pyright: reportGeneralTypeIssues=false, reportAttributeAccessIssue=false

from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils.http import http_date
from django.views.decorators.csrf import csrf_exempt

from .auth import get_dav_user, unauthorized_response
from .core import propmap as core_propmap
from .core import props as core_props
from .resolver import get_principal
from .views_common import _conditional_not_modified
from .views_common import _dav_common_headers
from .views_common import _home_etag_and_timestamp
from .views_common import _not_allowed
from .views_common import _parse_propfind_payload
from .views_common import _require_dav_user
from .views_common import _sync_token_for_calendar
from .views_common import _xml_response
from .views_common import _visible_calendars_for_home
from .views_reports import _handle_report
from .xml import multistatus_document, response_with_props
from .view_helpers.identity import _calendar_home_href_for_user
from .view_helpers.identity import _dav_username_for_guid
from .view_helpers.identity import _principal_href_for_user


@csrf_exempt
def well_known_caldav(request):
    return redirect("/dav/", permanent=False)


@csrf_exempt
def dav_root(request):
    allowed = ["OPTIONS", "PROPFIND", "GET", "HEAD"]
    if request.method == "OPTIONS":
        response = HttpResponse(status=204)
        response["Allow"] = ", ".join(allowed)
        return _dav_common_headers(response)

    if request.method in ("GET", "HEAD"):
        user = get_dav_user(request)
        if user is None:
            return unauthorized_response()
        if request.method == "HEAD":
            response = HttpResponse(status=200)
        else:
            response = HttpResponse(
                b"DAV root",
                content_type="text/plain; charset=utf-8",
            )
        return _dav_common_headers(response)

    if request.method != "PROPFIND":
        return _not_allowed(request, allowed)

    user = get_dav_user(request)
    if user is None:
        return unauthorized_response()

    parsed, parse_error = _parse_propfind_payload(request)
    if parse_error is not None:
        return parse_error

    prop_map = core_propmap.build_root_prop_map(user, _principal_href_for_user)

    depth = request.headers.get("Depth", "infinity")
    if parsed is None:
        return HttpResponse(status=400)

    requested = parsed["requested"] if parsed["mode"] == "prop" else None
    root_ok, root_missing = core_props.select_props(prop_map, requested)
    responses = [response_with_props("/dav/", root_ok, root_missing)]

    if depth == "1":
        principal_href = _principal_href_for_user(user)
        home_href = _calendar_home_href_for_user(user)

        principal_map = core_propmap.build_principal_prop_map(
            user,
            user,
            _principal_href_for_user,
            _calendar_home_href_for_user,
        )
        principal_ok, principal_missing = core_props.select_props(
            principal_map,
            requested,
        )
        responses.append(
            response_with_props(principal_href, principal_ok, principal_missing)
        )

        home_map = core_propmap.build_calendar_home_prop_map(
            user,
            user,
            _principal_href_for_user,
        )
        home_ok, home_missing = core_props.select_props(home_map, requested)
        responses.append(response_with_props(home_href, home_ok, home_missing))

    return _xml_response(207, multistatus_document(responses))


@csrf_exempt
def principal_view(request, username):
    allowed = ["OPTIONS", "PROPFIND", "GET", "HEAD"]
    if request.method == "OPTIONS":
        response = HttpResponse(status=204)
        response["Allow"] = ", ".join(allowed)
        return _dav_common_headers(response)

    user, auth_response = _require_dav_user(request)
    if auth_response is not None:
        return auth_response

    principal = get_principal(username)
    if principal is None:
        return HttpResponse(status=404)

    if principal != user:
        return HttpResponse(status=403)

    if request.method in ("GET", "HEAD"):
        if request.method == "HEAD":
            response = HttpResponse(status=200)
        else:
            response = HttpResponse(
                b"Principal",
                content_type="text/plain; charset=utf-8",
            )
        return _dav_common_headers(response)

    if request.method != "PROPFIND":
        return _not_allowed(request, allowed, username=username)

    parsed, parse_error = _parse_propfind_payload(request)
    if parse_error is not None:
        return parse_error
    if parsed is None:
        return HttpResponse(status=400)

    requested = parsed["requested"] if parsed["mode"] == "prop" else None
    principal_map = core_propmap.build_principal_prop_map(
        user,
        principal,
        _principal_href_for_user,
        _calendar_home_href_for_user,
    )
    ok, missing = core_props.select_props(principal_map, requested)
    responses = [
        response_with_props(f"/dav/principals/{principal.username}/", ok, missing)
    ]

    return _xml_response(207, multistatus_document(responses))


@csrf_exempt
def principal_uid_view(request, guid):
    username = _dav_username_for_guid(guid)
    if username is None:
        return HttpResponse(status=404)
    return principal_view(request, username)


@csrf_exempt
def principals_collection_view(request):
    return _collection_view(request, "/dav/principals/", "principals")


@csrf_exempt
def principals_users_collection_view(request):
    return _collection_view(request, "/dav/principals/users/", "users")


principal_users_view = principal_view


def _collection_view(request, href, display_name):
    allowed = ["OPTIONS", "PROPFIND", "GET", "HEAD"]
    if request.method == "OPTIONS":
        response = HttpResponse(status=204)
        response["Allow"] = ", ".join(allowed)
        return _dav_common_headers(response)

    user, auth_response = _require_dav_user(request)
    if auth_response is not None:
        return auth_response

    if request.method in ("GET", "HEAD"):
        if request.method == "HEAD":
            response = HttpResponse(status=200)
        else:
            response = HttpResponse(
                b"Collection",
                content_type="text/plain; charset=utf-8",
            )
        return _dav_common_headers(response)

    if request.method != "PROPFIND":
        return _not_allowed(request, allowed, href=href)

    parsed, parse_error = _parse_propfind_payload(request)
    if parse_error is not None:
        return parse_error
    if parsed is None:
        return HttpResponse(status=400)

    resolved_map = core_propmap.build_collection_prop_map(
        display_name,
        user,
        _principal_href_for_user,
    )

    requested = parsed["requested"] if parsed["mode"] == "prop" else None
    ok, missing = core_props.select_props(resolved_map, requested)
    return _xml_response(
        207,
        multistatus_document([response_with_props(href, ok, missing)]),
    )


@csrf_exempt
def calendar_home_view(request, username):
    allowed = ["OPTIONS", "PROPFIND", "GET", "HEAD", "REPORT"]
    if request.method == "OPTIONS":
        response = HttpResponse(status=204)
        response["Allow"] = ", ".join(allowed)
        return _dav_common_headers(response)

    user, auth_response = _require_dav_user(request)
    if auth_response is not None:
        return auth_response

    owner = get_principal(username)
    if owner is None:
        return HttpResponse(status=404)

    home_etag, home_timestamp = _home_etag_and_timestamp(owner, user)

    if request.method in ("GET", "HEAD"):
        if _conditional_not_modified(request, home_etag, home_timestamp):
            response = HttpResponse(status=304)
            response["ETag"] = home_etag
            response["Last-Modified"] = http_date(home_timestamp)
            return _dav_common_headers(response)

        if request.method == "HEAD":
            response = HttpResponse(status=200)
        else:
            response = HttpResponse(
                b"Calendar home",
                content_type="text/plain; charset=utf-8",
            )
        response["ETag"] = home_etag
        response["Last-Modified"] = http_date(home_timestamp)
        return _dav_common_headers(response)

    calendars = _visible_calendars_for_home(owner, user)

    if request.method == "REPORT":
        return _handle_report(calendars, request, allow_sync_collection=False)

    if request.method != "PROPFIND":
        return _not_allowed(request, allowed, username=username)

    parsed, parse_error = _parse_propfind_payload(request)
    if parse_error is not None:
        return parse_error
    if parsed is None:
        return HttpResponse(status=400)

    depth = request.headers.get("Depth", "infinity")
    requested = parsed["requested"] if parsed["mode"] == "prop" else None
    home_map = core_propmap.build_calendar_home_prop_map(
        owner,
        user,
        _principal_href_for_user,
    )
    home_ok, home_missing = core_props.select_props(home_map, requested)
    responses = [
        response_with_props(f"/dav/calendars/{owner.username}/", home_ok, home_missing)
    ]

    if depth == "1":
        for calendar in calendars:
            cal_map = core_propmap.build_calendar_collection_prop_map(
                calendar,
                user,
                _principal_href_for_user,
                _sync_token_for_calendar,
            )
            cal_ok, cal_missing = core_props.select_props(cal_map, requested)
            href = f"/dav/calendars/{owner.username}/{calendar.slug}/"
            responses.append(response_with_props(href, cal_ok, cal_missing))

    return _xml_response(207, multistatus_document(responses))


@csrf_exempt
def calendars_collection_view(request):
    return _collection_view(request, "/dav/calendars/", "calendars")


@csrf_exempt
def calendars_uids_collection_view(request):
    return _collection_view(request, "/dav/calendars/__uids__/", "uid calendars")


@csrf_exempt
def calendars_users_collection_view(request):
    return _collection_view(request, "/dav/calendars/users/", "user calendars")


@csrf_exempt
def calendar_home_uid_view(request, guid):
    username = _dav_username_for_guid(guid)
    if username is None:
        return HttpResponse(status=404)
    return calendar_home_view(request, username)


calendar_home_users_view = calendar_home_view
