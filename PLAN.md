# Davhome CalDAV REPORT Plan

## Goal

Make `just caldavtester-test-suite` pass completely (including `CalDAV/reports.xml` and `CalDAV/floating.xml`) using a robust, maintainable REPORT implementation.

## Current Status

- `just caldavtester-test-suite` is now passing (`ok=224`, `ignored=15`, `failed=0`).
- REPORT/floating behavior in `CalDAV/reports.xml` and `CalDAV/floating.xml` is stabilized.
- Remaining follow-up is cleanup/refactor quality, not test correctness.

## Design Principles

- Build one canonical occurrence/evaluation model for VEVENT/VTODO/VFREEBUSY/VALARM.
- Parse once, evaluate many.
- Keep view layer thin (auth/routing/HTTP), move REPORT logic to dedicated engine module.
- Make behavior explicit and testable, especially floating timezone semantics.

## Milestones

- [x] **M1: Report Engine Boundary**
  - Extract REPORT-specific logic from `dav/views.py` into `dav/report_engine.py`.
  - Introduce request/response DTOs for `calendar-query`, `calendar-multiget`, `free-busy-query`, and `calendar-data` options.
  - Keep behavior unchanged at this milestone.

- [x] **M2: Filter Evaluator Core**
  - Implement robust `comp-filter`/`prop-filter`/`param-filter` semantics.
  - Correct `is-not-defined`, `text-match match-type`, and `test=allof|anyof` behavior.
  - Stabilize all `basic query reports` and `filtered data` expectations.

- [x] **M3: Time-Range + Occurrence Engine**
  - Build canonical occurrence expansion for VEVENT/VTODO including overrides.
  - Implement correct time-range semantics for recurring, all-day, and floating components.
  - Stabilize `time-range query reports` and `alarm time-range query reports`.

- [x] **M4: Calendar-Data Rendering**
  - Implement `calendar-data` projection (`comp`/`prop`) and `expand` output rendering.
  - Ensure output content satisfies `dataString` checks in recurrence/expand tests.

- [x] **M5: Free-Busy + Floating Finalization**
  - Finalize `free-busy-query` period aggregation and serialization.
  - Resolve floating timezone behavior across collection/calendar contexts.
  - Stabilize `floating.xml` and free-busy suites.

- [x] **M6: Full Suite Green + Cleanup**
  - [x] Run `just caldavtester-test-suite` to green.
  - [x] Remove tactical hacks and dead code from `dav/views.py`.
  - [x] Keep feature advertisement in sync with actual support.

## Execution Notes

- After each milestone, update this file and mark the milestone complete.
- Prefer small, reversible commits internally while iterating (commit only when requested).
- Validate with targeted module runs first, then full suite.
