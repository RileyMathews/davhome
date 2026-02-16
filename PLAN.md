# Davhome Plan

## Product Vision

Davhome is a family-focused calendar server with two primary surfaces:

1. A minimal web UI for account and sharing management.
2. CalDAV/WebDAV endpoints that calendar clients (mobile/desktop) can sync against.

The key user flow is:

- A user registers and signs in.
- They create one or more calendars.
- They share calendars with family members and assign permissions.
- CalDAV clients discover and sync calendars based on those permissions.

## Non-Goals (for now)

- No deployment/production configuration work yet.
- No multi-environment settings split yet.
- No `django-environ`.
- No CalDAV scheduling (RFC 6638) in MVP.
- No notifications/push subscriptions in MVP.

## Current Ground Rules

- Keep settings local-only in `config/settings.py`.
- Use SQLite during early implementation.
- Prioritize protocol correctness over UI complexity.
- Validate behavior with both:
  - `litmus` (WebDAV baseline)
  - `CalDAVTester` (CalDAV/CardDAV behavior)

## High-Level Architecture

Planned Django apps:

- `accounts`: registration/auth/profile pages.
- `calendars`: calendar metadata, sharing, and calendar object storage.
- `dav`: DAV/CalDAV endpoints and XML method handlers.

Shared service layer:

- permission checks (owner/read/write/admin)
- DAV path/resource resolution
- ETag/CTag generation/update rules

## Data Model Plan

### User

- Use Django auth user model for MVP.

### Calendar

- `id` (UUID)
- `owner` (FK -> User)
- `slug` (URL-safe identifier, unique per owner)
- `name`
- `description` (optional)
- `color` (optional)
- `timezone` (default `UTC`)
- `created_at`, `updated_at`
- soft-delete flag optional (`is_deleted`) if needed

### CalendarShare

- `calendar` (FK -> Calendar)
- `user` (FK -> User)
- `role` (`read`, `write`, `admin`)
- `accepted_at` (optional for invite acceptance flow)
- `created_at`, `updated_at`

Constraints:

- unique `(calendar, user)`

### CalendarObject

- `calendar` (FK -> Calendar)
- `uid` (iCalendar UID)
- `filename` (resource path segment, typically `*.ics`)
- `ical_blob` (raw iCalendar text)
- `etag` (strong ETag)
- `content_type` (default `text/calendar; charset=utf-8`)
- `size`
- `created_at`, `updated_at`
- soft-delete optional (`is_deleted`)

Constraints:

- unique `(calendar, uid)`
- unique `(calendar, filename)`

## Permission Model

- **owner**: full control (manage calendar, objects, shares)
- **admin**: manage share list and calendar metadata, plus write data
- **write**: read + create/update/delete calendar objects
- **read**: read-only calendar access

Enforcement points:

- web views/forms
- DAV method handlers

Use one central permission service to avoid drift.

## URL/Endpoint Plan

Web UI:

- `/` dashboard
- `/accounts/register/`, `/accounts/login/`, `/accounts/logout/`
- `/calendars/` list/create
- `/calendars/<id>/` detail/update
- `/calendars/<id>/sharing/` manage members/roles

DAV/CalDAV:

- `/.well-known/caldav` -> redirect to DAV root
- `/dav/principals/<username>/`
- `/dav/calendars/<username>/`
- `/dav/calendars/<username>/<calendar_slug>/`
- `/dav/calendars/<username>/<calendar_slug>/<filename>.ics`

## Library Plan

Planned additions to Python dependencies:

- `icalendar` for parsing/validating iCalendar payloads
- `defusedxml` for safe XML parsing
- `lxml` for robust DAV XML generation/parsing
- `pytest`
- `pytest-django`

Note: no settings/env helper library for now.

## CalDAV/WebDAV Capability Scope

### MVP DAV Methods

Implement in this order:

1. `OPTIONS`, `HEAD`, `GET`
2. `PROPFIND` (Depth `0` and `1`)
3. `REPORT` (`calendar-query`, `calendar-multiget`)
4. `PUT`, `DELETE`
5. `MKCOL`/`MKCALENDAR` if needed for client compatibility

### Required DAV Properties/Behavior for MVP

- correct `207 Multi-Status` responses
- `getetag`
- collection change tag (`getctag` extension)
- principal and home-set discovery properties
- stable hrefs and canonical path handling

### Deferred Protocol Features

- scheduling workflows (inbox/outbox, attendee messaging)
- ACL depth beyond role model requirements
- sync-collection/token optimizations (can be phase 2+)

## Testing Strategy

### Unit/Integration Tests (Django)

- permission matrix tests for each role
- calendar share CRUD behavior
- iCalendar validation and UID/filename uniqueness
- ETag regeneration on object changes
- CTag changes when collection contents mutate

### Protocol Compliance Tests

- `just litmus-test`
- `just caldavtester-test-suite`
- `just integration-test` (runs both)

Run targeted subsets first, then expand coverage.

## Phase-by-Phase Implementation Plan

### Phase 0 - Foundation and Hygiene

Deliverables:

- create apps: `accounts`, `calendars`, `dav`
- wire `INSTALLED_APPS`, base templates, static setup
- add planned dependencies
- add basic pytest config

Acceptance:

- `manage.py check` passes
- migrations apply cleanly
- test runner executes

### Phase 1 - Accounts and Basic UI Shell [DONE!]

Deliverables:

- registration/login/logout flows
- authenticated dashboard page
- nav/layout templates

Acceptance:

- user can register, sign in, sign out
- anonymous users are redirected appropriately

### Phase 2 - Calendar and Sharing Domain [DONE!]

Deliverables:

