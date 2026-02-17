# RFC 6578 Sync Support Plan (Simple v1)

## Goal

Add basic RFC 6578 collection synchronization support for calendar collections so clients can fetch incremental changes via `DAV:sync-collection`.

Scope for v1:
- Support sync on calendar collection URLs only:
  - `/dav/calendars/<username>/<slug>/`
  - `/dav/calendars/users/<username>/<slug>/`
  - `/dav/calendars/__uids__/<guid>/<slug>/`
- Do **not** support sync on calendar-home URLs in v1.
- Keep implementation minimal, predictable, and test-covered.

## Current State

- REPORT support exists for:
  - `C:calendar-query`
  - `C:calendar-multiget`
  - `C:free-busy-query`
- `supported-report-set` does not include `D:sync-collection`.
- No `D:sync-token` property is exposed in calendar collection PROPFIND responses.
- No persistent change log exists for tracking deletions/updates across sync cycles.

## Implementation Plan

### 1) Add persistent change log model

Add a new model in `calendars/models.py` to track per-calendar revisions:

- Model: `CalendarObjectChange`
- Fields:
  - `calendar` (FK to `Calendar`)
  - `revision` (positive integer, monotonic per calendar)
  - `filename` (resource path segment)
  - `uid` (nullable for non-ical/generic resources if needed)
  - `is_deleted` (bool)
  - `created_at` (auto timestamp)

Constraints/indexes:
- Unique `(calendar, revision)`
- Indexed `(calendar, revision)`
- Optional index `(calendar, filename)` for lookup/debugging

Create migration in `calendars/migrations/`.

### 2) Define sync token format + helpers

In `dav/views.py`, add helpers to:

- Build token from `(calendar_id, revision)`
- Parse/validate inbound token
- Return RFC-appropriate error for invalid token (`valid-sync-token`)

Recommended opaque format:
- `http://davhome/sync/<calendar_uuid>/<revision>`

Behavior:
- Token must belong to the same calendar being queried.
- Unknown/malformed token => 403 with DAV error body containing `valid-sync-token`.

### 3) Record revisions during writes/deletes

Update write paths in `calendar_object_view`:

- On `PUT` create/update:
  - persist object change row with incremented revision and `is_deleted=False`
- On `DELETE`:
  - persist object change row with incremented revision and `is_deleted=True`
- For collection resources created/deleted via `MKCOL`/`MKCALENDAR` and delete path:
  - also record change rows

Implementation detail:
- Use atomic transaction and lock calendar row to ensure monotonic per-calendar revision under concurrency.

### 4) Advertise sync capability

Update calendar collection discovery in `dav/views.py`:

- Add `D:sync-token` to calendar collection PROPFIND property map (`_build_prop_map_for_calendar_collection`).
  - Value should reflect latest known revision token for that calendar.
- Extend `_supported_report_set_prop` to include:
  - `D:sync-collection`

Keep existing reports unchanged.

### 5) Implement `REPORT D:sync-collection`

In `_handle_report` (`dav/views.py`):

- Add branch for `root.tag == qname(NS_DAV, "sync-collection")`
- Parse request:
  - `D:sync-token` (optional; absent means initial sync)
  - `D:sync-level` (accept `"1"` for v1)
  - `D:limit` (optional; best effort if present)
  - requested `<D:prop>` set
- Response semantics:
  - Initial sync (no token): return all current members as 200 propstats
  - Incremental sync: return changed members since token revision
  - Deleted members: return `response_with_status(href, "404 Not Found")`
  - Always include new `<D:sync-token>` in multistatus response
- Href style must match request path style (`username` / `users` / `__uids__`).

### 6) Keep calendar-home sync unsupported in v1

If `sync-collection` REPORT is sent to calendar-home endpoint:

- Return non-support response (recommended: 501 in current app style)
- Document as intentional v1 limitation.

### 7) Add tests in `dav/tests.py`

Add a new test class (or extend `DavReportTests`) to cover:

1. Discovery:
   - `supported-report-set` includes `sync-collection`
   - calendar collection PROPFIND includes `sync-token`
2. Initial sync:
   - no token returns all objects and emits `sync-token`
3. Incremental sync:
   - after PUT create/update, sync from prior token returns changed item
4. Delete sync:
   - delete after token returns 404 response entry for deleted href
5. Invalid token:
   - malformed/wrong-calendar token returns proper DAV error (`valid-sync-token`)
6. Href style:
   - users/uids paths produce matching href style in sync responses

### 8) CalDAVTester suite integration in `justfile`

After implementation, update `caldavtester-test-suite` in `justfile` to include:

- `CalDAV/sync-report.xml`

This ensures RFC 6578 behavior is continuously validated in the default implementation loop.

To keep tests aligned with v1 scope:
- Enable `sync-report`
- Keep `sync-report-home` disabled for now
- Keep `sync-report-limit` disabled for now

If `sync-report-home` remains advertised in CalDAVTester server feature config, home-level sync tests in `sync-report.xml` will be expected and can fail despite correct calendar-collection sync behavior.

## Suggested Order of Work

1. Add `CalendarObjectChange` model + migration
2. Add token encode/decode helpers
3. Add change logging in write/delete flows
4. Add discovery changes (`sync-token`, `sync-collection` report advertisement)
5. Add REPORT handler for `sync-collection`
6. Add/adjust tests
7. Update `justfile` test suite to include `CalDAV/sync-report.xml`
8. Run test suite and fix edge cases

## Out of Scope (v1)

- Calendar-home level sync token semantics
- Sharing-specific sync behavior
- Trash/recovery sync semantics
- Pagination/partial sync beyond minimal `limit` handling
- Historical token retention policies beyond basic DB persistence
- Cross-calendar aggregate sync

## Acceptance Criteria

- Calendar collection PROPFIND returns `D:sync-token`
- Calendar collection `supported-report-set` contains `D:sync-collection`
- `REPORT D:sync-collection` works for initial + incremental sync
- Deleted resources are represented as 404 responses in sync report
- Invalid tokens return RFC-compatible sync-token error response
- New Django tests pass for sync behavior
- `CalDAV/sync-report.xml` is included in the default `just caldavtester-test-suite` command (with feature flags aligned to v1 scope)
