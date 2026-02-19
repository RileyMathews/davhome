from datetime import datetime, timezone

from django.test import SimpleTestCase

from dav.view_helpers import freebusy as view_freebusy


class ViewHelpersFreebusyTests(SimpleTestCase):
    def test_build_freebusy_response_lines_all_groups(self):
        window_start = datetime(2026, 2, 20, 9, 0, tzinfo=timezone.utc)
        window_end = datetime(2026, 2, 20, 18, 0, tzinfo=timezone.utc)
        busy = [
            (
                datetime(2026, 2, 20, 10, 0, tzinfo=timezone.utc),
                datetime(2026, 2, 20, 11, 0, tzinfo=timezone.utc),
            )
        ]
        tentative = [
            (
                datetime(2026, 2, 20, 11, 0, tzinfo=timezone.utc),
                datetime(2026, 2, 20, 12, 0, tzinfo=timezone.utc),
            )
        ]
        unavailable = [
            (
                datetime(2026, 2, 20, 12, 0, tzinfo=timezone.utc),
                datetime(2026, 2, 20, 13, 0, tzinfo=timezone.utc),
            )
        ]

        lines = view_freebusy._build_freebusy_response_lines(
            window_start,
            window_end,
            busy,
            tentative,
            unavailable,
        )

        rendered = "\n".join(lines)
        self.assertIn("BEGIN:VCALENDAR", rendered)
        self.assertIn("BEGIN:VFREEBUSY", rendered)
        self.assertIn("FREEBUSY:20260220T100000Z/20260220T110000Z", rendered)
        self.assertIn(
            "FREEBUSY;FBTYPE=BUSY-TENTATIVE:20260220T110000Z/20260220T120000Z",
            rendered,
        )
        self.assertIn(
            "FREEBUSY;FBTYPE=BUSY-UNAVAILABLE:20260220T120000Z/20260220T130000Z",
            rendered,
        )

    def test_build_freebusy_response_lines_without_groups(self):
        lines = view_freebusy._build_freebusy_response_lines(
            datetime(2026, 2, 20, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 2, 20, 18, 0, tzinfo=timezone.utc),
            [],
            [],
            [],
        )
        rendered = "\n".join(lines)
        self.assertNotIn("FBTYPE=BUSY-TENTATIVE", rendered)
        self.assertNotIn("FBTYPE=BUSY-UNAVAILABLE", rendered)
        self.assertIn("END:VCALENDAR", rendered)
