from unittest.mock import patch

from django.contrib.auth.models import User
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from calendars.models import Calendar, CalendarObject
from dav.views.calendar_object import CalendarObjectView


class CalendarObjectViewTests(TestCase):
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
        cls.litmus = Calendar.objects.create(
            owner=cls.owner,
            slug="litmus",
            name="Litmus",
            timezone="UTC",
        )
        cls.object = CalendarObject.objects.create(
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
        view = CalendarObjectView()
        view.request = request
        return view

    def test_validate_path_args_rejects_non_strings(self):
        request = self.factory.get("/dav/")
        request.user = self.owner

        response = self._view(request)._validate_path_args("owner", "family", 1)

        self.assertEqual(response.status_code, 404)

    def test_resolve_writable_calendar_handles_none_and_false(self):
        request = self.factory.get("/dav/")
        request.user = self.owner
        view = self._view(request)

        with patch(
            "dav.views.calendar_object.get_calendar_for_write_user", return_value=None
        ):
            writable, error = view._resolve_writable_calendar(
                self.owner, "owner", "family"
            )
        self.assertIsNone(writable)
        self.assertEqual(error.status_code, 404)

        with patch(
            "dav.views.calendar_object.get_calendar_for_write_user", return_value=False
        ):
            writable, error = view._resolve_writable_calendar(
                self.owner, "owner", "family"
            )
        self.assertIsNone(writable)
        self.assertEqual(error.status_code, 403)

    def test_options_sets_allow_and_dav_headers(self):
        request = self.factory.options("/dav/")
        request.user = self.owner

        response = self._view(request).options(request)

        self.assertEqual(response.status_code, 204)
        self.assertIn("COPY", response["Allow"])
        self.assertIn("calendar-access", response["DAV"])

    def test_get_object_normalizes_trailing_slash(self):
        request = self.factory.get("/dav/")
        request.user = self.owner
        view = self._view(request)

        with patch(
            "dav.views.calendar_object.get_calendar_object_for_user", return_value=None
        ) as mocked:
            view._get_object(self.owner, "owner", "family", "folder/")

        self.assertTrue(mocked.called)

    def test_get_existing_writable_object_uses_marker_fallback(self):
        request = self.factory.get("/dav/")
        request.user = self.owner
        view = self._view(request)

        marker_obj = CalendarObject.objects.create(
            calendar=self.litmus,
            uid="marker",
            filename="marker/",
            etag='"m"',
            ical_blob="",
            content_type="httpd/unix-directory",
            size=0,
        )

        found = view._get_existing_writable_object(self.litmus, "folder/", "marker/")

        self.assertEqual(found, marker_obj)

    def test_get_invalid_path_returns_404(self):
        request = self.factory.get("/dav/")
        request.user = self.owner

        response = self._view(request).get(request, "owner", "family", 123)

        self.assertEqual(response.status_code, 404)

    def test_head_returns_404_for_invalid_and_missing_object(self):
        request = self.factory.head("/dav/")
        request.user = self.owner
        view = self._view(request)

        invalid = view.head(request, "owner", "family", 123)
        missing = view.head(request, "owner", "family", "missing.ics")

        self.assertEqual(invalid.status_code, 404)
        self.assertEqual(missing.status_code, 404)

    def test_head_success_sets_metadata_headers(self):
        request = self.factory.head("/dav/")
        request.user = self.owner

        response = self._view(request).head(
            request,
            self.owner.username,
            self.calendar.slug,
            self.object.filename,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("ETag", response)
        self.assertIn("Last-Modified", response)
        self.assertEqual(response["Content-Length"], str(self.object.size))

    def test_propfind_returns_errors_for_path_missing_and_parse(self):
        request = self.factory.generic("PROPFIND", "/dav/")
        request.user = self.owner
        view = self._view(request)

        invalid = view.propfind(request, "owner", "family", 123)
        missing = view.propfind(request, "owner", "family", "missing.ics")

        with patch(
            "dav.views.calendar_object._parse_propfind_payload",
            return_value=(None, HttpResponse(status=422)),
        ):
            parse_error = view.propfind(
                request,
                self.owner.username,
                self.calendar.slug,
                self.object.filename,
            )

        with patch(
            "dav.views.calendar_object._parse_propfind_payload",
            return_value=(None, None),
        ):
            empty_parsed = view.propfind(
                request,
                self.owner.username,
                self.calendar.slug,
                self.object.filename,
            )

        self.assertEqual(invalid.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(parse_error.status_code, 422)
        self.assertEqual(empty_parsed.status_code, 400)

    def test_delete_handles_invalid_writable_error_and_missing_existing(self):
        request = self.factory.delete("/dav/")
        request.user = self.owner
        view = self._view(request)

        invalid = view.delete(request, "owner", "family", 123)
        self.assertEqual(invalid.status_code, 404)

        with patch.object(
            view,
            "_resolve_writable_calendar",
            return_value=(None, HttpResponse(status=403)),
        ):
            writable_error = view.delete(request, "owner", "family", "event-1.ics")
        self.assertEqual(writable_error.status_code, 403)

        missing = view.delete(
            request, self.owner.username, self.calendar.slug, "missing.ics"
        )
        self.assertEqual(missing.status_code, 404)

    def test_delete_collection_deletes_prefix_objects(self):
        CalendarObject.objects.create(
            calendar=self.litmus,
            uid="c",
            filename="coll/",
            etag='"c"',
            ical_blob="",
            content_type="httpd/unix-directory",
            size=0,
        )
        CalendarObject.objects.create(
            calendar=self.litmus,
            uid="child",
            filename="coll/item.txt",
            etag='"child"',
            ical_blob="x",
            content_type="text/plain",
            size=1,
        )
        request = self.factory.delete("/dav/")
        request.user = self.owner

        response = self._view(request).delete(
            request, self.owner.username, "litmus", "coll/"
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(
            CalendarObject.objects.filter(
                calendar=self.litmus, filename__startswith="coll/"
            ).exists()
        )

    def test_proppatch_handles_invalid_writable_forbidden_and_bad_xml(self):
        request = self.factory.generic(
            "PROPPATCH", "/dav/", data=b"<x", content_type="application/xml"
        )
        request.user = self.owner
        view = self._view(request)

        invalid = view.proppatch(request, "owner", "family", 123)
        self.assertEqual(invalid.status_code, 404)

        with patch.object(
            view,
            "_resolve_writable_calendar",
            return_value=(None, HttpResponse(status=403)),
        ):
            writable_error = view.proppatch(request, "owner", "family", "prop")
        self.assertEqual(writable_error.status_code, 403)

        wrong_slug = view.proppatch(
            request, self.owner.username, self.calendar.slug, self.object.filename
        )
        self.assertEqual(wrong_slug.status_code, 405)

        bad_xml = view.proppatch(request, self.owner.username, "litmus", "missing")
        self.assertEqual(bad_xml.status_code, 400)

    def test_proppatch_handles_missing_object_and_dead_property_mutations(self):
        request_missing = self.factory.generic(
            "PROPPATCH",
            "/dav/",
            data=b"<D:propertyupdate xmlns:D='DAV:'/>",
            content_type="application/xml",
        )
        request_missing.user = self.owner

        missing = self._view(request_missing).proppatch(
            request_missing,
            self.owner.username,
            "litmus",
            "missing",
        )
        self.assertEqual(missing.status_code, 404)

        obj = CalendarObject.objects.create(
            calendar=self.litmus,
            uid="prop",
            filename="prop",
            etag='"prop"',
            ical_blob="x",
            content_type="text/plain",
            size=1,
            dead_properties={"{urn:test}remove": "<X/>"},
        )
        body = b"""
<D:propertyupdate xmlns:D='DAV:' xmlns:X='urn:test'>
  <D:set>
    <D:prop><D:getetag /></D:prop>
  </D:set>
  <D:set />
  <X:noop />
  <D:remove>
    <D:prop><X:remove /></D:prop>
  </D:remove>
</D:propertyupdate>
"""
        request = self.factory.generic(
            "PROPPATCH",
            "/dav/",
            data=body,
            content_type="application/xml",
        )
        request.user = self.owner

        response = self._view(request).proppatch(
            request, self.owner.username, "litmus", "prop"
        )

        self.assertEqual(response.status_code, 207)
        obj.refresh_from_db()
        self.assertNotIn("{urn:test}remove", obj.dead_properties)

    def test_mkcollection_handles_invalid_writable_and_policy_conflicts(self):
        request = self.factory.generic("MKCOL", "/dav/")
        request.user = self.owner
        view = self._view(request)

        invalid = view._mkcollection(request, "owner", "family", 123)
        self.assertEqual(invalid.status_code, 404)

        with patch.object(
            view,
            "_resolve_writable_calendar",
            return_value=(None, HttpResponse(status=403)),
        ):
            writable_error = view._mkcollection(request, "owner", "family", "coll/")
        self.assertEqual(writable_error.status_code, 403)

        wrong_slug = view._mkcollection(
            request, self.owner.username, self.calendar.slug, "coll/"
        )
        self.assertEqual(wrong_slug.status_code, 403)

        bad_mkcol = self.factory.generic(
            "MKCOL", "/dav/", data=b"body", content_type="text/plain"
        )
        bad_mkcol.user = self.owner
        body_rejected = view._mkcollection(
            bad_mkcol, self.owner.username, "litmus", "coll/"
        )
        self.assertEqual(body_rejected.status_code, 415)

        missing_parent = view._mkcollection(
            request, self.owner.username, "litmus", "nope/coll/"
        )
        self.assertEqual(missing_parent.status_code, 409)

    def test_mkcollection_rejects_existing_and_mkcalendar_wrapper(self):
        CalendarObject.objects.create(
            calendar=self.litmus,
            uid="existing-marker",
            filename="exists/",
            etag='"exists"',
            ical_blob="",
            content_type="httpd/unix-directory",
            size=0,
        )
        request = self.factory.generic("MKCOL", "/dav/")
        request.user = self.owner
        view = self._view(request)

        conflict = view._mkcollection(request, self.owner.username, "litmus", "exists/")
        self.assertEqual(conflict.status_code, 405)

        mkcalendar_request = self.factory.generic("MKCALENDAR", "/dav/")
        mkcalendar_request.user = self.owner
        created = view.mkcalendar(
            mkcalendar_request, self.owner.username, "litmus", "fresh/"
        )
        self.assertEqual(created.status_code, 201)

    def test_copy_or_move_handles_invalid_writable_and_non_litmus(self):
        request = self.factory.generic("COPY", "/dav/")
        request.user = self.owner
        view = self._view(request)

        invalid = view._copy_or_move(request, "owner", "family", 123)
        self.assertEqual(invalid.status_code, 404)

        with patch.object(
            view,
            "_resolve_writable_calendar",
            return_value=(None, HttpResponse(status=403)),
        ):
            writable_error = view._copy_or_move(request, "owner", "family", "a.ics")
        self.assertEqual(writable_error.status_code, 403)

        wrong_slug = view._copy_or_move(
            request, self.owner.username, self.calendar.slug, self.object.filename
        )
        self.assertEqual(wrong_slug.status_code, 405)

    def test_copy_or_move_maps_integrity_error_to_409(self):
        request = self.factory.generic("COPY", "/dav/")
        request.user = self.owner
        view = self._view(request)

        with patch(
            "dav.views.calendar_object.copy_or_move_calendar_object",
            side_effect=Exception("boom"),
        ):
            # ensure generic exception does not get swallowed by IntegrityError branch
            with self.assertRaises(Exception):
                view._copy_or_move(request, self.owner.username, "litmus", "a.ics")

        with patch(
            "dav.views.calendar_object.copy_or_move_calendar_object",
            side_effect=__import__("django.db").db.IntegrityError,
        ):
            response = view._copy_or_move(
                request, self.owner.username, "litmus", "a.ics"
            )
        self.assertEqual(response.status_code, 409)

    def test_put_invalid_path_and_invalid_payload_parse(self):
        request = self.factory.put("/dav/", data=b"", content_type="text/plain")
        request.user = self.owner
        view = self._view(request)

        invalid = view.put(request, "owner", "family", 123)
        self.assertEqual(invalid.status_code, 404)

        with patch(
            "dav.views.calendar_object.core_payloads.validate_generic_payload",
            return_value=(None, None),
        ):
            response = view.put(request, self.owner.username, "litmus", "prop")
        self.assertEqual(response.status_code, 400)
