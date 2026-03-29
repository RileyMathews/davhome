from unittest.mock import patch
from xml.etree import ElementTree as ET

from django.contrib.auth.models import User
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from calendars.models import Calendar, CalendarObject
from dav.views.calendar_collection import CalendarCollectionView
from dav.xml import NS_CALDAV, qname


class CalendarCollectionViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(username="owner", password="pw-test-12345")
        cls.other = User.objects.create_user(username="other", password="pw-test-12345")
        cls.calendar = Calendar.objects.create(
            owner=cls.owner,
            slug="family",
            name="Family",
            timezone="UTC",
        )
        CalendarObject.objects.create(
            calendar=cls.calendar,
            uid="uid-1",
            filename="event-1.ics",
            etag='"etag-1"',
            ical_blob="BEGIN:VCALENDAR\nEND:VCALENDAR\n",
            size=30,
        )

    def setUp(self):
        self.factory = RequestFactory()

    def _view(self, request):
        view = CalendarCollectionView()
        view.request = request
        return view

    def test_resolve_owner_rejects_unknown_principal(self):
        request = self.factory.get("/dav/")
        request.user = self.owner

        with patch("dav.views.calendar_collection.get_principal", return_value=None):
            error = self._view(request)._resolve_owner(request, "missing")

        self.assertEqual(error.status_code, 404)

    def test_resolve_calendar_allows_report_fallback_for_freebusy(self):
        request = self.factory.generic(
            "REPORT",
            "/dav/calendars/owner/fallback/",
            data=(
                b"<?xml version='1.0' encoding='utf-8'?>"
                b"<C:free-busy-query xmlns:C='urn:ietf:params:xml:ns:caldav'/>"
            ),
            content_type="application/xml",
        )
        request.user = self.owner
        fallback = Calendar.objects.create(
            owner=self.owner,
            slug="fallback",
            name="Fallback",
            timezone="UTC",
        )

        with (
            patch(
                "dav.views.calendar_collection.get_calendar_for_user", return_value=None
            ),
            patch(
                "dav.views.calendar_collection.get_principal", return_value=self.owner
            ),
        ):
            calendar = self._view(request)._resolve_calendar(
                request,
                self.owner.username,
                "fallback",
                allow_report_fallback=True,
            )

        self.assertEqual(calendar, fallback)

    def test_resolve_calendar_returns_404_when_not_found(self):
        request = self.factory.get("/dav/")
        request.user = self.owner

        with patch(
            "dav.views.calendar_collection.get_calendar_for_user", return_value=None
        ):
            error = self._view(request)._resolve_calendar(
                request,
                self.owner.username,
                "missing",
            )

        self.assertEqual(error.status_code, 404)

    def test_resolve_calendar_report_fallback_non_freebusy_returns_404(self):
        request = self.factory.generic("REPORT", "/dav/calendars/owner/fallback/")
        request.user = self.owner

        with (
            patch(
                "dav.views.calendar_collection.get_calendar_for_user", return_value=None
            ),
            patch(
                "dav.views.calendar_collection.get_principal", return_value=self.owner
            ),
            patch(
                "dav.views.calendar_collection._parse_xml_body",
                return_value=ET.Element(qname("DAV:", "propfind")),
            ),
        ):
            error = self._view(request)._resolve_calendar(
                request,
                self.owner.username,
                "fallback",
                allow_report_fallback=True,
            )

        self.assertEqual(error.status_code, 404)

    def test_options_sets_allow_and_dav_headers(self):
        request = self.factory.options("/dav/")
        request.user = self.owner

        response = self._view(request).options(request)

        self.assertEqual(response.status_code, 204)
        self.assertIn("PROPPATCH", response["Allow"])
        self.assertIn("calendar-access", response["DAV"])

    def test_mkcol_rejects_non_empty_body(self):
        request = self.factory.generic(
            "MKCOL",
            "/dav/calendars/owner/newcal/",
            data=b"not-allowed",
            content_type="text/plain",
        )
        request.user = self.owner

        response = self._view(request).mkcol(request)

        self.assertEqual(response.status_code, 415)

    def test_mkcalendar_rejects_existing_calendar(self):
        request = self.factory.generic("MKCALENDAR", "/dav/calendars/owner/family/")
        request.user = self.owner

        response = self._view(request).mkcalendar(
            request, self.owner.username, "family"
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("resource-must-be-null", response.content.decode("utf-8"))

    def test_mkcalendar_returns_property_error_response(self):
        request = self.factory.generic("MKCALENDAR", "/dav/calendars/owner/newcal/")
        request.user = self.owner

        with patch(
            "dav.views.calendar_collection._mkcalendar_props_from_payload",
            return_value=(None, [], HttpResponse(status=422)),
        ):
            response = self._view(request).mkcalendar(
                request, self.owner.username, "newcal"
            )

        self.assertEqual(response.status_code, 422)

    def test_mkcalendar_rejects_invalid_payload(self):
        request = self.factory.generic("MKCALENDAR", "/dav/calendars/owner/newcal/")
        request.user = self.owner

        with patch(
            "dav.views.calendar_collection._mkcalendar_props_from_payload",
            return_value=(None, [], None),
        ):
            response = self._view(request).mkcalendar(
                request, self.owner.username, "newcal"
            )

        self.assertEqual(response.status_code, 400)

    def test_mkcalendar_returns_multistatus_for_bad_props(self):
        request = self.factory.generic("MKCALENDAR", "/dav/calendars/owner/newcal/")
        request.user = self.owner

        with patch(
            "dav.views.calendar_collection._mkcalendar_props_from_payload",
            return_value=({}, [qname("DAV:", "displayname")], None),
        ):
            response = self._view(request).mkcalendar(
                request, self.owner.username, "newcal"
            )

        self.assertEqual(response.status_code, 207)

    def test_delete_returns_calendar_resolution_error(self):
        request = self.factory.delete("/dav/calendars/owner/family/")
        request.user = self.owner
        view = self._view(request)

        with patch.object(
            view, "_resolve_calendar", return_value=HttpResponse(status=404)
        ):
            response = view.delete(
                request,
                username=self.owner.username,
                slug=self.calendar.slug,
            )

        self.assertEqual(response.status_code, 404)

    def test_delete_returns_owner_resolution_error(self):
        request = self.factory.delete("/dav/calendars/missing/family/")
        request.user = self.owner

        response = self._view(request).delete(
            request,
            username="missing",
            slug=self.calendar.slug,
        )

        self.assertEqual(response.status_code, 404)

    def test_delete_forbidden_when_actor_not_owner(self):
        request = self.factory.delete("/dav/calendars/owner/family/")
        request.user = self.owner
        view = self._view(request)

        with (
            patch.object(view, "_resolve_owner", return_value=(self.other, self.owner)),
            patch.object(view, "_resolve_calendar", return_value=self.calendar),
        ):
            response = view.delete(
                request,
                username=self.owner.username,
                slug=self.calendar.slug,
            )

        self.assertEqual(response.status_code, 403)

    def test_delete_owner_can_delete_calendar(self):
        request = self.factory.delete("/dav/calendars/owner/deleteme/")
        request.user = self.owner
        view = self._view(request)
        calendar = Calendar.objects.create(
            owner=self.owner,
            slug="deleteme",
            name="Delete me",
            timezone="UTC",
        )

        response = view.delete(
            request,
            username=self.owner.username,
            slug=calendar.slug,
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Calendar.objects.filter(id=calendar.id).exists())

    def test_proppatch_rejects_invalid_xml(self):
        request = self.factory.generic(
            "PROPPATCH",
            "/dav/calendars/owner/family/",
            data=b"<not-xml",
            content_type="application/xml",
        )
        request.user = self.owner

        response = self._view(request).proppatch(
            request,
            self.owner.username,
            slug=self.calendar.slug,
        )

        self.assertEqual(response.status_code, 400)

    def test_proppatch_handles_no_updates(self):
        request = self.factory.generic(
            "PROPPATCH",
            "/dav/calendars/owner/family/",
            data=(
                b"<?xml version='1.0' encoding='utf-8'?>"
                b"<D:propertyupdate xmlns:D='DAV:'/>"
            ),
            content_type="application/xml",
        )
        request.user = self.owner

        with patch(
            "dav.views.calendar_collection._calendar_collection_proppatch_plan",
            return_value=({}, set(), [], []),
        ):
            response = self._view(request).proppatch(
                request,
                self.owner.username,
                slug=self.calendar.slug,
            )

        self.assertEqual(response.status_code, 207)

    def test_proppatch_returns_owner_resolution_error(self):
        request = self.factory.generic(
            "PROPPATCH",
            "/dav/calendars/missing/family/",
            data=(
                b"<?xml version='1.0' encoding='utf-8'?>"
                b"<D:propertyupdate xmlns:D='DAV:'/>"
            ),
            content_type="application/xml",
        )
        request.user = self.owner

        response = self._view(request).proppatch(
            request,
            "missing",
            slug=self.calendar.slug,
        )

        self.assertEqual(response.status_code, 404)

    def test_proppatch_returns_calendar_resolution_error(self):
        request = self.factory.generic(
            "PROPPATCH",
            "/dav/calendars/owner/missing/",
            data=(
                b"<?xml version='1.0' encoding='utf-8'?>"
                b"<D:propertyupdate xmlns:D='DAV:'/>"
            ),
            content_type="application/xml",
        )
        request.user = self.owner

        response = self._view(request).proppatch(
            request,
            self.owner.username,
            slug="missing",
        )

        self.assertEqual(response.status_code, 404)

    def test_proppatch_forbidden_when_actor_not_owner(self):
        request = self.factory.generic(
            "PROPPATCH",
            "/dav/calendars/owner/family/",
            data=(
                b"<?xml version='1.0' encoding='utf-8'?>"
                b"<D:propertyupdate xmlns:D='DAV:'/>"
            ),
            content_type="application/xml",
        )
        request.user = self.owner
        view = self._view(request)

        with (
            patch.object(view, "_resolve_owner", return_value=(self.other, self.owner)),
            patch.object(view, "_resolve_calendar", return_value=self.calendar),
        ):
            response = view.proppatch(
                request,
                self.owner.username,
                slug=self.calendar.slug,
            )

        self.assertEqual(response.status_code, 403)

    def test_get_returns_calendar_error(self):
        request = self.factory.get("/dav/calendars/owner/family/")
        request.user = self.owner
        view = self._view(request)

        with patch.object(
            view, "_resolve_calendar", return_value=HttpResponse(status=404)
        ):
            response = view.get(
                request, username=self.owner.username, slug=self.calendar.slug
            )

        self.assertEqual(response.status_code, 404)

    def test_head_returns_not_modified_with_headers(self):
        request = self.factory.head("/dav/calendars/owner/family/")
        request.user = self.owner

        with patch(
            "dav.views.calendar_collection._conditional_not_modified", return_value=True
        ):
            response = self._view(request).head(
                request,
                username=self.owner.username,
                slug=self.calendar.slug,
            )

        self.assertEqual(response.status_code, 304)
        self.assertIn("ETag", response)
        self.assertIn("Last-Modified", response)

    def test_head_success_sets_etag_and_last_modified(self):
        request = self.factory.head("/dav/calendars/owner/family/")
        request.user = self.owner

        with patch(
            "dav.views.calendar_collection._conditional_not_modified",
            return_value=False,
        ):
            response = self._view(request).head(
                request,
                username=self.owner.username,
                slug=self.calendar.slug,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("ETag", response)
        self.assertIn("Last-Modified", response)

    def test_head_returns_calendar_error(self):
        request = self.factory.head("/dav/calendars/owner/family/")
        request.user = self.owner
        view = self._view(request)

        with patch.object(
            view, "_resolve_calendar", return_value=HttpResponse(status=404)
        ):
            response = view.head(
                request, username=self.owner.username, slug=self.calendar.slug
            )

        self.assertEqual(response.status_code, 404)

    def test_report_returns_calendar_error(self):
        request = self.factory.generic("REPORT", "/dav/calendars/owner/family/")
        request.user = self.owner
        view = self._view(request)

        with patch.object(
            view, "_resolve_calendar", return_value=HttpResponse(status=404)
        ):
            response = view.report(
                request, username=self.owner.username, slug=self.calendar.slug
            )

        self.assertEqual(response.status_code, 404)

    def test_propfind_returns_calendar_error(self):
        request = self.factory.generic("PROPFIND", "/dav/calendars/owner/family/")
        request.user = self.owner
        view = self._view(request)

        with patch.object(
            view, "_resolve_calendar", return_value=HttpResponse(status=404)
        ):
            response = view.propfind(
                request, self.owner.username, slug=self.calendar.slug
            )

        self.assertEqual(response.status_code, 404)

    def test_propfind_returns_not_modified(self):
        request = self.factory.generic("PROPFIND", "/dav/calendars/owner/family/")
        request.user = self.owner

        with patch(
            "dav.views.calendar_collection._conditional_not_modified", return_value=True
        ):
            response = self._view(request).propfind(
                request,
                self.owner.username,
                slug=self.calendar.slug,
            )

        self.assertEqual(response.status_code, 304)
        self.assertIn("ETag", response)
        self.assertIn("Last-Modified", response)

    def test_propfind_rejects_empty_parsed_payload(self):
        request = self.factory.generic("PROPFIND", "/dav/calendars/owner/family/")
        request.user = self.owner

        with (
            patch(
                "dav.views.calendar_collection._conditional_not_modified",
                return_value=False,
            ),
            patch(
                "dav.views.calendar_collection._parse_propfind_payload",
                return_value=(None, None),
            ),
        ):
            response = self._view(request).propfind(
                request,
                self.owner.username,
                slug=self.calendar.slug,
            )

        self.assertEqual(response.status_code, 400)

    def test_propfind_depth_one_includes_object_responses(self):
        request = self.factory.generic(
            "PROPFIND",
            "/dav/calendars/owner/family/",
            data=b"",
            content_type="application/xml",
            HTTP_DEPTH="1",
        )
        request.user = self.owner

        with (
            patch(
                "dav.views.calendar_collection._conditional_not_modified",
                return_value=False,
            ),
            patch(
                "dav.views.calendar_collection._parse_propfind_payload",
                return_value=({"mode": "allprop", "requested": None}, None),
            ),
        ):
            response = self._view(request).propfind(
                request,
                self.owner.username,
                slug=self.calendar.slug,
            )

        self.assertEqual(response.status_code, 207)
        xml_text = response.content.decode("utf-8")
        self.assertIn(
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/", xml_text
        )
        self.assertIn(
            f"/dav/calendars/{self.owner.username}/{self.calendar.slug}/event-1.ics",
            xml_text,
        )
