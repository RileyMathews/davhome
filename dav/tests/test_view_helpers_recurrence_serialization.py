from datetime import date, datetime, timedelta, timezone

from django.test import SimpleTestCase

from dav.views.helpers import recurrence_serialization as rs


class _Comp:
    def __init__(self, name, props=None, decoded_props=None):
        self.name = name
        self._props = props or {}
        self._decoded_props = decoded_props or {}

    def get(self, key, default=None):
        return self._props.get(key, default)

    def decoded(self, key, default=None):
        return self._decoded_props.get(key, default)


class RecurrenceSerializationTests(SimpleTestCase):
    def test_append_date_or_datetime_line(self):
        lines = []
        rs._append_date_or_datetime_line(lines, "RECURRENCE-ID", "20260220", True)
        rs._append_date_or_datetime_line(
            lines, "RECURRENCE-ID", "20260220T100000Z", False
        )
        self.assertEqual(lines[0], "RECURRENCE-ID;VALUE=DATE:20260220")
        self.assertEqual(lines[1], "RECURRENCE-ID:20260220T100000Z")

    def test_uid_drop_recurrence_map(self):
        expanded = [
            _Comp("VEVENT", {"UID": "has-master"}, {"RECURRENCE-ID": None}),
            _Comp(
                "VEVENT",
                {"UID": "drop-me"},
                {"RECURRENCE-ID": datetime(2026, 2, 20, 10, 0, tzinfo=timezone.utc)},
            ),
            _Comp(
                "VEVENT",
                {"UID": "drop-me"},
                {"RECURRENCE-ID": datetime(2026, 2, 20, 11, 0, tzinfo=timezone.utc)},
            ),
            _Comp(
                "VEVENT",
                {"UID": "single"},
                {"RECURRENCE-ID": datetime(2026, 2, 20, 12, 0, tzinfo=timezone.utc)},
            ),
            _Comp("VEVENT", {}, {"RECURRENCE-ID": None}),
        ]

        mapping = rs._uid_drop_recurrence_map(expanded, None)
        self.assertEqual(mapping, {"drop-me": "20260220T100000Z"})

    def test_resolved_recurrence_text_paths(self):
        component = _Comp("VEVENT", {}, {"RECURRENCE-ID": None})
        rec_text, rec_is_date = rs._resolved_recurrence_text(
            component,
            "u1",
            None,
            "20260220T100000Z",
            False,
            {"u1": datetime(2026, 2, 19, 10, 0, tzinfo=timezone.utc)},
            set(),
            {},
        )
        self.assertEqual((rec_text, rec_is_date), ("20260220T100000Z", False))

        component = _Comp("VEVENT", {"RRULE": "FREQ=DAILY", "EXDATE": "x"}, {})
        rec_text, _ = rs._resolved_recurrence_text(
            component,
            "u2",
            None,
            "20260220T100000Z",
            False,
            None,
            set(),
            {},
        )
        self.assertEqual(rec_text, "20260220T100000Z")

        component = _Comp("VEVENT", {}, {})
        rec_text, _ = rs._resolved_recurrence_text(
            component,
            "u3",
            None,
            "20260220T100000Z",
            False,
            None,
            {"u3"},
            {},
        )
        self.assertEqual(rec_text, "20260220T100000Z")

        component = _Comp(
            "VEVENT",
            {},
            {"RECURRENCE-ID": datetime(2026, 2, 20, 10, 0, tzinfo=timezone.utc)},
        )
        rec_text, _ = rs._resolved_recurrence_text(
            component,
            "u4",
            None,
            "20260220T100000Z",
            False,
            None,
            None,
            {"u4": "20260220T100000Z"},
        )
        self.assertIsNone(rec_text)

    def test_serialize_expanded_components(self):
        local_tz = timezone(timedelta(hours=-6))
        expanded = [
            _Comp("VJOURNAL", {"UID": "skip"}, {}),
            _Comp(
                "VEVENT",
                {"UID": "e1", "SUMMARY": "Meeting"},
                {
                    "DTSTART": datetime(2026, 2, 20, 10, 0, tzinfo=timezone.utc),
                    "DTEND": datetime(2026, 2, 20, 11, 0, tzinfo=timezone.utc),
                    "RECURRENCE-ID": None,
                    "DUE": None,
                    "DURATION": None,
                },
            ),
            _Comp(
                "VEVENT",
                {"UID": "e2"},
                {
                    "DTSTART": date(2026, 2, 20),
                    "DTEND": None,
                    "RECURRENCE-ID": None,
                    "DUE": None,
                    "DURATION": None,
                },
            ),
            _Comp(
                "VTODO",
                {"UID": "t1", "SUMMARY": "Task"},
                {
                    "DTSTART": None,
                    "DTEND": None,
                    "RECURRENCE-ID": None,
                    "DUE": datetime(2026, 2, 20, 12, 0, tzinfo=timezone.utc),
                    "DURATION": timedelta(hours=2),
                },
            ),
        ]

        rendered = rs._serialize_expanded_components(
            expanded,
            tzinfo=local_tz,
            master_starts={"e1": datetime(2026, 2, 19, 10, 0, tzinfo=timezone.utc)},
            first_instance_excluded_uids={"e2"},
        )

        self.assertIn("BEGIN:VCALENDAR", rendered)
        self.assertIn("BEGIN:VEVENT", rendered)
        self.assertIn("BEGIN:VTODO", rendered)
        self.assertIn("UID:e1", rendered)
        self.assertIn("SUMMARY:Meeting", rendered)
        self.assertIn("DURATION:PT1H", rendered)
        self.assertIn("RECURRENCE-ID:20260220T100000Z", rendered)
        self.assertIn("DTSTART;VALUE=DATE:20260219", rendered)
        self.assertIn("RECURRENCE-ID;VALUE=DATE:20260220", rendered)
        self.assertIn("DUE:20260220T120000Z", rendered)
        self.assertIn("SUMMARY:Task", rendered)
        self.assertTrue(rendered.endswith("\r\n"))
