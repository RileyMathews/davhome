from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

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
        self.assertFalse(
            core_recurrence.line_matches_time_range(
                "INVALID",
                {"start": "20260220T090000Z", "end": "20260220T110000Z"},
            )
        )

        dt = core_recurrence.parse_line_datetime_with_tz(
            "DTSTART;TZID=Not/AZone:20260220T101112"
        )
        self.assertEqual(dt, datetime(2026, 2, 20, 10, 11, 12, tzinfo=timezone.utc))

        dt = core_recurrence.parse_line_datetime_with_tz(
            "DTSTART:20260220T101112",
            active_report_tzinfo=ZoneInfo("America/New_York"),
        )
        self.assertEqual(dt, datetime(2026, 2, 20, 15, 11, 12, tzinfo=timezone.utc))

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

    def test_simple_recurrence_instances_variants(self):
        not_daily = (
            "BEGIN:VEVENT\n"
            "UID:x\n"
            "DTSTART:20260220T100000Z\n"
            "RRULE:FREQ=WEEKLY;COUNT=2\n"
            "END:VEVENT\n"
        )
        self.assertIsNone(core_recurrence.simple_recurrence_instances(not_daily))

        without_count = (
            "BEGIN:VEVENT\n"
            "UID:x\n"
            "DTSTART:20260220T100000Z\n"
            "RRULE:FREQ=DAILY\n"
            "END:VEVENT\n"
        )
        self.assertIsNone(core_recurrence.simple_recurrence_instances(without_count))

        recurring_with_exdate = (
            "BEGIN:VEVENT\n"
            "UID:x\n"
            "DTSTART:20260220T100000Z\n"
            "RRULE:FREQ=DAILY;COUNT=3\n"
            "EXDATE:20260221T100000Z\n"
            "END:VEVENT\n"
        )
        instances = core_recurrence.simple_recurrence_instances(recurring_with_exdate)
        self.assertIsNotNone(instances)
        assert instances is not None
        self.assertEqual(len(instances), 2)

    def test_matches_time_range_and_alarm_fail_paths(self):
        outside_range = (
            "BEGIN:VEVENT\n"
            "UID:x\n"
            "DTSTART:20260220T100000Z\n"
            "RRULE:FREQ=DAILY;COUNT=2\n"
            "END:VEVENT\n"
        )
        self.assertFalse(
            core_recurrence.matches_time_range_recurrence(
                outside_range,
                datetime(2026, 2, 22, 0, 0, tzinfo=timezone.utc),
                datetime(2026, 2, 23, 0, 0, tzinfo=timezone.utc),
                "VEVENT",
            )
        )

        self.assertFalse(
            core_recurrence.alarm_matches_time_range(
                outside_range,
                {},
            )
        )

    def test_simple_recurrence_instances_with_overrides_and_this_and_future(self):
        text = (
            "BEGIN:VEVENT\n"
            "UID:x\n"
            "DTSTART:20260220T100000Z\n"
            "RRULE:FREQ=DAILY;COUNT=3\n"
            "END:VEVENT\n"
            "BEGIN:VEVENT\n"
            "UID:x\n"
            "RECURRENCE-ID:20260221T100000Z\n"
            "DTSTART:20260221T120000Z\n"
            "END:VEVENT\n"
            "BEGIN:VEVENT\n"
            "UID:x\n"
            "RECURRENCE-ID;RANGE=THISANDFUTURE:20260222T100000Z\n"
            "DTSTART:20260222T130000Z\n"
            "END:VEVENT\n"
        )
        instances = core_recurrence.simple_recurrence_instances(text)
        self.assertIsNotNone(instances)
        assert instances is not None
        starts = [instance[0] for instance in instances]
        self.assertEqual(starts[0], datetime(2026, 2, 20, 10, 0, tzinfo=timezone.utc))
        self.assertEqual(starts[1], datetime(2026, 2, 21, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(starts[2], datetime(2026, 2, 22, 13, 0, tzinfo=timezone.utc))

    def test_matches_time_range_recurrence_fallback_paths(self):
        class _FakeComp:
            def __init__(self, name):
                self.name = name

        class _FakeQuery:
            def __init__(self, comps):
                self._comps = comps

            def between(self, _start, _end):
                return self._comps

        text = "BEGIN:VEVENT\nUID:x\nEND:VEVENT\n"
        with (
            patch.object(
                core_recurrence, "simple_recurrence_instances", return_value=None
            ),
            patch.object(
                core_recurrence.icalendar.Calendar, "from_ical", return_value=object()
            ),
            patch.object(
                core_recurrence,
                "recurring_of",
                return_value=_FakeQuery([_FakeComp("VEVENT")]),
            ),
        ):
            self.assertTrue(
                core_recurrence.matches_time_range_recurrence(
                    text,
                    None,
                    None,
                    "VEVENT",
                )
            )

        with (
            patch.object(
                core_recurrence, "simple_recurrence_instances", return_value=None
            ),
            patch.object(
                core_recurrence.icalendar.Calendar,
                "from_ical",
                side_effect=RuntimeError("x"),
            ),
        ):
            self.assertFalse(
                core_recurrence.matches_time_range_recurrence(
                    text,
                    None,
                    None,
                    "VEVENT",
                )
            )

    def test_alarm_matches_time_range_fallback_paths(self):
        class _ParamValue:
            def __init__(self, params):
                self.params = params

        class _Alarm:
            name = "VALARM"

            def __init__(self, trigger, related, repeat=0, duration=None):
                self._trigger = trigger
                self._related = related
                self._repeat = repeat
                self._duration = duration

            def decoded(self, key, default=None):
                if key == "TRIGGER":
                    return self._trigger
                if key == "DURATION":
                    return self._duration
                return default

            def get(self, key, default=None):
                if key == "TRIGGER":
                    return _ParamValue({"RELATED": self._related})
                if key == "REPEAT":
                    return self._repeat
                return default

        class _Comp:
            name = "VEVENT"

            def __init__(self, dtstart, dtend, alarms):
                self.subcomponents = alarms
                self._dtstart = dtstart
                self._dtend = dtend

            def decoded(self, key, default=None):
                if key == "DTSTART":
                    return self._dtstart
                if key == "DTEND":
                    return self._dtend
                if key == "DUE":
                    return None
                return default

        class _FakeCal:
            def __init__(self, components):
                self._components = components

            def walk(self):
                return self._components

        class _FakeQuery:
            def __init__(self, occurrences):
                self.keep_recurrence_attributes = False
                self._occurrences = occurrences

            def between(self, _start, _end):
                return self._occurrences

        component = _Comp(
            datetime(2026, 2, 20, 10, 0, tzinfo=timezone.utc),
            datetime(2026, 2, 20, 11, 0, tzinfo=timezone.utc),
            [
                _Alarm(datetime(2026, 2, 20, 10, 5, tzinfo=timezone.utc), "START"),
                _Alarm(
                    timedelta(minutes=-10),
                    "END",
                    repeat=1,
                    duration=timedelta(minutes=5),
                ),
            ],
        )

        with (
            patch.object(
                core_recurrence, "simple_recurrence_instances", return_value=None
            ),
            patch.object(
                core_recurrence.icalendar.Calendar,
                "from_ical",
                return_value=_FakeCal([component]),
            ),
            patch.object(
                core_recurrence, "recurring_of", return_value=_FakeQuery([component])
            ),
        ):
            self.assertTrue(
                core_recurrence.alarm_matches_time_range(
                    "BEGIN:VEVENT\nUID:x\nEND:VEVENT\n",
                    {"start": "20260220T095000Z", "end": "20260220T110000Z"},
                )
            )

        with (
            patch.object(
                core_recurrence, "simple_recurrence_instances", return_value=None
            ),
            patch.object(
                core_recurrence.icalendar.Calendar,
                "from_ical",
                return_value=_FakeCal([component]),
            ),
            patch.object(
                core_recurrence, "recurring_of", side_effect=RuntimeError("x")
            ),
        ):
            self.assertFalse(
                core_recurrence.alarm_matches_time_range(
                    "BEGIN:VEVENT\nUID:x\nEND:VEVENT\n",
                    {"start": "20260220T095000Z", "end": "20260220T110000Z"},
                )
            )

    def test_additional_parse_and_simple_recurrence_paths(self):
        wrapped = "BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n"
        self.assertEqual(core_recurrence.calendar_for_component_text(wrapped), wrapped)
        self.assertIsNone(core_recurrence.parse_rrule_count("RRULE:FREQ=DAILY"))

        self.assertIsNone(
            core_recurrence.parse_line_datetime_with_tz("DTSTART:20261340")
        )
        self.assertIsNone(
            core_recurrence.parse_line_datetime_with_tz("DTSTART:20261340T101112Z")
        )
        self.assertIsNone(
            core_recurrence.parse_line_datetime_with_tz("DTSTART:20261340T101112")
        )

        self.assertFalse(
            core_recurrence.line_matches_time_range(
                "DTSTART:20260220T110000Z",
                {"start": "20260220T100000Z", "end": "20260220T110000Z"},
            )
        )

        self.assertIsNone(
            core_recurrence.simple_recurrence_instances(
                "BEGIN:VJOURNAL\nUID:no-event\nEND:VJOURNAL\n"
            )
        )

        without_base = (
            "BEGIN:VEVENT\nUID:no-base\nRRULE:FREQ=DAILY;COUNT=2\nEND:VEVENT\n"
        )
        self.assertIsNone(core_recurrence.simple_recurrence_instances(without_base))

        without_rrule = (
            "BEGIN:VEVENT\nUID:no-rrule\nDTSTART:20260220T100000Z\nEND:VEVENT\n"
        )
        self.assertIsNone(core_recurrence.simple_recurrence_instances(without_rrule))

    def test_simple_recurrence_due_exdate_override_paths(self):
        text = (
            "BEGIN:VEVENT\n"
            "UID:due-series\n"
            "DUE:20260220T100000Z\n"
            "RRULE:FREQ=DAILY;COUNT=3\n"
            "DTEND:20260220T103000Z\n"
            "EXDATE\n"
            "EXDATE:bad\n"
            "EXDATE:20260221T100000\n"
            "END:VEVENT\n"
            "BEGIN:VEVENT\n"
            "UID:due-series\n"
            "RECURRENCE-ID:bad\n"
            "DTSTART:20260220T120000Z\n"
            "END:VEVENT\n"
            "BEGIN:VEVENT\n"
            "UID:due-series\n"
            "RECURRENCE-ID:20260222T100000Z\n"
            "DUE:20260222T120000Z\n"
            "END:VEVENT\n"
            "BEGIN:VEVENT\n"
            "UID:due-series\n"
            "RECURRENCE-ID:20260221T100000Z\n"
            "END:VEVENT\n"
        )
        instances = core_recurrence.simple_recurrence_instances(text)
        self.assertIsNotNone(instances)
        assert instances is not None
        # naive EXDATE should be interpreted in UTC and excluded
        self.assertEqual(len(instances), 2)
        self.assertEqual(
            instances[0][0], datetime(2026, 2, 20, 10, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(
            instances[0][1], datetime(2026, 2, 20, 10, 30, tzinfo=timezone.utc)
        )
        self.assertEqual(
            instances[1][0], datetime(2026, 2, 22, 12, 0, tzinfo=timezone.utc)
        )

    def test_recurrence_and_alarm_additional_fallback_branches(self):
        class _Alarm:
            name = "VALARM"

            def __init__(self, trigger, related="START", repeat=0, duration=None):
                self._trigger = trigger
                self._related = related
                self._repeat = repeat
                self._duration = duration

            def decoded(self, key, default=None):
                if key == "TRIGGER":
                    return self._trigger
                if key == "DURATION":
                    return self._duration
                return default

            def get(self, key, default=None):
                if key == "TRIGGER":
                    return type("Param", (), {"params": {"RELATED": self._related}})()
                if key == "REPEAT":
                    return self._repeat
                return default

        class _Comp:
            name = "VEVENT"

            def __init__(self, dtstart, alarms):
                self._dtstart = dtstart
                self.subcomponents = alarms

            def decoded(self, key, default=None):
                if key == "DTSTART":
                    return self._dtstart
                if key == "DTEND":
                    return None
                if key == "DUE":
                    return None
                return default

        class _Cal:
            def __init__(self, components):
                self._components = components

            def walk(self):
                return self._components

        class _Query:
            def __init__(self, occurrences):
                self.keep_recurrence_attributes = False
                self._occurrences = occurrences

            def between(self, _start, _end):
                return self._occurrences

        self.assertFalse(
            core_recurrence.matches_time_range_recurrence(
                "BEGIN:VEVENT\nUID:edge\nDTSTART:20260220T100000Z\nRRULE:FREQ=DAILY;COUNT=1\nEND:VEVENT\n",
                None,
                datetime(2026, 2, 20, 10, 0, tzinfo=timezone.utc),
                "VEVENT",
            )
        )

        # Empty occurrences should fall back to calendar.walk().
        comp = _Comp(
            datetime(2026, 2, 20, 10, 0, tzinfo=timezone.utc),
            [
                _Alarm(
                    timedelta(minutes=-10),
                    related="END",
                    repeat=2,
                    duration=timedelta(minutes=5),
                )
            ],
        )
        with (
            patch.object(
                core_recurrence, "simple_recurrence_instances", return_value=None
            ),
            patch.object(
                core_recurrence.icalendar.Calendar,
                "from_ical",
                return_value=_Cal([comp]),
            ),
            patch.object(core_recurrence, "recurring_of", return_value=_Query([])),
        ):
            self.assertTrue(
                core_recurrence.alarm_matches_time_range(
                    "BEGIN:VEVENT\nUID:edge\nEND:VEVENT\n",
                    {"start": "20260220T095000Z", "end": "20260220T110500Z"},
                )
            )

        # THISANDFUTURE override without alarm sets a cutoff that excludes later alarms.
        cutoff_text = "BEGIN:VEVENT\nUID:edge\nRECURRENCE-ID;RANGE=THISANDFUTURE:20260220T100000Z\nEND:VEVENT\n"
        with (
            patch.object(
                core_recurrence, "simple_recurrence_instances", return_value=None
            ),
            patch.object(
                core_recurrence.icalendar.Calendar,
                "from_ical",
                return_value=_Cal([comp]),
            ),
            patch.object(core_recurrence, "recurring_of", return_value=_Query([comp])),
        ):
            self.assertFalse(
                core_recurrence.alarm_matches_time_range(
                    cutoff_text,
                    {"start": "20260220T090000Z", "end": "20260220T120000Z"},
                )
            )
