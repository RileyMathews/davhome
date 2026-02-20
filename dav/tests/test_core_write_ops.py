from django.test import SimpleTestCase

import dav.core.write_ops as core_write_ops


class DavCoreWriteOpsTests(SimpleTestCase):
    def test_precondition_if_none_match_blocks_existing(self):
        precondition = core_write_ops.build_write_precondition(
            if_match_header=None,
            if_none_match_header="*",
            existing_etag='"etag"',
            parse_if_match_values=lambda _: (),
        )
        decision = core_write_ops.decide_precondition(precondition)
        self.assertFalse(decision.allowed)
        if decision.error is None:
            self.fail("Expected precondition error")
        self.assertEqual(decision.error.http_status, 412)

    def test_precondition_if_match_requires_existing_match(self):
        precondition = core_write_ops.build_write_precondition(
            if_match_header='"old"',
            if_none_match_header=None,
            existing_etag='"new"',
            parse_if_match_values=lambda header: [header],
        )
        decision = core_write_ops.decide_precondition(precondition)
        self.assertFalse(decision.allowed)
        if decision.error is None:
            self.fail("Expected precondition error")
        self.assertEqual(decision.error.http_status, 412)

    def test_precondition_allows_matching_if_match(self):
        precondition = core_write_ops.build_write_precondition(
            if_match_header='"etag"',
            if_none_match_header=None,
            existing_etag='"etag"',
            parse_if_match_values=lambda header: [header],
        )
        decision = core_write_ops.decide_precondition(precondition)
        self.assertTrue(decision.allowed)
        self.assertIsNone(decision.error)

    def test_build_payload_plan(self):
        plan = core_write_ops.build_payload_validation_plan(
            filename="event.ics",
            raw_content_type="text/calendar; charset=utf-8",
            normalize_content_type=lambda value: value.split(";", 1)[0].strip(),
            is_ical_resource=lambda filename, content_type: (
                filename.endswith(".ics") and content_type == "text/calendar"
            ),
        )
        self.assertEqual(plan.content_type, "text/calendar")
        self.assertTrue(plan.is_ical)

    def test_component_kind_decision(self):
        denied = core_write_ops.decide_component_kind(
            parsed_component_kind="VTODO",
            calendar_component_kind="VEVENT",
        )
        self.assertFalse(denied.allowed)
        if denied.error is None:
            self.fail("Expected component mismatch error")
        self.assertEqual(denied.error.code, "supported-calendar-component")

        allowed = core_write_ops.decide_component_kind(
            parsed_component_kind="VEVENT",
            calendar_component_kind="VEVENT",
        )
        self.assertTrue(allowed.allowed)
