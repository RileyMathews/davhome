from django.test import SimpleTestCase

from dav.core import payloads as core_payloads


class DavCorePayloadTests(SimpleTestCase):
    def test_validate_ical_payload(self):
        parsed, error = core_payloads.validate_ical_payload(
            b"BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:test\nEND:VEVENT\nEND:VCALENDAR\n"
        )
        self.assertIsNone(error)
        self.assertIsNotNone(parsed)
        self.assertEqual((parsed or {})["uid"], "test")

        parsed, error = core_payloads.validate_ical_payload(b"\xff")
        self.assertIsNone(parsed)
        self.assertEqual(error, "Calendar payload must be UTF-8 text.")

    def test_validate_generic_payload(self):
        parsed, error = core_payloads.validate_generic_payload(b"hello")
        self.assertIsNone(error)
        self.assertEqual((parsed or {})["text"], "hello")

    def test_if_match_and_precondition(self):
        self.assertEqual(core_payloads.if_match_values('"a", "b"'), ['"a"', '"b"'])

        request = type("Request", (), {"headers": {"If-None-Match": "*"}})()
        existing = type("Obj", (), {"etag": '"etag-1"'})()
        self.assertTrue(core_payloads.precondition_failed_for_write(request, existing))

    def test_extract_tzid_from_timezone_text(self):
        self.assertEqual(
            core_payloads.extract_tzid_from_timezone_text(
                "BEGIN:VTIMEZONE\nTZID:America/New_York\nEND:VTIMEZONE"
            ),
            "America/New_York",
        )
        self.assertIsNone(core_payloads.extract_tzid_from_timezone_text(""))

    def test_component_kind_from_payload(self):
        self.assertEqual(
            core_payloads.component_kind_from_payload("BEGIN:VEVENT\nEND:VEVENT"),
            "VEVENT",
        )
        self.assertEqual(
            core_payloads.component_kind_from_payload("BEGIN:VTODO\nEND:VTODO"),
            "VTODO",
        )
        self.assertIsNone(
            core_payloads.component_kind_from_payload(
                "BEGIN:VEVENT\nBEGIN:VTODO\nEND:VTODO\nEND:VEVENT"
            )
        )
