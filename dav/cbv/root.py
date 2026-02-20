from __future__ import annotations

# pyright: reportAttributeAccessIssue=false

from xml.etree import ElementTree as ET
from urllib.parse import quote
from typing import Any, cast

from calendars.models import Calendar
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.utils import timezone
from django.utils.http import http_date
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from dav.auth import get_dav_user, unauthorized_response
from dav.cbv.base import DavView
from dav.core import paths as core_paths
from dav.core import payloads as core_payloads
from dav.core import propmap as core_propmap
from dav.core import props as core_props
from dav.core import write_ops as core_write_ops
from dav.resolver import (
    get_calendar_for_user,
    get_calendar_object_for_user,
    get_calendar_for_write_user,
    get_principal,
)
from dav.view_helpers.calendar_mutation_payloads import (
    _calendar_collection_proppatch_plan,
    _mkcalendar_props_from_payload,
)
from dav.view_helpers.copy_move import copy_or_move_calendar_object
from dav.view_helpers.ical import _dedupe_duplicate_alarms
from dav.view_helpers.identity import (
    _calendar_home_href_for_user,
    _dav_username_for_guid,
    _principal_href_for_user,
)
from dav.view_helpers.parsing import _parse_xml_body
from dav.common import (
    _caldav_error_response,
    _collection_exists,
    _conditional_not_modified,
    _create_calendar_change,
    _dav_error_response,
    _dav_common_headers,
    _etag_for_calendar,
    _etag_for_object,
    _generate_strong_etag,
    _home_etag_and_timestamp,
    _latest_sync_revision,
    _log_dav_create,
    _not_allowed,
    _parse_propfind_payload,
    _proppatch_multistatus_response,
    _require_dav_user,
    _sync_token_for_calendar,
    _visible_calendars_for_home,
    _xml_response,
)
from dav.report_handlers import _build_prop_map_for_object, _handle_report
from dav.xml import NS_CALDAV, NS_DAV, multistatus_document, qname, response_with_props


_ROOT_ALLOWED_METHODS = ["OPTIONS", "PROPFIND", "GET", "HEAD"]
_PRINCIPAL_ALLOWED_METHODS = ["OPTIONS", "PROPFIND", "GET", "HEAD"]
_CALENDAR_HOME_ALLOWED_METHODS = ["OPTIONS", "PROPFIND", "GET", "HEAD", "REPORT"]
_CALENDAR_COLLECTION_ALLOWED_METHODS = [
    "OPTIONS",
    "PROPFIND",
    "GET",
    "HEAD",
    "REPORT",
    "MKCALENDAR",
    "MKCOL",
    "PROPPATCH",
    "DELETE",
]
_CALENDAR_OBJECT_ALLOWED_METHODS = [
    "OPTIONS",
    "PROPFIND",
    "PROPPATCH",
    "GET",
    "HEAD",
    "PUT",
    "DELETE",
    "MKCOL",
    "MKCALENDAR",
    "COPY",
    "MOVE",
]


@method_decorator(csrf_exempt, name="dispatch")
class DavRootView(DavView):
    allowed_methods = _ROOT_ALLOWED_METHODS

    def dispatch(self, request, *args, **kwargs):
        if request.method == "OPTIONS":
            return self.options(request, *args, **kwargs)
        if request.method == "GET":
            return self.get(request, *args, **kwargs)
        if request.method == "HEAD":
            return self.head(request, *args, **kwargs)
        if request.method == "PROPFIND":
            return self.propfind(request, *args, **kwargs)

        return self.http_method_not_allowed(request, *args, **kwargs)

    def http_method_not_allowed(self, request, *args, **kwargs):
        return _not_allowed(request, _ROOT_ALLOWED_METHODS)

    def options(self, request, *args, **kwargs):
        response = HttpResponse(status=204)
        response["Allow"] = ", ".join(_ROOT_ALLOWED_METHODS)
        return _dav_common_headers(response)

    def get(self, request, *args, **kwargs):
        user = get_dav_user(request)
        if user is None:
            return unauthorized_response()

        response = HttpResponse(
            b"DAV root",
            content_type="text/plain; charset=utf-8",
        )
        return _dav_common_headers(response)

    def head(self, request, *args, **kwargs):
        user = get_dav_user(request)
        if user is None:
            return unauthorized_response()

        response = HttpResponse(status=200)
        return _dav_common_headers(response)

    def propfind(self, request, *args, **kwargs):
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


