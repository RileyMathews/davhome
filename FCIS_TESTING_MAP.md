# FCIS Testing Map

This document tracks how endpoint-heavy DAV behavior is covered by pure-core tests.

## Mapping

- REPORT request type classification
  - Core: `dav/test_core_report.py`
  - Shell/wiring: `dav/tests.py` REPORT endpoint cases

- REPORT time-range validation and boundary checks
  - Core: `dav/test_core_report.py`
  - Shell/wiring: `dav/tests.py` invalid REPORT payload status checks

- CalDAV text/prop/param filter evaluation
  - Core: `dav/test_core_filters.py`
  - Shell/wiring: `dav/tests.py` calendar-query response behavior

- iCal date/duration parsing and UTC conversions
  - Core: `dav/test_core_time.py`
  - Shell/wiring: `dav/tests.py` report/query scenarios indirectly using these helpers

- Recurrence and VALARM time window logic
  - Core: `dav/test_core_recurrence.py`
  - Shell/wiring: `dav/tests.py` freebusy/query integration behavior

- Repository and HTTP adapter boundaries
  - Core-ish shell tests: `dav/test_shell_adapters.py`
  - Endpoint coverage: `dav/tests.py`

## Notes

- Pure-core tests should be preferred for branch-heavy logic.
- Endpoint tests should stay focused on integration semantics (auth, routing, DB persistence, HTTP responses).
