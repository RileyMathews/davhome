from types import SimpleNamespace

from django.test import SimpleTestCase

import dav.core.report_dispatch as core_report_dispatch


class DavCoreReportDispatchTests(SimpleTestCase):
    def test_build_report_execution_context(self):
        parsed_report = SimpleNamespace(
            root=SimpleNamespace(tag="{DAV:}sync-collection"),
            requested_props=("{DAV:}getetag",),
            calendar_data_request=None,
        )
        context = core_report_dispatch.build_report_execution_context(
            parsed_report=parsed_report,
            calendars=["cal"],
            request_path="/dav/calendars/owner/family/",
            classify_report_kind=lambda tag: "sync-collection" if tag else "unknown",
        )

        self.assertEqual(context.calendars, ("cal",))
        self.assertEqual(context.request_path, "/dav/calendars/owner/family/")
        self.assertEqual(context.report_kind, "sync-collection")
        self.assertEqual(context.requested_props, ("{DAV:}getetag",))

    def test_dispatch_report_routes_by_kind(self):
        context = SimpleNamespace(report_kind="query")
        result = core_report_dispatch.dispatch_report(
            context=context,
            report_kind_multiget="multiget",
            report_kind_query="query",
            report_kind_freebusy="freebusy",
            report_kind_sync_collection="sync",
            handle_multiget=lambda _ctx: "m",
            handle_query=lambda _ctx: "q",
            handle_freebusy=lambda _ctx: "f",
            handle_sync_collection=lambda _ctx: "s",
            handle_unknown=lambda _ctx: "u",
        )
        self.assertEqual(result, "q")

    def test_dispatch_report_falls_back_to_unknown(self):
        context = SimpleNamespace(report_kind="other")
        result = core_report_dispatch.dispatch_report(
            context=context,
            report_kind_multiget="multiget",
            report_kind_query="query",
            report_kind_freebusy="freebusy",
            report_kind_sync_collection="sync",
            handle_multiget=lambda _ctx: "m",
            handle_query=lambda _ctx: "q",
            handle_freebusy=lambda _ctx: "f",
            handle_sync_collection=lambda _ctx: "s",
            handle_unknown=lambda _ctx: "u",
        )
        self.assertEqual(result, "u")
