from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from django.test import SimpleTestCase

from dav.core import freebusy as core_freebusy
from dav.core import time as core_time


class _FakeQuery:
    def __init__(self, components):
        self.keep_recurrence_attributes = False
        self._components = components

    def between(self, _start, _end):
        return self._components


class _FakeComponent:
    name = "VEVENT"

    def __init__(self, props, decoded_props):
        self._props = props
        self._decoded_props = decoded_props

    def get(self, key, default=None):
        return self._props.get(key, default)

    def decoded(self, key, default=None):
        return self._decoded_props.get(key, default)


class _FakeNonEventComponent:
    name = "VTODO"

    def get(self, _key, default=None):
        return default

    def decoded(self, _key, default=None):
        return default


class _FakeFreebusyProp:
    def __init__(self, fbtype, value_text):
        self.params = {"FBTYPE": fbtype}
        self._value_text = value_text

    def to_ical(self):
        return f"FREEBUSY:{self._value_text}".encode("utf-8")


class _FakeVFreebusy:
    name = "VFREEBUSY"

    def __init__(self, props):
        self._props = props

    def getall(self, key):
        if key == "FREEBUSY":
            return self._props
        return []


class _FakeCalendar:
    def __init__(self, walk_components):
        self._walk_components = walk_components

    def walk(self):
        return self._walk_components