- `Calendar`, `CalendarShare`, `CalendarObject` models + migrations
- UI to create/edit/delete calendars
- UI to add/remove shares and change roles
- central permission service

Acceptance:

- owners can manage own calendars
- shared users see appropriate calendars and actions by role
- role checks enforced in UI and server-side form handling

### Phase 3 - DAV Discovery and Read Path [DONE!]

Deliverables:

- DAV URL routing and request dispatch scaffolding
- `OPTIONS`, `GET`, `HEAD`
- principal and collection `PROPFIND` (Depth 0/1)

Acceptance:

- DAV client can discover principal and calendar home
- collection/object reads return valid status, headers, and XML

### Phase 4 - DAV Write Path

Deliverables:

- `PUT` create/update of `.ics` resources
- `DELETE` resource deletion
- object parsing/validation via `icalendar`
- ETag/CTag update semantics

Acceptance:

- client can create, edit, delete events
- concurrent updates respect ETag preconditions where provided

### Phase 5 - REPORT Support and Query Semantics

Deliverables:

- `REPORT` handlers:
  - `calendar-query`
  - `calendar-multiget`
- response filtering for basic time-range/component matching

Acceptance:

- mainstream clients can sync and fetch expected objects
- targeted CalDAVTester scripts begin passing

### Phase 6 - Compliance Loop and Hardening

#### 6.1 Litmus Unblock (Core WebDAV Semantics) [DONE!]

Deliverables:

- accept generic DAV `PUT` payloads on litmus-tested paths
- return RFC-correct `409 Conflict` for missing parent on `PUT` and `MKCOL`
- implement nested `MKCOL` lifecycle semantics needed by litmus basic
- add regression tests for each corrected status/method behavior

Acceptance:

- litmus `basic` is no longer blocked at early collection/resource creation failures
- status code behavior for parent-missing and create paths is deterministic and covered by tests

Completion notes:

- litmus `basic` now passes at 100% against the current local server target
- added regression coverage for generic DAV PUT, missing-parent conflict status, nested MKCOL lifecycle, and MKCOL-with-body rejection

#### 6.2 Discovery and Header Hardening [DONE!]

Deliverables:

- normalize `OPTIONS`/`Allow`/`DAV` headers across DAV endpoints
- tighten principal/home/calendar PROPFIND behavior:
  - `current-user-principal`
  - `calendar-home-set`
  - canonical href formatting
- ensure consistent `207 Multi-Status` + `propstat` structure

Acceptance:

- targeted CalDAVTester discovery/propfind suites show measurable pass-rate improvement
- depth `0/1` discovery contracts are covered by regression tests

Completion notes:

- added DAV alias routes for principal and calendar trees (`users` and `__uids__` forms)
- updated `current-user-principal` and `calendar-home-set` href behavior to align with CalDAVTester expectations
- implemented proper PROPFIND depth handling with finite-depth error responses
- targeted CalDAVTester suites now pass:
  - `CalDAV/current-user-principal.xml` (6/6)
  - `CalDAV/propfind.xml` (33/33)

#### 6.3 REPORT Interoperability Hardening

Deliverables:

- improve `calendar-multiget` and `calendar-query` edge handling:
  - mixed valid/missing hrefs
  - unsupported report types
  - deterministic response ordering
- tighten `calendar-data` behavior under requested-prop sets
- add regression tests from observed report failures

Acceptance:

- report-focused CalDAVTester subset improves and stays stable across reruns
- REPORT edge cases are covered by Django tests

#### 6.4 Write-Path Compatibility Polish

Deliverables:

- relax over-strict iCalendar rejection where standards allow
- re-verify `If-Match` / `If-None-Match` semantics for create/update/delete
- ensure ETag/Last-Modified/Content-Length consistency after writes
- add UTF-8/special-path regression tests relevant to litmus

Acceptance:

- write-focused litmus/CalDAVTester failures drop and shift toward advanced/deferred areas
- preconditions and metadata semantics are test-covered and stable

#### 6.5 Integration Closure and Failure Classification

Deliverables:

- run gated subset for stability confirmation:
  - litmus basic
  - CalDAVTester subset (`current-user-principal`, `propfind`, `reports`, `put`, `get`)
- run full CalDAVTester `--all` once for classification
- classify remaining failures into:
  - fixed in Phase 6
  - deferred (scheduling, CardDAV, advanced ACL, etc.)

Acceptance:

- reproducible baseline for MVP-scope compliance exists
- remaining failures are explicitly categorized (no unknown bucket)

#### 6.x Test Execution Policy (Phase 6)

- fast loop (default): litmus basic + CalDAVTester targeted subset
- full loop (boundary checks): full CalDAVTester `--all`
- keep fixture setup separate from suite runs to avoid SQLite lock contention when server is live

### Phase 7 - Post-MVP Enhancements (Optional)

Candidates:

- invitation acceptance UX for shares
- calendar import/export UI
- per-user preferences (default timezone, colors)
- scheduling extensions and richer ACL support
- production settings split and deployment hardening

## Risks and Mitigations

- **Risk:** protocol edge cases are subtle and client-specific.
  - **Mitigation:** strict compliance loop with both test suites plus client smoke tests.
- **Risk:** permission divergence between UI and DAV handlers.
  - **Mitigation:** single permission service shared by both layers.
- **Risk:** performance issues from storing full blobs in SQLite.
  - **Mitigation:** acceptable for MVP; revisit storage/indexing post-MVP.

## Definition of MVP Complete

MVP is complete when:

- users can register/login and manage calendars in web UI
- calendars can be shared with role-based access
- CalDAV clients can discover, read, and write calendar data according to sharing permissions
- integration tests (`litmus` + selected `CalDAVTester`) are reproducibly runnable and passing for in-scope features
