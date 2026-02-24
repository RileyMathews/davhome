import importlib.util
import sys
import types
from pathlib import Path

from django.test import SimpleTestCase


class DavModuleShimsTests(SimpleTestCase):
    def test_report_engine_module_executes_with_stub_reports_module(self):
        module_path = Path(__file__).resolve().parents[1] / "report_engine.py"
        spec = importlib.util.spec_from_file_location(
            "dav.report_engine_cov", module_path
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)

        fake_reports = types.ModuleType("dav.reports")
        fake_engine = types.SimpleNamespace(
            parse_report_request=lambda payload: payload
        )
        fake_reports.engine = fake_engine

        previous_reports = sys.modules.get("dav.reports")
        try:
            sys.modules["dav.reports"] = fake_reports
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        finally:
            if previous_reports is None:
                sys.modules.pop("dav.reports", None)
            else:
                sys.modules["dav.reports"] = previous_reports

        self.assertIn("parse_report_request", module.__all__)
        self.assertTrue(callable(module.parse_report_request))
