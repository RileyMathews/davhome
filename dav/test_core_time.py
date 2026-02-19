from datetime import date, datetime, timedelta, timezone

from django.test import SimpleTestCase

from dav.core import time as core_time


class DavCoreTimeTests(SimpleTestCase):
    def test_parse_ical_datetime(self):
        self.assertEqual(
            core_time.parse_ical_datetime("20260220"),
            datetime(2026, 2, 20, tzinfo=timezone.utc),
        )
        self.assertEqual(
            core_time.parse_ical_datetime("20260220T101112Z"),
            datetime(2026, 2, 20, 10, 11, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(
            core_time.parse_ical_datetime("20260220T101112"),
            datetime(2026, 2, 20, 10, 11, 12, tzinfo=timezone.utc),
        )
        self.assertIsNone(core_time.parse_ical_datetime("bad"))

    def test_parse_and_format_ical_duration(self):
        self.assertEqual(
            core_time.parse_ical_duration("-P1DT2H3M4S"),
            -timedelta(days=1, hours=2, minutes=3, seconds=4),
        )
        self.assertEqual(core_time.parse_ical_duration("nope"), None)
        self.assertEqual(core_time.format_ical_duration(timedelta(0)), "PT0S")
        self.assertEqual(
            core_time.format_ical_duration(timedelta(days=2, minutes=5)),
            "P2DT5M",
        )

    def test_format_value_date_or_datetime(self):
        text, is_date = core_time.format_value_date_or_datetime(
            datetime(2026, 2, 20, 10, 11, 12, tzinfo=timezone.utc)
        )
        self.assertEqual(text, "20260220T101112Z")
        self.assertFalse(is_date)

        text, is_date = core_time.format_value_date_or_datetime(date(2026, 2, 20))
        self.assertEqual(text, "20260220")
        self.assertTrue(is_date)

    def test_as_utc_datetime(self):
        naive = datetime(2026, 2, 20, 10, 11, 12)
        self.assertEqual(
            core_time.as_utc_datetime(naive),
            datetime(2026, 2, 20, 10, 11, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(
            core_time.as_utc_datetime(date(2026, 2, 20)),
            datetime(2026, 2, 20, 0, 0, tzinfo=timezone.utc),
        )

    def test_unfold_and_line_helpers(self):
        ical = "SUMMARY:Hello\r\n World\r\nDTSTART:20260220T101112Z"
        self.assertEqual(
            core_time.unfold_ical(ical),
            "SUMMARY:HelloWorld\r\nDTSTART:20260220T101112Z",
        )
        self.assertEqual(core_time.first_ical_line_value(ical, "SUMMARY"), "Hello")
        self.assertEqual(
            core_time.first_ical_line(ical, "DTSTART"),
            "DTSTART:20260220T101112Z",
        )
