from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from dav.view_helpers import report_paths


class ViewHelpersReportPathsTests(SimpleTestCase):
    def test_report_href_style(self):
        self.assertEqual(
            report_paths._report_href_style("/dav/calendars/__uids__/x/y/"), "uids"
        )
        self.assertEqual(
            report_paths._report_href_style("/dav/calendars/users/alice/work/"), "users"
        )
        self.assertEqual(
            report_paths._report_href_style("/dav/calendars/alice/work/"), "username"
        )

    def test_object_and_collection_hrefs(self):
        calendar = SimpleNamespace(owner=SimpleNamespace(username="alice"), slug="work")
        obj = SimpleNamespace(filename="event.ics")
        data = SimpleNamespace(
            owner_username="alice", slug="work", filename="event.ics"
        )

        with patch.object(
            report_paths, "_dav_guid_for_username", return_value="guid-alice"
        ):
            self.assertEqual(
                report_paths._object_href_for_style(calendar, obj, "uids"),
                "/dav/calendars/__uids__/guid-alice/work/event.ics",
            )
            self.assertIn(
                "/dav/calendars/__uids__/guid-alice/work/event.ics",
                report_paths._all_object_hrefs(calendar, obj),
            )
            self.assertEqual(
                report_paths._object_href_for_style_data(data, "uids"),
                "/dav/calendars/__uids__/guid-alice/work/event.ics",
            )
            self.assertIn(
                "/dav/calendars/__uids__/guid-alice/work/event.ics",
                report_paths._all_object_hrefs_for_data(data),
            )
            self.assertEqual(
                report_paths._object_href_for_filename(calendar, "todo.ics", "uids"),
                "/dav/calendars/__uids__/guid-alice/work/todo.ics",
            )
            self.assertEqual(
                report_paths._collection_href_for_style(calendar, "uids"),
                "/dav/calendars/__uids__/guid-alice/work",
            )

        with patch.object(report_paths, "_dav_guid_for_username", return_value=None):
            self.assertEqual(
                report_paths._object_href_for_style(calendar, obj, "uids"),
                "/dav/calendars/alice/work/event.ics",
            )
            self.assertEqual(
                report_paths._object_href_for_style(calendar, obj, "users"),
                "/dav/calendars/users/alice/work/event.ics",
            )
            self.assertEqual(
                report_paths._object_href_for_style(calendar, obj, "username"),
                "/dav/calendars/alice/work/event.ics",
            )
            self.assertEqual(
                report_paths._object_href_for_style_data(data, "uids"),
                "/dav/calendars/alice/work/event.ics",
            )
            self.assertEqual(
                report_paths._object_href_for_style_data(data, "users"),
                "/dav/calendars/users/alice/work/event.ics",
            )
            self.assertEqual(
                report_paths._object_href_for_style_data(data, "username"),
                "/dav/calendars/alice/work/event.ics",
            )
            self.assertEqual(
                report_paths._object_href_for_filename(calendar, "todo.ics", "uids"),
                "/dav/calendars/alice/work/todo.ics",
            )
            self.assertEqual(
                report_paths._object_href_for_filename(calendar, "todo.ics", "users"),
                "/dav/calendars/users/alice/work/todo.ics",
            )
            self.assertEqual(
                report_paths._object_href_for_filename(
                    calendar, "todo.ics", "username"
                ),
                "/dav/calendars/alice/work/todo.ics",
            )
            self.assertEqual(
                report_paths._collection_href_for_style(calendar, "uids"),
                "/dav/calendars/alice/work",
            )
            self.assertEqual(
                report_paths._collection_href_for_style(calendar, "users"),
                "/dav/calendars/users/alice/work",
            )
            self.assertEqual(
                report_paths._collection_href_for_style(calendar, "username"),
                "/dav/calendars/alice/work",
            )
