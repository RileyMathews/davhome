from datetime import datetime, timezone

from django.test import SimpleTestCase

from dav.core import recurrence as core_recurrence


class DavCoreRecurrenceTests(SimpleTestCase):
    def test_extract_and_parse_helpers(self):
        text = "BEGIN:VEVENT\nUID:1\nEND:VEVENT\nBEGIN:VTODO\nUID:2\nEND:VTODO\n"
        blocks = core_recurrence.extract_component_blocks(text, "VEVENT")
        self.assertEqual(len(blocks), 1)

        wrapped = core_recurrence.calendar_for_component_text(
            "BEGIN:VEVENT\nUID:1\nEND:VEVENT"
        )
        self.assertIn("BEGIN:VCALENDAR", wrapped)
        self.assertEqual(
            core_recurrence.parse_rrule_count("RRULE:FREQ=DAILY;COUNT=3"), 3
        )

    def test_parse_line_and_line_matches_time_range(self):
        dt = core_recurrence.parse_line_datetime_with_tz(
            "DTSTART;TZID=UTC:20260220T101112"
        )
        self.assertEqual(dt, datetime(2026, 2, 20, 10, 11, 12, tzinfo=timezone.utc))

        self.assertTrue(
            core_recurrence.line_matches_time_range(
                "DTSTART:20260220T101112Z",
                {"start": "20260220T090000Z", "end": "20260220T110000Z"},
            )
        )

    def test_recurrence_and_alarm_matching(self):
        recurring_event = (
            "BEGIN:VEVENT\n"
            "UID:evt-2\n"
            "DTSTART:20260220T100000Z\n"
            "RRULE:FREQ=DAILY;COUNT=2\n"
            "BEGIN:VALARM\n"
            "TRIGGER:-PT15M\n"
            "END:VALARM\n"
            "END:VEVENT\n"
        )
        self.assertTrue(
            core_recurrence.matches_time_range_recurrence(
                recurring_event,
                datetime(2026, 2, 20, 0, 0, tzinfo=timezone.utc),
                datetime(2026, 2, 22, 0, 0, tzinfo=timezone.utc),
                "VEVENT",
            )
        )
        self.assertTrue(
            core_recurrence.alarm_matches_time_range(
                recurring_event,
                {"start": "20260220T094000Z", "end": "20260220T095000Z"},
            )
        )