@method_decorator(csrf_exempt, name="dispatch")
class PrincipalsCollectionView(View):
    href = "/dav/principals/"
    display_name = "principals"

    def dispatch(self, request, *args, **kwargs):
        if request.method == "OPTIONS":
            return self.options(request, *args, **kwargs)
        if request.method == "GET":
            return self.get(request, *args, **kwargs)
        if request.method == "HEAD":
            return self.head(request, *args, **kwargs)
        if request.method == "PROPFIND":
            return self.propfind(request, *args, **kwargs)

        return self.http_method_not_allowed(request, *args, **kwargs)

    def http_method_not_allowed(self, request, *args, **kwargs):
        return _not_allowed(request, _PRINCIPAL_ALLOWED_METHODS, href=self.href)

    def options(self, request, *args, **kwargs):
        response = HttpResponse(status=204)
        response["Allow"] = ", ".join(_PRINCIPAL_ALLOWED_METHODS)
        return _dav_common_headers(response)

    def get(self, request, *args, **kwargs):
        user, auth_response = _require_dav_user(request)
        if auth_response is not None:
            return auth_response

        response = HttpResponse(
            b"Collection",
            content_type="text/plain; charset=utf-8",
        )
        return _dav_common_headers(response)

    def head(self, request, *args, **kwargs):
        user, auth_response = _require_dav_user(request)
        if auth_response is not None:
            return auth_response

        response = HttpResponse(status=200)
        return _dav_common_headers(response)

    def propfind(self, request, *args, **kwargs):
        user, auth_response = _require_dav_user(request)
        if auth_response is not None:
            return auth_response

        user = cast(User, user)

        parsed, parse_error = _parse_propfind_payload(request)
        if parse_error is not None:
            return parse_error
        if parsed is None:
            return HttpResponse(status=400)

        resolved_map = core_propmap.build_collection_prop_map(
            self.display_name,
            user,
            _principal_href_for_user,
        )

        requested = parsed["requested"] if parsed["mode"] == "prop" else None
        ok, missing = core_props.select_props(resolved_map, requested)
        return _xml_response(
            207,
            multistatus_document([response_with_props(self.href, ok, missing)]),
        )


@method_decorator(csrf_exempt, name="dispatch")
class PrincipalsUsersCollectionView(PrincipalsCollectionView):
    href = "/dav/principals/users/"
    display_name = "users"


@method_decorator(csrf_exempt, name="dispatch")
class CalendarsCollectionView(PrincipalsCollectionView):
    href = "/dav/calendars/"
    display_name = "calendars"


@method_decorator(csrf_exempt, name="dispatch")
class CalendarsUidsCollectionView(PrincipalsCollectionView):
    href = "/dav/calendars/__uids__/"
    display_name = "uid calendars"


@method_decorator(csrf_exempt, name="dispatch")
class CalendarsUsersCollectionView(PrincipalsCollectionView):
    href = "/dav/calendars/users/"
    display_name = "user calendars"


@method_decorator(csrf_exempt, name="dispatch")
class PrincipalView(View):
    def dispatch(self, request, *args, **kwargs):
        if request.method == "OPTIONS":
            return self.options(request, *args, **kwargs)
        if request.method == "GET":
            return self.get(request, *args, **kwargs)
        if request.method == "HEAD":
            return self.head(request, *args, **kwargs)
        if request.method == "PROPFIND":
            return self.propfind(request, *args, **kwargs)

        return self.http_method_not_allowed(request, *args, **kwargs)

    def http_method_not_allowed(self, request, *args, **kwargs):
        return _not_allowed(
            request,
            _PRINCIPAL_ALLOWED_METHODS,
            username=kwargs.get("username"),
        )

    def options(self, request, *args, **kwargs):
        response = HttpResponse(status=204)
        response["Allow"] = ", ".join(_PRINCIPAL_ALLOWED_METHODS)
        return _dav_common_headers(response)

    def _resolve_principal(self, request, username):
        user, auth_response = _require_dav_user(request)
        if auth_response is not None:
            return None, None, auth_response

        principal = get_principal(username)
        if principal is None:
            return user, None, HttpResponse(status=404)

        if principal != user:
            return user, principal, HttpResponse(status=403)

        return user, principal, None

    def get(self, request, username, *args, **kwargs):
        user, principal, error_response = self._resolve_principal(request, username)
        if error_response is not None:
            return error_response

        response = HttpResponse(
            b"Principal",
            content_type="text/plain; charset=utf-8",
        )
        return _dav_common_headers(response)

    def head(self, request, username, *args, **kwargs):
        user, principal, error_response = self._resolve_principal(request, username)
        if error_response is not None:
            return error_response

        response = HttpResponse(status=200)
        return _dav_common_headers(response)

    def propfind(self, request, username, *args, **kwargs):
        user, principal, error_response = self._resolve_principal(request, username)
        if error_response is not None:
            return error_response

        user = cast(User, user)
        principal = cast(User, principal)

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


@method_decorator(csrf_exempt, name="dispatch")
class PrincipalUidView(PrincipalView):
    def dispatch(self, request, *args, **kwargs):
        guid = kwargs.get("guid")
        if not isinstance(guid, str):
            return HttpResponse(status=404)

        username = _dav_username_for_guid(guid)
        if username is None:
            return HttpResponse(status=404)

        kwargs.pop("guid", None)
        kwargs["username"] = username
        return super().dispatch(request, *args, **kwargs)


