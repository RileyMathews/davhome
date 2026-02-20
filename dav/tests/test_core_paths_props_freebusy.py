from datetime import datetime, timedelta, timezone

from django.test import SimpleTestCase

from dav.core import freebusy as core_freebusy
from dav.core import paths as core_paths
from dav.core import props as core_props
from dav.xml import NS_APPLE_ICAL, NS_CALDAV, NS_DAV, qname


class DavCorePathPropFreebusyTests(SimpleTestCase):
    def test_paths_helpers(self):
        self.assertEqual(core_paths.collection_marker("foo/bar"), "foo/bar/")
        self.assertEqual(
            core_paths.split_filename_path("foo/bar.ics"), ("foo", "bar.ics")
        )
        self.assertEqual(
            core_paths.destination_filename_from_header(
                "/dav/calendars/users/alice/home/file.ics",
                "alice",
                "home",
            ),
            "file.ics",
        )
        self.assertTrue(core_paths.is_ical_resource("a.ics", "text/plain"))
        self.assertEqual(
            core_paths.normalize_content_type("text/calendar; charset=utf-8"),
            "text/calendar;charset=utf-8",
        )
        self.assertEqual(core_paths.normalize_href_path("x/y"), "/x/y")

    def test_props_helpers(self):
        text = core_props.text_prop(qname, NS_DAV, "displayname", "davhome")
        self.assertEqual(text.text, "davhome")

        res = core_props.resourcetype_prop(qname, NS_DAV, (NS_DAV, "collection"))
        self.assertEqual(res.tag, qname(NS_DAV, "resourcetype"))

        ok, missing = core_props.select_props(
            {qname(NS_DAV, "displayname"): lambda: text},
            [qname(NS_DAV, "displayname"), qname(NS_DAV, "owner")],
        )
        self.assertEqual(len(ok), 1)
        self.assertEqual(len(missing), 1)

        supported = core_props.supported_components_prop(
            qname,
            NS_CALDAV,
            "VEVENT",
        )
        self.assertEqual(
            supported.tag, qname(NS_CALDAV, "supported-calendar-component-set")
        )

        timezone_prop = core_props.calendar_timezone_prop(qname, NS_CALDAV, "UTC")
        self.assertIsNotNone(timezone_prop.text)
        self.assertIn("TZID:UTC", timezone_prop.text or "")

        color_prop = core_props.calendar_color_prop(qname, NS_APPLE_ICAL, "#aabbcc")
        self.assertEqual(color_prop.text, "#aabbcc")

        order_prop = core_props.calendar_order_prop(qname, NS_APPLE_ICAL, 5)
        self.assertEqual(order_prop.text, "5")

    def test_freebusy_helpers(self):
        self.assertEqual(
            core_freebusy.format_ical_utc(
                datetime(2026, 2, 20, 10, 11, 12, tzinfo=timezone.utc)
            ),
            "20260220T101112Z",
        )

        parsed = core_freebusy.parse_freebusy_value(
            "20260220T100000Z/PT1H",
            lambda value: datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(
                tzinfo=timezone.utc
            ),
            lambda value: timedelta(hours=1),
            lambda value: value,
        )
        self.assertIsNotNone(parsed)

        merged = core_freebusy.merge_intervals(
            [
                (
                    datetime(2026, 2, 20, 10, 0, tzinfo=timezone.utc),
                    datetime(2026, 2, 20, 11, 0, tzinfo=timezone.utc),
                ),
                (
                    datetime(2026, 2, 20, 10, 30, tzinfo=timezone.utc),
                    datetime(2026, 2, 20, 12, 0, tzinfo=timezone.utc),
                ),
            ]
        )
        self.assertEqual(len(merged), 1)
