from unittest.mock import patch

from django.contrib.auth.models import User
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from calendars.models import Calendar
from dav.views.calendar_home import CalendarHomeView
from dav.views.calendar_home_uid import CalendarHomeUidView
from dav.views.calendar_collection_uid import CalendarCollectionUidView
from dav.views.calendar_object_uid import CalendarObjectUidView
from dav.views.mixins import GuidToUsernameDispatchMixin
from dav.views.principal import PrincipalView
from dav.views.principal_uid import PrincipalUidView


class CalendarHomeAndPrincipalViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(username="owner", password="pw-test-12345")
        cls.member = User.objects.create_user(
            username="member", password="pw-test-12345"
        )
        cls.calendar = Calendar.objects.create(
            owner=cls.owner,
            slug="family",
            name="Family",
            timezone="UTC",
        )

    def setUp(self):
        self.factory = RequestFactory()

    def _home_view(self, request):
        view = CalendarHomeView()
        view.request = request
        return view

    def _principal_view(self, request):
        view = PrincipalView()
        view.request = request
        return view

    def test_calendar_home_options_and_invalid_owner_paths(self):
        request = self.factory.options("/dav/")
        request.user = self.owner
        view = self._home_view(request)

        response = view.options(request)
        self.assertEqual(response.status_code, 204)
        self.assertIn("REPORT", response["Allow"])

        self.assertEqual(view.get(request, 123).status_code, 404)
        self.assertEqual(view.get(request, "missing").status_code, 404)
        self.assertEqual(view.head(request, 123).status_code, 404)
        self.assertEqual(view.report(request, "missing").status_code, 404)
        self.assertEqual(view.propfind(request, "missing").status_code, 404)

    def test_calendar_home_get_head_report_and_propfind_paths(self):
        request = self.factory.get("/dav/")
        request.user = self.owner
        view = self._home_view(request)

        get_error = view.get(request, "missing")
        self.assertEqual(get_error.status_code, 404)

        with patch(
            "dav.views.calendar_home._conditional_not_modified", return_value=True
        ):
            not_modified = view.get(request, self.owner.username)
        self.assertEqual(not_modified.status_code, 304)

        with patch(
            "dav.views.calendar_home._conditional_not_modified", return_value=False
        ):
            ok = view.get(request, self.owner.username)
        self.assertEqual(ok.status_code, 200)

        head_error = view.head(request, "missing")
        self.assertEqual(head_error.status_code, 404)

        with patch(
            "dav.views.calendar_home._conditional_not_modified", return_value=True
        ):
            head_not_modified = view.head(request, self.owner.username)
        self.assertEqual(head_not_modified.status_code, 304)

        with patch(
            "dav.views.calendar_home._conditional_not_modified", return_value=False
        ):
            head_ok = view.head(request, self.owner.username)
        self.assertEqual(head_ok.status_code, 200)

        report_error = view.report(request, "missing")
        self.assertEqual(report_error.status_code, 404)

        with patch(
            "dav.views.calendar_home._handle_report",
            return_value=HttpResponse(status=207),
        ):
            report_ok = view.report(request, self.owner.username)
        self.assertEqual(report_ok.status_code, 207)

        with patch(
            "dav.views.calendar_home._parse_propfind_payload",
            return_value=(None, HttpResponse(status=422)),
        ):
            parse_error = view.propfind(request, self.owner.username)
        self.assertEqual(parse_error.status_code, 422)

        with patch(
            "dav.views.calendar_home._parse_propfind_payload", return_value=(None, None)
        ):
            parse_none = view.propfind(request, self.owner.username)
        self.assertEqual(parse_none.status_code, 400)

    def test_calendar_home_propfind_depth_one_includes_calendar(self):
        request = self.factory.generic(
            "PROPFIND",
            "/dav/calendars/owner/",
            data="",
            content_type="application/xml",
            HTTP_DEPTH="1",
        )
        request.user = self.owner

        with patch(
            "dav.views.calendar_home._parse_propfind_payload",
            return_value=({"mode": "allprop", "requested": None}, None),
        ):
            response = self._home_view(request).propfind(request, self.owner.username)

        self.assertEqual(response.status_code, 207)
        body = response.content.decode("utf-8")
        self.assertIn(f"/dav/calendars/{self.owner.username}/", body)
        self.assertIn(
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/", body
        )

    def test_calendar_home_propfind_returns_404_for_missing_owner(self):
        request = self.factory.generic("PROPFIND", "/dav/calendars/missing/")
        request.user = self.owner

        response = self._home_view(request).propfind(request, "missing")

        self.assertEqual(response.status_code, 404)

    def test_principal_options_resolve_get_head_propfind(self):
        request = self.factory.generic("GET", "/dav/principals/owner/")
        request.user = self.owner
        view = self._principal_view(request)

        options = view.options(request)
        self.assertEqual(options.status_code, 204)

        _, principal, error = view._resolve_principal(request, "missing")
        self.assertIsNone(principal)
        self.assertEqual(error.status_code, 404)

        _, _, forbidden = view._resolve_principal(request, self.member.username)
        self.assertEqual(forbidden.status_code, 403)

        get_ok = view.get(request, self.owner.username)
        self.assertEqual(get_ok.status_code, 200)

        head_ok = view.head(request, self.owner.username)
        self.assertEqual(head_ok.status_code, 200)

        get_forbidden = view.get(request, self.member.username)
        self.assertEqual(get_forbidden.status_code, 403)

        head_missing = view.head(request, "missing")
        self.assertEqual(head_missing.status_code, 404)

        with patch(
            "dav.views.principal._parse_propfind_payload",
            return_value=(None, HttpResponse(status=422)),
        ):
            parse_error = view.propfind(request, self.owner.username)
        self.assertEqual(parse_error.status_code, 422)

        with patch(
            "dav.views.principal._parse_propfind_payload", return_value=(None, None)
        ):
            parse_none = view.propfind(request, self.owner.username)
        self.assertEqual(parse_none.status_code, 400)

        with patch(
            "dav.views.principal._parse_propfind_payload",
            return_value=({"mode": "allprop", "requested": None}, None),
        ):
            propfind_ok = view.propfind(request, self.owner.username)
        self.assertEqual(propfind_ok.status_code, 207)

    def test_uid_view_dispatch_guards_and_mapping(self):
        request = self.factory.get("/dav/")
        request.user = self.owner

        mixin = GuidToUsernameDispatchMixin()
        self.assertEqual(
            mixin.guid_to_username("10000000-0000-0000-0000-000000000001"),
            "user01",
        )
        self.assertIsNone(
            mixin.guid_to_username("10000000-0000-0000-0000-000000000000")
        )
        self.assertIsNone(mixin.guid_to_username("not-a-guid"))

        for cls in (
            CalendarHomeUidView,
            CalendarCollectionUidView,
            CalendarObjectUidView,
            PrincipalUidView,
        ):
            view = cls()
            not_str = view.dispatch(request, guid=123)
            self.assertEqual(not_str.status_code, 404)

            with patch.object(
                GuidToUsernameDispatchMixin, "guid_to_username", return_value=None
            ):
                missing = view.dispatch(request, guid="bad-guid")
            self.assertEqual(missing.status_code, 404)

    def test_uid_dispatch_routes_to_username_handlers(self):
        home_request = self.factory.get("/dav/")
        home_request.user = self.owner
        with patch.object(
            GuidToUsernameDispatchMixin,
            "guid_to_username",
            return_value=self.owner.username,
        ):
            response = CalendarHomeUidView.as_view()(home_request, guid="good")
        self.assertEqual(response.status_code, 200)

        principal_request = self.factory.get("/dav/")
        principal_request.user = self.owner
        with patch.object(
            GuidToUsernameDispatchMixin,
            "guid_to_username",
            return_value=self.owner.username,
        ):
            response = PrincipalUidView.as_view()(principal_request, guid="good")
        self.assertEqual(response.status_code, 200)

        object_request = self.factory.get("/dav/")
        object_request.user = self.owner
        with patch.object(
            GuidToUsernameDispatchMixin,
            "guid_to_username",
            return_value=self.owner.username,
        ):
            response = CalendarObjectUidView.as_view()(
                object_request,
                guid="good",
                slug=self.calendar.slug,
                filename="missing.ics",
            )
        self.assertEqual(response.status_code, 404)