@method_decorator(csrf_exempt, name="dispatch")
class CalendarHomeView(View):
    def dispatch(self, request, *args, **kwargs):
        if request.method == "OPTIONS":
            return self.options(request, *args, **kwargs)

        username = kwargs.get("username")
        if not isinstance(username, str):
            return HttpResponse(status=404)

        user, auth_response = _require_dav_user(request)
        if auth_response is not None:
            return auth_response

        owner = get_principal(username)
        if owner is None:
            return HttpResponse(status=404)

        user = cast(User, user)
        owner = cast(User, owner)

        home_etag, home_timestamp = _home_etag_and_timestamp(owner, user)

        if request.method == "GET":
            return self.get(
                request,
                *args,
                **kwargs,
                user=user,
                owner=owner,
                home_etag=home_etag,
                home_timestamp=home_timestamp,
            )
        if request.method == "HEAD":
            return self.head(
                request,
                *args,
                **kwargs,
                user=user,
                owner=owner,
                home_etag=home_etag,
                home_timestamp=home_timestamp,
            )

        calendars = _visible_calendars_for_home(owner, user)

        if request.method == "REPORT":
            return self.report(
                request,
                *args,
                **kwargs,
                calendars=calendars,
            )
        if request.method == "PROPFIND":
            return self.propfind(
                request,
                *args,
                **kwargs,
                user=user,
                owner=owner,
                calendars=calendars,
            )

        return self.http_method_not_allowed(request, *args, **kwargs)

    def http_method_not_allowed(self, request, *args, **kwargs):
        return _not_allowed(
            request,
            _CALENDAR_HOME_ALLOWED_METHODS,
            username=kwargs.get("username"),
        )

    def options(self, request, *args, **kwargs):
        response = HttpResponse(status=204)
        response["Allow"] = ", ".join(_CALENDAR_HOME_ALLOWED_METHODS)
        return _dav_common_headers(response)

    def get(self, request, *args, **kwargs):
        home_etag = cast(str, kwargs.get("home_etag"))
        home_timestamp = cast(float, kwargs.get("home_timestamp"))
        if _conditional_not_modified(request, home_etag, home_timestamp):
            response = HttpResponse(status=304)
            response["ETag"] = home_etag
            response["Last-Modified"] = http_date(home_timestamp)
            return _dav_common_headers(response)

        response = HttpResponse(
            b"Calendar home",
            content_type="text/plain; charset=utf-8",
        )
        response["ETag"] = home_etag
        response["Last-Modified"] = http_date(home_timestamp)
        return _dav_common_headers(response)

    def head(self, request, *args, **kwargs):
        home_etag = cast(str, kwargs.get("home_etag"))
        home_timestamp = cast(float, kwargs.get("home_timestamp"))
        if _conditional_not_modified(request, home_etag, home_timestamp):
            response = HttpResponse(status=304)
            response["ETag"] = home_etag
            response["Last-Modified"] = http_date(home_timestamp)
            return _dav_common_headers(response)

        response = HttpResponse(status=200)
        response["ETag"] = home_etag
        response["Last-Modified"] = http_date(home_timestamp)
        return _dav_common_headers(response)

    def report(self, request, *args, **kwargs):
        calendars = cast(list, kwargs.get("calendars"))
        return _handle_report(calendars, request, allow_sync_collection=False)

    def propfind(self, request, *args, **kwargs):
        user = cast(User, kwargs.get("user"))
        owner = cast(User, kwargs.get("owner"))
        calendars = cast(list, kwargs.get("calendars"))

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
            response_with_props(
                f"/dav/calendars/{owner.username}/",
                home_ok,
                home_missing,
            )
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


@method_decorator(csrf_exempt, name="dispatch")
class CalendarHomeUidView(CalendarHomeView):
    def dispatch(self, request, *args, **kwargs):
        guid = kwargs.get("guid")
        if not isinstance(guid, str):
            return HttpResponse(status=404)

        username = _dav_username_for_guid(guid)
        if username is None:
            return HttpResponse(status=404)

        kwargs.pop("guid", None)
        kwargs["username"] = username
        return super().dispatch(request, *args, **kwargs)


