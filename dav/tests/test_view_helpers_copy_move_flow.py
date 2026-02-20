from types import SimpleNamespace

from django.contrib.auth.models import User
from django.test import TestCase

from calendars.models import Calendar
from dav.view_helpers.copy_move import copy_or_move_calendar_object


class CopyMoveFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="owner", password="pw")
        cls.calendar = Calendar.objects.create(
            owner=cls.user, slug="family", name="Family"
        )

    def _create_object(self, filename, uid):
        return self.calendar.calendar_objects.create(
            uid=uid,
            filename=filename,
            etag='"e1"',
            ical_blob="BEGIN:VCALENDAR\nEND:VCALENDAR\n",
            content_type="text/calendar; charset=utf-8",
            size=32,
            dead_properties={},
        )

    def _request(self, destination, overwrite="T", depth="infinity"):
        return SimpleNamespace(
            headers={
                "Destination": destination,
                "Overwrite": overwrite,
                "Depth": depth,
            }
        )

    def _collection_exists(self, writable, parent):
        if parent == "":
            return True
        return writable.calendar_objects.filter(filename=f"{parent}/").exists()

    def test_copy_single_resource(self):
        self._create_object("source.ics", "dav:source.ics")
        changes = []

        response = copy_or_move_calendar_object(
            writable=self.calendar,
            request=self._request("/dav/calendars/owner/family/copied.ics"),
            username="owner",
            slug="family",
            filename="source.ics",
            next_revision=1,
            is_move=False,
            collection_exists=self._collection_exists,
            create_calendar_change=lambda *_args: changes.append(_args[2:]),
            dav_common_headers=lambda response: response,
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            self.calendar.calendar_objects.filter(
                filename="copied.ics", uid="dav:copied.ics"
            ).exists()
        )
        self.assertEqual(response["Location"], "/dav/calendars/owner/family/copied.ics")
        self.assertEqual(len(changes), 1)

    def test_copy_rejects_missing_source_bad_destination_and_missing_parent(self):
        response = copy_or_move_calendar_object(
            writable=self.calendar,
            request=self._request("/dav/calendars/owner/family/copied.ics"),
            username="owner",
            slug="family",
            filename="missing.ics",
            next_revision=1,
            is_move=False,
            collection_exists=self._collection_exists,
            create_calendar_change=lambda *_args: None,
            dav_common_headers=lambda response: response,
        )
        self.assertEqual(response.status_code, 404)

        src = self._create_object("source.ics", "dav:source.ics")
        response = copy_or_move_calendar_object(
            writable=self.calendar,
            request=SimpleNamespace(headers={"Destination": "/"}),
            username="owner",
            slug="family",
            filename=src.filename,
            next_revision=1,
            is_move=False,
            collection_exists=self._collection_exists,
            create_calendar_change=lambda *_args: None,
            dav_common_headers=lambda response: response,
        )
        self.assertEqual(response.status_code, 400)

        response = copy_or_move_calendar_object(
            writable=self.calendar,
            request=self._request("/dav/calendars/owner/family/folder/copied.ics"),
            username="owner",
            slug="family",
            filename=src.filename,
            next_revision=1,
            is_move=False,
            collection_exists=self._collection_exists,
            create_calendar_change=lambda *_args: None,
            dav_common_headers=lambda response: response,
        )
        self.assertEqual(response.status_code, 409)

    def test_copy_respects_overwrite_flag_and_identity_destination(self):
        self._create_object("source.ics", "dav:source.ics")
        self._create_object("target.ics", "dav:target.ics")

        response = copy_or_move_calendar_object(
            writable=self.calendar,
            request=self._request(
                "/dav/calendars/owner/family/target.ics", overwrite="F"
            ),
            username="owner",
            slug="family",
            filename="source.ics",
            next_revision=1,
            is_move=False,
            collection_exists=self._collection_exists,
            create_calendar_change=lambda *_args: None,
            dav_common_headers=lambda response: response,
        )
        self.assertEqual(response.status_code, 412)

        response = copy_or_move_calendar_object(
            writable=self.calendar,
            request=self._request("/dav/calendars/owner/family/source.ics"),
            username="owner",
            slug="family",
            filename="source.ics",
            next_revision=1,
            is_move=False,
            collection_exists=self._collection_exists,
            create_calendar_change=lambda *_args: None,
            dav_common_headers=lambda response: response,
        )
        self.assertEqual(response.status_code, 204)

    def test_copy_overwrite_replaces_existing_resource(self):
        self._create_object("source.ics", "dav:source.ics")
        self._create_object("target.ics", "dav:target.ics")

        changes = []
        response = copy_or_move_calendar_object(
            writable=self.calendar,
            request=self._request(
                "/dav/calendars/owner/family/target.ics",
                overwrite="T",
            ),
            username="owner",
            slug="family",
            filename="source.ics",
            next_revision=1,
            is_move=False,
            collection_exists=self._collection_exists,
            create_calendar_change=lambda *_args: changes.append(_args[2:]),
            dav_common_headers=lambda response: response,
        )

        self.assertEqual(response.status_code, 204)
        self.assertTrue(
            self.calendar.calendar_objects.filter(
                filename="target.ics",
                uid="dav:target.ics",
            ).exists()
        )
        self.assertTrue(
            any(change[0] == "target.ics" and change[2] for change in changes)
        )
        self.assertTrue(
            any(change[0] == "target.ics" and not change[2] for change in changes)
        )

    def test_destination_that_resolves_to_empty_path_returns_forbidden(self):
        self._create_object("source.ics", "dav:source.ics")
        response = copy_or_move_calendar_object(
            writable=self.calendar,
            request=self._request("/dav/calendars/owner/family/"),
            username="owner",
            slug="family",
            filename="source.ics",
            next_revision=1,
            is_move=False,
            collection_exists=self._collection_exists,
            create_calendar_change=lambda *_args: None,
            dav_common_headers=lambda response: response,
        )
        self.assertEqual(response.status_code, 403)

    def test_move_collection_and_depth_zero_copy_behavior(self):
        self._create_object("folder/", "collection:folder/")
        self._create_object("folder/a.ics", "dav:folder/a.ics")
        self._create_object("folder/b.ics", "dav:folder/b.ics")

        changes = []
        response = copy_or_move_calendar_object(
            writable=self.calendar,
            request=self._request(
                "/dav/calendars/owner/family/copied-folder/", depth="0"
            ),
            username="owner",
            slug="family",
            filename="folder/",
            next_revision=1,
            is_move=False,
            collection_exists=self._collection_exists,
            create_calendar_change=lambda *_args: changes.append(_args[2:]),
            dav_common_headers=lambda response: response,
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            self.calendar.calendar_objects.filter(filename="copied-folder/").exists()
        )
        self.assertFalse(
            self.calendar.calendar_objects.filter(
                filename="copied-folder/a.ics"
            ).exists()
        )

        response = copy_or_move_calendar_object(
            writable=self.calendar,
            request=self._request("/dav/calendars/owner/family/moved-folder/"),
            username="owner",
            slug="family",
            filename="folder/",
            next_revision=50,
            is_move=True,
            collection_exists=self._collection_exists,
            create_calendar_change=lambda *_args: changes.append(_args[2:]),
            dav_common_headers=lambda response: response,
        )
        self.assertIn(response.status_code, {201, 204})
        self.assertFalse(
            self.calendar.calendar_objects.filter(
                filename__startswith="folder/"
            ).exists()
        )
        self.assertTrue(
            self.calendar.calendar_objects.filter(filename="moved-folder/").exists()
        )

    def test_source_resolution_handles_double_trailing_slash_collection(self):
        self._create_object("folder/", "collection:folder/")
        response = copy_or_move_calendar_object(
            writable=self.calendar,
            request=self._request("/dav/calendars/owner/family/copied-folder/"),
            username="owner",
            slug="family",
            filename="folder//",
            next_revision=1,
            is_move=False,
            collection_exists=self._collection_exists,
            create_calendar_change=lambda *_args: None,
            dav_common_headers=lambda response: response,
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            self.calendar.calendar_objects.filter(filename="copied-folder/").exists()
        )

    def test_collection_overwrite_deletes_destination_entries(self):
        self._create_object("src/", "collection:src/")
        self._create_object("src/a.ics", "dav:src/a.ics")
        self._create_object("dst/", "collection:dst/")
        self._create_object("dst/old.ics", "dav:dst/old.ics")

        changes = []
        response = copy_or_move_calendar_object(
            writable=self.calendar,
            request=self._request("/dav/calendars/owner/family/dst/", overwrite="T"),
            username="owner",
            slug="family",
            filename="src/",
            next_revision=1,
            is_move=False,
            collection_exists=self._collection_exists,
            create_calendar_change=lambda *_args: changes.append(_args[2:]),
            dav_common_headers=lambda response: response,
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(
            self.calendar.calendar_objects.filter(filename="dst/old.ics").exists(),
        )
        self.assertTrue(
            self.calendar.calendar_objects.filter(filename="dst/a.ics").exists(),
        )
        self.assertTrue(
            any(change[0] == "dst/old.ics" and change[2] for change in changes)
        )

    def test_resource_overwrite_deletes_destination_entry(self):
        self._create_object("src.ics", "dav:src.ics")
        self._create_object("dst.ics", "dav:dst.ics")

        changes = []
        response = copy_or_move_calendar_object(
            writable=self.calendar,
            request=self._request("/dav/calendars/owner/family/dst.ics", overwrite="T"),
            username="owner",
            slug="family",
            filename="src.ics",
            next_revision=1,
            is_move=False,
            collection_exists=self._collection_exists,
            create_calendar_change=lambda *_args: changes.append(_args[2:]),
            dav_common_headers=lambda response: response,
        )

        self.assertEqual(response.status_code, 204)
        self.assertTrue(
            self.calendar.calendar_objects.filter(
                filename="dst.ics", uid="dav:dst.ics"
            ).exists()
        )
        self.assertTrue(any(change[0] == "dst.ics" and change[2] for change in changes))

    def test_collection_source_with_extra_slashes_uses_marker_resolution(self):
        self._create_object("folder/", "collection:folder/")

        response = copy_or_move_calendar_object(
            writable=self.calendar,
            request=self._request("/dav/calendars/owner/family/copied-folder/"),
            username="owner",
            slug="family",
            filename="/folder/",
            next_revision=1,
            is_move=False,
            collection_exists=self._collection_exists,
            create_calendar_change=lambda *_args: None,
            dav_common_headers=lambda response: response,
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            self.calendar.calendar_objects.filter(filename="copied-folder/").exists()
        )

    def test_destination_with_empty_relative_path_returns_forbidden(self):
        self._create_object("src.ics", "dav:src.ics")

        response = copy_or_move_calendar_object(
            writable=self.calendar,
            request=self._request("/dav/calendars/owner/family/"),
            username="owner",
            slug="family",
            filename="src.ics",
            next_revision=1,
            is_move=False,
            collection_exists=self._collection_exists,
            create_calendar_change=lambda *_args: None,
            dav_common_headers=lambda response: response,
        )

        self.assertEqual(response.status_code, 403)

    def test_missing_destination_header_returns_bad_request(self):
        self._create_object("source.ics", "dav:source.ics")
        response = copy_or_move_calendar_object(
            writable=self.calendar,
            request=SimpleNamespace(headers={}),
            username="owner",
            slug="family",
            filename="source.ics",
            next_revision=1,
            is_move=False,
            collection_exists=self._collection_exists,
            create_calendar_change=lambda *_args: None,
            dav_common_headers=lambda response: response,
        )
        self.assertEqual(response.status_code, 400)

    def test_copy_rejects_empty_destination_path_with_403(self):
        self._create_object("source.ics", "dav:source.ics")
        response = copy_or_move_calendar_object(
            writable=self.calendar,
            request=self._request("/dav/calendars/owner/family/"),
            username="owner",
            slug="family",
            filename="source.ics",
            next_revision=1,
            is_move=False,
            collection_exists=self._collection_exists,
            create_calendar_change=lambda *_args: None,
            dav_common_headers=lambda response: response,
        )
        self.assertEqual(response.status_code, 403)

    def test_copy_collection_source_with_extra_slashes_resolves_marker(self):
        self._create_object("folder/", "collection:folder/")
        self._create_object("folder/a.ics", "dav:folder/a.ics")
        response = copy_or_move_calendar_object(
            writable=self.calendar,
            request=self._request("/dav/calendars/owner/family/copied-folder/"),
            username="owner",
            slug="family",
            filename="/folder/",
            next_revision=1,
            is_move=False,
            collection_exists=self._collection_exists,
            create_calendar_change=lambda *_args: None,
            dav_common_headers=lambda response: response,
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            self.calendar.calendar_objects.filter(
                filename="copied-folder/a.ics"
            ).exists()
        )

    def test_copy_resource_overwrite_deletes_existing_destination(self):
        self._create_object("source.ics", "dav:source.ics")
        self._create_object("target.ics", "dav:target.ics")
        changes = []
        response = copy_or_move_calendar_object(
            writable=self.calendar,
            request=self._request(
                "/dav/calendars/owner/family/target.ics", overwrite="T"
            ),
            username="owner",
            slug="family",
            filename="source.ics",
            next_revision=1,
            is_move=False,
            collection_exists=self._collection_exists,
            create_calendar_change=lambda *_args: changes.append(_args[2:]),
            dav_common_headers=lambda response: response,
        )
        self.assertEqual(response.status_code, 204)
        self.assertTrue(
            any(change[0] == "target.ics" and change[2] for change in changes)
        )
