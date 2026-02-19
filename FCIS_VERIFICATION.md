# FCIS Verification Log

Verification runs used during FCIS migration slices.

## Core and shell test suites

- `uv run python manage.py test --settings=config.settings_test dav.test_core_time dav.test_core_filters dav.test_core_recurrence dav.test_core_report dav.test_core_contracts dav.test_shell_adapters`
  - Result: pass

## DAV regression suite

- `uv run python manage.py test --settings=config.settings_test dav.tests dav.test_core_time dav.test_core_filters dav.test_core_recurrence dav.test_core_report dav.test_core_contracts dav.test_shell_adapters`
  - Result: pass

## Coverage gate

- `just django-test-cov`
  - Result: pass
  - Total branch coverage at run time: `70%`

## Django system checks

- `uv run python manage.py check --settings=config.settings_dev`
  - Result: pass

## Safety constraints verified

- No vendored CalDAVTester resources were modified.
- Existing DAV endpoint tests continue to pass after core extraction.