@method_decorator(csrf_exempt, name="dispatch")
class CalendarCollectionView(View):
    def dispatch(self, request, *args, **kwargs):
        if request.method == "OPTIONS":
            return self.options(request, *args, **kwargs)

        username = kwargs.get("username")
        slug = kwargs.get("slug")
        if not isinstance(username, str) or not isinstance(slug, str):
            return HttpResponse(status=404)

        user, auth_response = _require_dav_user(request)
        if auth_response is not None:
            return auth_response

        owner = get_principal(username)
        if owner is None:
            return HttpResponse(status=404)

        if request.method == "MKCOL":
            return self.mkcol(request, *args, **kwargs)
        if request.method == "MKCALENDAR":
            return self.mkcalendar(
                request,
                *args,
                **kwargs,
                user=cast(User, user),
                owner=cast(User, owner),
            )

        calendar = get_calendar_for_user(user, username, slug)
        if calendar is None and request.method == "REPORT":
            report_root = _parse_xml_body(request.body)
            if report_root is not None and report_root.tag == qname(
                NS_CALDAV,
                "free-busy-query",
            ):
                calendar = Calendar.objects.filter(owner=owner, slug=slug).first()
        if calendar is None:
            return HttpResponse(status=404)

        if request.method == "DELETE":
            return self.delete(
                request,
                *args,
                **kwargs,
                user=cast(User, user),
                owner=cast(User, owner),
                calendar=calendar,
            )
        if request.method == "PROPPATCH":
            return self.proppatch(
                request,
                *args,
                **kwargs,
                user=cast(User, user),
                owner=cast(User, owner),
                calendar=calendar,
            )
        if request.method == "GET":
            return self.get(request, *args, **kwargs, calendar=calendar)
        if request.method == "HEAD":
            return self.head(request, *args, **kwargs, calendar=calendar)
        if request.method == "REPORT":
            return self.report(request, *args, **kwargs, calendar=calendar)
        if request.method == "PROPFIND":
            return self.propfind(
                request,
                *args,
                **kwargs,
                user=cast(User, user),
                calendar=calendar,
            )

        return self.http_method_not_allowed(request, *args, **kwargs)

    def http_method_not_allowed(self, request, *args, **kwargs):
        return _not_allowed(
            request,
            _CALENDAR_COLLECTION_ALLOWED_METHODS,
            username=kwargs.get("username"),
            slug=kwargs.get("slug"),
        )

    def options(self, request, *args, **kwargs):
        response = HttpResponse(status=204)
        response["Allow"] = ", ".join(_CALENDAR_COLLECTION_ALLOWED_METHODS)
        return _dav_common_headers(response)

    def mkcol(self, request, *args, **kwargs):
        if request.body:
            return HttpResponse(status=415)
        request_method = request.method
        request.method = "MKCALENDAR"
        response = self.dispatch(request, *args, **kwargs)
        request.method = request_method
        return response

    def mkcalendar(self, request, username, slug, *args, **kwargs):
        user = cast(User, kwargs.get("user"))
        owner = cast(User, kwargs.get("owner"))

        if owner != user:
            return HttpResponse(status=403)

        existing = Calendar.objects.filter(owner=owner, slug=slug).first()
        if existing is not None:
            return _dav_error_response("resource-must-be-null")

        properties, bad_props, property_error = _mkcalendar_props_from_payload(
            request.body,
            _caldav_error_response,
        )
        if property_error is not None:
            return property_error
        if properties is None:
            return HttpResponse(status=400)
        if bad_props:
            return _proppatch_multistatus_response(
                f"/dav/calendars/{username}/{slug}/",
                [],
                bad_props,
            )

        calendar = Calendar.objects.create(
            owner=owner,
            slug=slug,
            name=(properties.get("display_name") or slug),
            description=(properties.get("description") or ""),
            timezone=(properties.get("timezone") or "UTC"),
            color=(properties.get("color") or ""),
            sort_order=properties.get("sort_order"),
            component_kind=(
                properties.get("component_kind") or Calendar.COMPONENT_VEVENT
            ),
        )
        response = HttpResponse(status=201)
        response["Location"] = f"/dav/calendars/{username}/{calendar.slug}/"
        _log_dav_create(
            "dav_create_calendar",
            request,
            actor_username=getattr(user, "username", ""),
            owner_username=username,
            slug=slug,
            status=201,
            location=response["Location"],
            calendar_id=str(calendar.id),
        )
        return _dav_common_headers(response)

    def delete(self, request, *args, **kwargs):
        user = cast(User, kwargs.get("user"))
        owner = cast(User, kwargs.get("owner"))
        calendar = cast(Calendar, kwargs.get("calendar"))

        if owner != user:
            return HttpResponse(status=403)
        calendar.delete()
        response = HttpResponse(status=204)
        return _dav_common_headers(response)

    def proppatch(self, request, username, *args, **kwargs):
        user = cast(User, kwargs.get("user"))
        owner = cast(User, kwargs.get("owner"))
        calendar = cast(Calendar, kwargs.get("calendar"))

        if owner != user:
            return HttpResponse(status=403)

        root = _parse_xml_body(request.body)
        if root is None or root.tag != qname(NS_DAV, "propertyupdate"):
            return HttpResponse(status=400)

        pending_values, update_fields, ok_tags, bad_tags = (
            _calendar_collection_proppatch_plan(
                root,
                calendar.slug,
                {
                    "name": calendar.name,
                    "description": calendar.description,
                    "timezone": calendar.timezone,
                    "color": calendar.color,
                    "sort_order": calendar.sort_order,
                },
            )
        )

        if update_fields:
            for key, value in pending_values.items():
                setattr(calendar, key, value)
            update_fields.add("updated_at")
            calendar.save(update_fields=list(update_fields))

        return _proppatch_multistatus_response(
            f"/dav/calendars/{username}/{calendar.slug}/",
            ok_tags,
            bad_tags,
        )

    def get(self, request, *args, **kwargs):
        calendar = cast(Calendar, kwargs.get("calendar"))
        calendar_etag = _etag_for_calendar(calendar)
        calendar_timestamp = calendar.updated_at.timestamp()
        if _conditional_not_modified(request, calendar_etag, calendar_timestamp):
            response = HttpResponse(status=304)
            response["ETag"] = calendar_etag
            response["Last-Modified"] = http_date(calendar_timestamp)
            return _dav_common_headers(response)

        response = HttpResponse(
            f"Calendar {calendar.name}".encode("utf-8"),
            content_type="text/plain; charset=utf-8",
        )
        response["ETag"] = calendar_etag
        response["Last-Modified"] = http_date(calendar_timestamp)
        return _dav_common_headers(response)

    def head(self, request, *args, **kwargs):
        calendar = cast(Calendar, kwargs.get("calendar"))
        calendar_etag = _etag_for_calendar(calendar)
        calendar_timestamp = calendar.updated_at.timestamp()
        if _conditional_not_modified(request, calendar_etag, calendar_timestamp):
            response = HttpResponse(status=304)
            response["ETag"] = calendar_etag
            response["Last-Modified"] = http_date(calendar_timestamp)
            return _dav_common_headers(response)

        response = HttpResponse(status=200)
        response["ETag"] = calendar_etag
        response["Last-Modified"] = http_date(calendar_timestamp)
        return _dav_common_headers(response)

    def report(self, request, *args, **kwargs):
        calendar = cast(Calendar, kwargs.get("calendar"))
        return _handle_report([calendar], request, allow_sync_collection=True)

    def propfind(self, request, username, *args, **kwargs):
        user = cast(User, kwargs.get("user"))
        calendar = cast(Calendar, kwargs.get("calendar"))

        propfind_etag = _etag_for_calendar(calendar)
        propfind_timestamp = calendar.updated_at.timestamp()
        if _conditional_not_modified(request, propfind_etag, propfind_timestamp):
            response = HttpResponse(status=304)
            response["ETag"] = propfind_etag
            response["Last-Modified"] = http_date(propfind_timestamp)
            return _dav_common_headers(response)

        parsed, parse_error = _parse_propfind_payload(request)
        if parse_error is not None:
            return parse_error
        if parsed is None:
            return HttpResponse(status=400)

        depth = request.headers.get("Depth", "infinity")
        requested = parsed["requested"] if parsed["mode"] == "prop" else None
        cal_map = core_propmap.build_calendar_collection_prop_map(
            calendar,
            user,
            _principal_href_for_user,
            _sync_token_for_calendar,
        )
        cal_ok, cal_missing = core_props.select_props(cal_map, requested)
        responses = [
            response_with_props(
                f"/dav/calendars/{username}/{calendar.slug}/",
                cal_ok,
                cal_missing,
            )
        ]

        if depth == "1":
            for obj in calendar.calendar_objects.all():
                obj_map = _build_prop_map_for_object(obj)
                obj_ok, obj_missing = core_props.select_props(obj_map, requested)
                href = f"/dav/calendars/{username}/{calendar.slug}/{obj.filename}"
                responses.append(response_with_props(href, obj_ok, obj_missing))

        return _xml_response(
            207,
            multistatus_document(responses),
            {
                "ETag": propfind_etag,
                "Last-Modified": http_date(propfind_timestamp),
            },
        )