class DavCoreFreebusyTests(SimpleTestCase):
    def test_parse_freebusy_value_invalid_inputs(self):
        self.assertIsNone(
            core_freebusy.parse_freebusy_value(
                "20260220T100000Z",
                core_time.parse_ical_datetime,
                core_time.parse_ical_duration,
                core_time.as_utc_datetime,
            )
        )
        self.assertIsNone(
            core_freebusy.parse_freebusy_value(
                "bad/20260220T110000Z",
                core_time.parse_ical_datetime,
                core_time.parse_ical_duration,
                core_time.as_utc_datetime,
            )
        )
        parsed = core_freebusy.parse_freebusy_value(
            "20260220T100000Z/PBAD",
            core_time.parse_ical_datetime,
            core_time.parse_ical_duration,
            core_time.as_utc_datetime,
        )
        self.assertEqual(
            parsed,
            (
                datetime(2026, 2, 20, 10, 0, tzinfo=timezone.utc),
                datetime(2026, 2, 20, 10, 0, tzinfo=timezone.utc),
            ),
        )

    def test_parse_freebusy_value_absolute_end(self):
        parsed = core_freebusy.parse_freebusy_value(
            "20260220T100000Z/20260220T120000Z",
            core_time.parse_ical_datetime,
            core_time.parse_ical_duration,
            core_time.as_utc_datetime,
        )
        self.assertEqual(
            parsed,
            (
                datetime(2026, 2, 20, 10, 0, tzinfo=timezone.utc),
                datetime(2026, 2, 20, 12, 0, tzinfo=timezone.utc),
            ),
        )

    def test_parse_freebusy_value_duration_and_end_parse_failures(self):
        self.assertIsNone(
            core_freebusy.parse_freebusy_value(
                "20260220T100000Z/PT1H",
                core_time.parse_ical_datetime,
                lambda _value: None,
                core_time.as_utc_datetime,
            )
        )
        self.assertIsNone(
            core_freebusy.parse_freebusy_value(
                "20260220T100000Z/not-a-time",
                core_time.parse_ical_datetime,
                core_time.parse_ical_duration,
                core_time.as_utc_datetime,
            )
        )

    def test_merge_intervals_non_overlapping(self):
        self.assertEqual(core_freebusy.merge_intervals([]), [])
        merged = core_freebusy.merge_intervals(
            [
                (
                    datetime(2026, 2, 20, 10, 0, tzinfo=timezone.utc),
                    datetime(2026, 2, 20, 11, 0, tzinfo=timezone.utc),
                ),
                (
                    datetime(2026, 2, 20, 12, 0, tzinfo=timezone.utc),
                    datetime(2026, 2, 20, 13, 0, tzinfo=timezone.utc),
                ),
            ]
        )
        self.assertEqual(len(merged), 2)

    def test_freebusy_intervals_for_object_covers_vfreebusy_and_events(self):
        window_start = datetime(2026, 2, 20, 9, 0, tzinfo=timezone.utc)
        window_end = datetime(2026, 2, 20, 18, 0, tzinfo=timezone.utc)

        ical_blob = "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"

        components = [
            _FakeComponent(
                {"STATUS": "CANCELLED", "TRANSP": "OPAQUE"},
                {
                    "DTSTART": datetime(2026, 2, 20, 10, 0, tzinfo=timezone.utc),
                    "DTEND": datetime(2026, 2, 20, 11, 0, tzinfo=timezone.utc),
                },
            ),
            _FakeComponent(
                {"STATUS": "", "TRANSP": "TRANSPARENT"},
                {
                    "DTSTART": datetime(2026, 2, 20, 10, 0, tzinfo=timezone.utc),
                    "DTEND": datetime(2026, 2, 20, 11, 0, tzinfo=timezone.utc),
                },
            ),
            _FakeComponent(
                {"STATUS": "TENTATIVE", "TRANSP": "OPAQUE"},
                {
                    "DTSTART": datetime(2026, 2, 20, 13, 0, tzinfo=timezone.utc),
                    "DTEND": datetime(2026, 2, 20, 14, 0, tzinfo=timezone.utc),
                },
            ),
            _FakeComponent(
                {"STATUS": "UNAVAILABLE", "TRANSP": "OPAQUE"},
                {
                    "DTSTART": datetime(2026, 2, 20, 14, 0, tzinfo=timezone.utc),
                    "DTEND": datetime(2026, 2, 20, 15, 0, tzinfo=timezone.utc),
                },
            ),
            _FakeComponent(
                {"STATUS": "", "TRANSP": "OPAQUE"},
                {
                    "DTSTART": datetime(2026, 2, 20, 15, 0, tzinfo=timezone.utc),
                    "DTEND": None,
                    "DURATION": timedelta(hours=1),
                },
            ),
            _FakeComponent(
                {"STATUS": "", "TRANSP": "OPAQUE"},
                {
                    "DTSTART": date(2026, 2, 20),
                    "DTEND": None,
                    "DURATION": None,
                },
            ),
            _FakeComponent(
                {"STATUS": "", "TRANSP": "OPAQUE"},
                {
                    "DTSTART": datetime(2026, 2, 20, 16, 0, tzinfo=timezone.utc),
                    "DTEND": None,
                    "DURATION": None,
                },
            ),
        ]

        fake_calendar = _FakeCalendar(
            [
                _FakeVFreebusy(
                    [
                        _FakeFreebusyProp("BUSY", "20260220T100000Z/20260220T110000Z"),
                        _FakeFreebusyProp(
                            "BUSY-TENTATIVE",
                            "20260220T110000Z/20260220T120000Z",
                        ),
                        _FakeFreebusyProp(
                            "BUSY-UNAVAILABLE",
                            "20260220T120000Z/20260220T130000Z",
                        ),
                        _FakeFreebusyProp("BUSY", "bad"),
                    ]
                )
            ]
        )

        with (
            patch.object(
                core_freebusy.icalendar.Calendar,
                "from_ical",
                return_value=fake_calendar,
            ),
            patch.object(
                core_freebusy, "recurring_of", lambda _cal: _FakeQuery(components)
            ),
        ):
            busy, tentative, unavailable = core_freebusy.freebusy_intervals_for_object(
                ical_blob,
                window_start,
                window_end,
                timezone.utc,
                lambda value: core_freebusy.parse_freebusy_value(
                    value,
                    core_time.parse_ical_datetime,
                    core_time.parse_ical_duration,
                    core_time.as_utc_datetime,
                ),
                core_time.as_utc_datetime,
            )

        self.assertGreaterEqual(len(busy), 3)
        self.assertGreaterEqual(len(tentative), 2)
        self.assertGreaterEqual(len(unavailable), 2)

    def test_freebusy_intervals_for_object_invalid_calendar_and_query_error(self):
        window_start = datetime(2026, 2, 20, 9, 0, tzinfo=timezone.utc)
        window_end = datetime(2026, 2, 20, 18, 0, tzinfo=timezone.utc)

        busy, tentative, unavailable = core_freebusy.freebusy_intervals_for_object(
            "not-ical",
            window_start,
            window_end,
            timezone.utc,
            lambda value: value,
            lambda value, _tz=None: value,
        )
        self.assertEqual((busy, tentative, unavailable), ([], [], []))

    def test_freebusy_intervals_for_object_additional_guard_paths(self):
        window_start = datetime(2026, 2, 20, 9, 0, tzinfo=timezone.utc)
        window_end = datetime(2026, 2, 20, 18, 0, tzinfo=timezone.utc)
        ical_blob = "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"

        fake_calendar = _FakeCalendar(
            [
                _FakeVFreebusy(
                    [
                        _FakeFreebusyProp("BUSY", "20260220T010000Z/20260220T020000Z"),
                    ]
                )
            ]
        )
        components = [
            _FakeNonEventComponent(),
            _FakeComponent(
                {"STATUS": "", "TRANSP": "OPAQUE"},
                {
                    "DTSTART": None,
                    "DTEND": None,
                },
            ),
            _FakeComponent(
                {"STATUS": "", "TRANSP": "OPAQUE"},
                {
                    "DTSTART": datetime(2026, 2, 21, 10, 0, tzinfo=timezone.utc),
                    "DTEND": datetime(2026, 2, 21, 11, 0, tzinfo=timezone.utc),
                },
            ),
        ]

        with (
            patch.object(
                core_freebusy.icalendar.Calendar,
                "from_ical",
                return_value=fake_calendar,
            ),
            patch.object(
                core_freebusy, "recurring_of", lambda _cal: _FakeQuery(components)
            ),
        ):
            busy, tentative, unavailable = core_freebusy.freebusy_intervals_for_object(
                ical_blob,
                window_start,
                window_end,
                timezone.utc,
                lambda value: core_freebusy.parse_freebusy_value(
                    value,
                    core_time.parse_ical_datetime,
                    core_time.parse_ical_duration,
                    core_time.as_utc_datetime,
                ),
                core_time.as_utc_datetime,
            )

        self.assertEqual((busy, tentative, unavailable), ([], [], []))

        ical_blob = "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"
        original_recurring_of = core_freebusy.recurring_of
        core_freebusy.recurring_of = lambda _cal: (_ for _ in ()).throw(
            RuntimeError("x")
        )
        try:
            busy, tentative, unavailable = core_freebusy.freebusy_intervals_for_object(
                ical_blob,
                window_start,
                window_end,
                timezone.utc,
                lambda value: None,
                core_time.as_utc_datetime,
            )
        finally:
            core_freebusy.recurring_of = original_recurring_of

        self.assertEqual((busy, tentative, unavailable), ([], [], []))
