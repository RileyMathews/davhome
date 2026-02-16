# Davhome Plan

## Summary So Far

- Built a Django app with account registration/login and a minimal web UI.
- Implemented calendar CRUD and role-based sharing (`read`, `write`, `admin`).
- Added invitation-based sharing flow with accept/decline and pending invite visibility.
- Implemented CalDAV/WebDAV endpoints for discovery, PROPFIND, GET/HEAD, PUT, DELETE, MKCOL/MKCALENDAR, and REPORT (`calendar-query`, `calendar-multiget`).
- Added compliance tooling and recipes:
  - `litmus` integration
  - `CalDAVTester` integration (subset + full-suite overnight mode)
- Established that full `CalDAVTester --all` is diagnostic only, not a day-to-day gate.

## Current Minimal Suite

The default suite in `just caldavtester-test-suite` now includes:

- `CalDAV/current-user-principal.xml`
- `CalDAV/propfind.xml`
- `CalDAV/put.xml`
- `CalDAV/get.xml`
- `CalDAV/delete.xml`
- `CalDAV/conditional.xml`
- `CalDAV/reports.xml`
- `CalDAV/recurrenceput.xml`
- `CalDAV/floating.xml`
- `CalDAV/timezoneservice.xml`
- `CalDAV/timezonestdservice.xml`
- `CalDAV/implicittodo.xml`

## Next Task

Make sure all modules included in `just caldavtester-test-suite` are passing.