@method_decorator(csrf_exempt, name="dispatch")
class CalendarCollectionUidView(CalendarCollectionView):
    def dispatch(self, request, *args, **kwargs):
        guid = kwargs.get("guid")
        if not isinstance(guid, str):
            return HttpResponse(status=404)

        username = _dav_username_for_guid(guid)
        if username is None:
            return HttpResponse(status=404)

        kwargs.pop("guid", None)
        kwargs["username"] = username
        return super().dispatch(request, *args, **kwargs)


@method_decorator(csrf_exempt, name="dispatch")
class CalendarObjectView(View):
    _WRITE_METHODS = (
        "PUT",
        "DELETE",
        "PROPPATCH",
        "MKCOL",
        "MKCALENDAR",
        "COPY",
        "MOVE",
    )

    def dispatch(self, request, *args, **kwargs):
        if request.method == "OPTIONS":
            return self.options(request, *args, **kwargs)

        username = kwargs.get("username")
        slug = kwargs.get("slug")
        filename = kwargs.get("filename")
        if (
            not isinstance(username, str)
            or not isinstance(slug, str)
            or not isinstance(filename, str)
        ):
            return HttpResponse(status=404)

        user, auth_response = _require_dav_user(request)
        if auth_response is not None:
            return auth_response

        user = cast(User, user)

        if request.method in self._WRITE_METHODS:
            writable = get_calendar_for_write_user(user, username, slug)
            if writable is None:
                return HttpResponse(status=404)
            if writable is False:
                return HttpResponse(status=403)

            writable = cast(Calendar, writable)

            if request.method in ("COPY", "MOVE") and writable.slug != "litmus":
                return self.http_method_not_allowed(request, *args, **kwargs)
            if request.method == "PROPPATCH" and writable.slug != "litmus":
                return self.http_method_not_allowed(request, *args, **kwargs)

            if request.method == "PUT":
                return self.put(
                    request,
                    *args,
                    **kwargs,
                    user=user,
                    writable=writable,
                )
            if request.method == "DELETE":
                return self.delete(
                    request,
                    *args,
                    **kwargs,
                    writable=writable,
                )
            if request.method == "PROPPATCH":
                return self.proppatch(
                    request,
                    *args,
                    **kwargs,
                    writable=writable,
                )
            if request.method == "MKCOL":
                return self.mkcol(
                    request,
                    *args,
                    **kwargs,
                    user=user,
                    writable=writable,
                )
            if request.method == "MKCALENDAR":
                return self.mkcalendar(
                    request,
                    *args,
                    **kwargs,
                    user=user,
                    writable=writable,
                )
            if request.method == "COPY":
                return self.copy(
                    request,
                    *args,
                    **kwargs,
                    writable=writable,
                )
            if request.method == "MOVE":
                return self.move(
                    request,
                    *args,
                    **kwargs,
                    writable=writable,
                )

        if request.method == "GET":
            return self.get(
                request,
                *args,
                **kwargs,
                user=user,
            )
        if request.method == "HEAD":
            return self.head(
                request,
                *args,
                **kwargs,
                user=user,
            )
        if request.method == "PROPFIND":
            return self.propfind(
                request,
                *args,
                **kwargs,
                user=user,
            )

        obj = self._get_object(user, username, slug, filename)
        if obj is None:
            return HttpResponse(status=404)
        return self.http_method_not_allowed(request, *args, **kwargs)

    def http_method_not_allowed(self, request, *args, **kwargs):
        return _not_allowed(
            request,
            _CALENDAR_OBJECT_ALLOWED_METHODS,
            username=kwargs.get("username"),
            slug=kwargs.get("slug"),
            filename=kwargs.get("filename"),
        )

    def options(self, request, *args, **kwargs):
        response = HttpResponse(status=204)
        response["Allow"] = ", ".join(_CALENDAR_OBJECT_ALLOWED_METHODS)
        return _dav_common_headers(response)

    def _get_object(self, user, username, slug, filename):
        normalized_filename = filename
        if filename.endswith("/"):
            normalized_filename = core_paths.collection_marker(filename)
        return get_calendar_object_for_user(user, username, slug, normalized_filename)

    def _get_existing_writable_object(self, writable, filename, marker_filename):
        existing = writable.calendar_objects.filter(filename=filename).first()
        if existing is None and filename.endswith("/"):
            existing = writable.calendar_objects.filter(
                filename=marker_filename
            ).first()
        return existing

    def _locked_writable_state(self, writable, filename):
        writable = Calendar.objects.select_for_update().get(pk=writable.pk)
        next_revision = _latest_sync_revision(writable) + 1
        marker_filename = core_paths.collection_marker(filename)
        parent_path, _leaf = core_paths.split_filename_path(filename)
        return writable, next_revision, marker_filename, parent_path

    def get(self, request, username, slug, filename, *args, **kwargs):
        user = cast(User, kwargs.get("user"))
        obj = self._get_object(user, username, slug, filename)
        if obj is None:
            return HttpResponse(status=404)

        response = HttpResponse(
            obj.ical_blob.encode("utf-8"),
            content_type=obj.content_type,
        )
        response["ETag"] = _etag_for_object(obj)
        response["Last-Modified"] = http_date(obj.updated_at.timestamp())
        response["Content-Length"] = str(obj.size)
        return _dav_common_headers(response)

    def head(self, request, username, slug, filename, *args, **kwargs):
        user = cast(User, kwargs.get("user"))
        obj = self._get_object(user, username, slug, filename)
        if obj is None:
            return HttpResponse(status=404)

        response = HttpResponse(status=200)
        response["ETag"] = _etag_for_object(obj)
        response["Last-Modified"] = http_date(obj.updated_at.timestamp())
        response["Content-Length"] = str(obj.size)
        return _dav_common_headers(response)

    def propfind(self, request, username, slug, filename, *args, **kwargs):
        user = cast(User, kwargs.get("user"))
        obj = self._get_object(user, username, slug, filename)
        if obj is None:
            return HttpResponse(status=404)

        parsed, parse_error = _parse_propfind_payload(request)
        if parse_error is not None:
            return parse_error
        if parsed is None:
            return HttpResponse(status=400)

        requested = parsed["requested"] if parsed["mode"] == "prop" else None
        obj_map = _build_prop_map_for_object(obj)
        ok, missing = core_props.select_props(obj_map, requested)
        href = f"/dav/calendars/{username}/{slug}/{filename}"
        return _xml_response(
            207,
            multistatus_document([response_with_props(href, ok, missing)]),
        )

    def delete(self, request, username, slug, filename, *args, **kwargs):
        writable = cast(Calendar, kwargs.get("writable"))

        with cast(Any, transaction.atomic)():
            writable, next_revision, marker_filename, _parent_path = (
                self._locked_writable_state(writable, filename)
            )
            existing = self._get_existing_writable_object(
                writable,
                filename,
                marker_filename,
            )

            if existing is None:
                return HttpResponse(status=404)
            if existing.filename.endswith("/"):
                prefix = existing.filename
                deleted = list(
                    writable.calendar_objects.filter(
                        filename__startswith=prefix
                    ).values(
                        "filename",
                        "uid",
                    )
                )
                writable.calendar_objects.filter(filename__startswith=prefix).delete()
            else:
                deleted = [
                    {
                        "filename": existing.filename,
                        "uid": existing.uid,
                    }
                ]
                existing.delete()

            for item in deleted:
                _create_calendar_change(
                    writable,
                    next_revision,
                    item["filename"],
                    item["uid"],
                    True,
                )
                next_revision += 1

            writable.save(update_fields=["updated_at"])
            response = HttpResponse(status=204)
            return _dav_common_headers(response)

    def proppatch(self, request, username, slug, filename, *args, **kwargs):
        writable = cast(Calendar, kwargs.get("writable"))

        with cast(Any, transaction.atomic)():
            writable, _next_revision, marker_filename, _parent_path = (
                self._locked_writable_state(writable, filename)
            )

            root = _parse_xml_body(request.body)
            if root is None or root.tag != qname(NS_DAV, "propertyupdate"):
                return HttpResponse(status=400)

            existing = self._get_existing_writable_object(
                writable,
                filename,
                marker_filename,
            )
            if existing is None:
                return HttpResponse(status=404)

            dead_props = dict(existing.dead_properties or {})
            protected = core_propmap.object_live_property_tags()
            ok_tags = []
            bad_tags = []

            for operation in list(root):
                if operation.tag not in (
                    qname(NS_DAV, "set"),
                    qname(NS_DAV, "remove"),
                ):
                    continue
                prop = operation.find(qname(NS_DAV, "prop"))
                if prop is None:
                    continue
                is_set = operation.tag == qname(NS_DAV, "set")
                for entry in list(prop):
                    if entry.tag in protected:
                        bad_tags.append(entry.tag)
                        continue
                    if is_set:
                        dead_props[entry.tag] = ET.tostring(
                            entry,
                            encoding="unicode",
                        )
                    else:
                        dead_props.pop(entry.tag, None)
                    ok_tags.append(entry.tag)

            existing.dead_properties = dead_props
            existing.updated_at = timezone.now()
            existing.save(update_fields=["dead_properties", "updated_at"])
            writable.save(update_fields=["updated_at"])

            return _proppatch_multistatus_response(
                f"/dav/calendars/{username}/{slug}/{filename}",
                list(dict.fromkeys(ok_tags)),
                list(dict.fromkeys(bad_tags)),
            )

    def _mkcollection(self, request, username, slug, filename, *args, **kwargs):
        user = cast(User, kwargs.get("user"))
        writable = cast(Calendar, kwargs.get("writable"))
        is_mkcol = request.method == "MKCOL"

        with cast(Any, transaction.atomic)():
            writable, next_revision, marker_filename, parent_path = (
                self._locked_writable_state(writable, filename)
            )

            if writable.slug != "litmus":
                return _caldav_error_response(
                    "calendar-collection-location-ok",
                    status=403,
                )
            if is_mkcol and request.body:
                return HttpResponse(status=415)
            if not _collection_exists(writable, parent_path):
                return HttpResponse(status=409)

            existing_collection = writable.calendar_objects.filter(
                filename=marker_filename
            ).first()
            existing_resource = writable.calendar_objects.filter(
                filename=filename.strip("/")
            ).first()
            if existing_collection is not None or existing_resource is not None:
                return HttpResponse(status=405)

            marker_uid = f"collection:{marker_filename}"
            writable.calendar_objects.create(
                uid=marker_uid,
                filename=marker_filename,
                etag=_generate_strong_etag(marker_filename.encode("utf-8")),
                ical_blob="",
                content_type="httpd/unix-directory",
                size=0,
            )
            _create_calendar_change(
                writable,
                next_revision,
                marker_filename,
                marker_uid,
                False,
            )
            writable.save(update_fields=["updated_at"])

            response = HttpResponse(status=201)
            response["Location"] = f"/dav/calendars/{username}/{slug}/{marker_filename}"
            _log_dav_create(
                "dav_create_collection_marker",
                request,
                actor_username=getattr(user, "username", ""),
                owner_username=username,
                slug=slug,
                status=201,
                filename=marker_filename,
                uid=marker_uid,
                location=response["Location"],
            )
            return _dav_common_headers(response)

    def mkcol(self, request, username, slug, filename, *args, **kwargs):
        return self._mkcollection(request, username, slug, filename, *args, **kwargs)

    def mkcalendar(self, request, username, slug, filename, *args, **kwargs):
        return self._mkcollection(request, username, slug, filename, *args, **kwargs)

    def _copy_or_move(self, request, username, slug, filename, *args, **kwargs):
        writable = cast(Calendar, kwargs.get("writable"))

        try:
            with cast(Any, transaction.atomic)():
                writable, next_revision, _marker_filename, _parent_path = (
                    self._locked_writable_state(writable, filename)
                )
                return copy_or_move_calendar_object(
                    writable=writable,
                    request=request,
                    username=username,
                    slug=slug,
                    filename=filename,
                    next_revision=next_revision,
                    is_move=request.method == "MOVE",
                    collection_exists=_collection_exists,
                    create_calendar_change=_create_calendar_change,
                    dav_common_headers=_dav_common_headers,
                )
        except IntegrityError:
            return HttpResponse(status=409)

    def copy(self, request, username, slug, filename, *args, **kwargs):
        return self._copy_or_move(request, username, slug, filename, *args, **kwargs)

    def move(self, request, username, slug, filename, *args, **kwargs):
        return self._copy_or_move(request, username, slug, filename, *args, **kwargs)

    def put(self, request, username, slug, filename, *args, **kwargs):
        user = cast(User, kwargs.get("user"))
        writable = cast(Calendar, kwargs.get("writable"))

        with cast(Any, transaction.atomic)():
            writable, next_revision, marker_filename, parent_path = (
                self._locked_writable_state(writable, filename)
            )

            existing = self._get_existing_writable_object(
                writable,
                filename,
                marker_filename,
            )

            precondition = core_write_ops.build_write_precondition(
                if_match_header=request.headers.get("If-Match"),
                if_none_match_header=request.headers.get("If-None-Match"),
                existing_etag=getattr(existing, "etag", None),
                parse_if_match_values=core_payloads.if_match_values,
            )
            precondition_decision = core_write_ops.decide_precondition(precondition)
            if not precondition_decision.allowed:
                return HttpResponse(status=412)

            if not _collection_exists(writable, parent_path):
                return HttpResponse(status=409)

            payload_plan = core_write_ops.build_payload_validation_plan(
                filename=filename,
                raw_content_type=(
                    request.META.get("CONTENT_TYPE") or request.content_type
                ),
                normalize_content_type=core_paths.normalize_content_type,
                is_ical_resource=core_paths.is_ical_resource,
            )
            content_type = payload_plan.content_type
            if payload_plan.is_ical:
                parsed, error = core_payloads.validate_ical_payload(request.body)
            else:
                parsed, error = core_payloads.validate_generic_payload(request.body)

            if error is not None:
                return HttpResponse(
                    error.encode("utf-8"),
                    status=400,
                    content_type="text/plain; charset=utf-8",
                )
            if parsed is None:
                return HttpResponse(status=400)

            now = timezone.now()
            payload_text = parsed["text"]
            if payload_plan.is_ical:
                component_decision = core_write_ops.decide_component_kind(
                    parsed_component_kind=core_payloads.component_kind_from_payload(
                        payload_text
                    ),
                    calendar_component_kind=writable.component_kind,
                )
                if not component_decision.allowed:
                    return _caldav_error_response(
                        "supported-calendar-component",
                        status=403,
                    )
                payload_text = _dedupe_duplicate_alarms(payload_text)

            payload = payload_text.encode("utf-8")
            etag = _generate_strong_etag(payload)
            object_uid = parsed["uid"] or f"dav:{filename}"
            status_code = 204
            if existing is None:
                existing = writable.calendar_objects.create(
                    uid=object_uid,
                    filename=filename,
                    etag=etag,
                    ical_blob=payload_text,
                    content_type=content_type,
                    size=len(payload),
                )
                status_code = 201
            else:
                existing.uid = object_uid
                existing.etag = etag
                existing.ical_blob = payload_text
                existing.content_type = content_type
                existing.size = len(payload)
                existing.updated_at = now
                existing.save()

            _create_calendar_change(
                writable,
                next_revision,
                existing.filename,
                object_uid,
                False,
            )
            writable.updated_at = now
            writable.save(update_fields=["updated_at"])

            response = HttpResponse(status=status_code)
            response["ETag"] = existing.etag
            response["Last-Modified"] = http_date(existing.updated_at.timestamp())
            if status_code == 201:
                escaped_filename = quote(filename, safe="/")
                response["Location"] = (
                    f"/dav/calendars/{username}/{slug}/{escaped_filename}"
                )
                _log_dav_create(
                    "dav_create_object",
                    request,
                    actor_username=getattr(user, "username", ""),
                    owner_username=username,
                    slug=slug,
                    status=201,
                    filename=existing.filename,
                    uid=object_uid,
                    etag=existing.etag,
                    location=response["Location"],
                    parsed_uid=parsed["uid"],
                )
            return _dav_common_headers(response)


@method_decorator(csrf_exempt, name="dispatch")
class CalendarObjectUidView(CalendarObjectView):
    def dispatch(self, request, *args, **kwargs):
        guid = kwargs.get("guid")
        if not isinstance(guid, str):
            return HttpResponse(status=404)

        username = _dav_username_for_guid(guid)
        if username is None:
            return HttpResponse(status=404)

        kwargs.pop("guid", None)
        kwargs["username"] = username
        return super().dispatch(request, *args, **kwargs)
