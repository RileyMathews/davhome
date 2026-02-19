# DAV Functional Core / Imperative Shell RFC

## Status

- Draft for ticket `#102`
- Owner: engineering
- Scope: `dav` app refactor architecture only (no protocol behavior changes)

## Problem Statement

`dav/views.py` currently mixes multiple concerns in the same functions:

- HTTP concerns (request parsing, status/header creation, Django response assembly)
- data access concerns (ORM lookups, permission-scoped object retrieval)
- protocol/business concerns (CalDAV filtering, recurrence logic, report selection)

This increases branch-heavy integration tests and makes it harder to achieve high-confidence coverage with fast tests.

## Goals

1. Establish a functional-core / imperative-shell architecture for DAV behavior.
2. Use dataclasses (not raw dicts) as the contracts at core boundaries.
3. Keep protocol behavior equivalent while migration is in progress.
4. Increase testability by moving branch-heavy logic to pure modules.
5. Keep endpoint integration tests focused on wiring/auth/permissions/HTTP semantics.

## Non-Goals

- No behavior changes to DAV/CalDAV semantics during migration.
- No changes to vendored CalDAVTester resources.
- No big-bang rewrite of all handlers in one patch.
- No new mandatory lint/format tooling.

## Architectural Principles

1. **Pure core modules**
   - Inputs/outputs are dataclasses and primitives.
   - No Django imports (`HttpRequest`, `HttpResponse`), no ORM access.
   - Deterministic behavior with no side effects.

2. **Imperative shell modules**
   - Handle Django requests/responses, ORM reads/writes, auth, logging.
   - Translate shell objects to/from dataclass contracts.

3. **Compatibility-first migration**
   - Keep existing call points in `dav/views.py` as wrappers while code moves.
   - Migrate in slices with behavior parity checks after each slice.

4. **Dataclass-first contracts**
   - Prefer `@dataclass(frozen=True, slots=True)` by default.
   - Use mutable dataclasses only where staged mutation is clearly required.

## Proposed Module Boundaries

### Core

- `dav/core/contracts.py`
  - Dataclass contracts and protocol error enums/constants.
- `dav/core/time.py`
  - iCal datetime/duration parsing and formatting helpers.
- `dav/core/filters.py`
  - CalDAV comp/prop/param filtering logic.
- `dav/core/recurrence.py`
  - Recurrence expansion decisions and alarm time-window matching.
- `dav/core/report.py`
  - REPORT request parsing and selection decisions.

### Shell

- `dav/shell/http.py`
  - Request decoding, response construction, header/status mapping.
- `dav/shell/repository.py`
  - ORM-backed retrieval/persistence for calendar and object data.

### Existing Entry Points (remain)

- `dav/views.py`
  - Orchestrates shell adapters + core services.
  - Temporary compatibility wrappers retained during migration.

## Dataclass Contract Catalog (Initial)

These are target contracts for the first migration slices.

- `TimeRange`
  - `start: datetime | None`
  - `end: datetime | None`

- `ProtocolError`
  - `code: str` (e.g. `"valid-timezone"`, `"valid-sync-token"`)
  - `http_status: int = 403`
  - `namespace: str = "caldav" | "dav"` (or equivalent explicit enum)

- `CalendarObjectData`
  - `calendar_id: str`
  - `owner_username: str`
  - `slug: str`
  - `filename: str`
  - `etag: str`
  - `content_type: str`
  - `ical_blob: str`
  - `last_modified: datetime | None`

- `WritePrecondition`
  - `if_match: tuple[str, ...]`
  - `if_none_match: str | None`
  - `existing_etag: str | None`

- `WriteDecision`
  - `allowed: bool`
  - `error: ProtocolError | None`

- `ReportRequest`
  - `report_name: str`
  - `requested_props: tuple[str, ...]`
  - `calendar_data_request: ET.Element | None`
  - `hrefs: tuple[str, ...]`
  - `query_filter: ET.Element | None`
  - `time_range: TimeRange | None`

- `ReportResult`
  - `responses: tuple[ET.Element, ...]`
  - `sync_token: str | None`
  - `error: ProtocolError | None`

Notes:

- Where XML `Element` values are present in contracts, they remain read-only inputs.
- Timezone normalization rules are centralized in `dav/core/time.py`.

## Migration Map (Function Clusters)

The following helper clusters are primary extraction targets from `dav/views.py`:

1. **Time primitives**
   - `_parse_ical_datetime`, `_parse_ical_duration`, `_format_ical_duration`, `_as_utc_datetime`

2. **Filter primitives**
   - `_text_match`, `_combine_filter_results`, `_matches_param_filter`, `_matches_prop_filter`, `_matches_comp_filter`

3. **Recurrence/alarm logic**
   - `_simple_recurrence_instances`, `_matches_time_range_recurrence`, `_alarm_matches_time_range`

4. **Report selection**
   - report request parsing and multiget/query decision logic

5. **Shell-only orchestration**
   - `_handle_report` and response assembly helpers remain orchestration points.

## Sequence of Work (Ticket-Aligned)

1. `#102` RFC and boundaries (this document)
2. `#103` dataclass contracts module
3. `#104` shell adapters for HTTP and repository mapping
4. `#105`, `#106`, `#107`, `#108`, `#110` extraction slices in parallel where safe
5. `#109` rewire report handlers to orchestration-only shell
6. `#111` rebalance tests toward pure-core coverage
7. `#112` full regression/protocol safety validation
8. `#113` coverage ratchet and workflow docs

## Behavior Preservation Rules

1. Existing status codes and DAV/CalDAV error body semantics must not change.
2. Existing XML response structure and key header behavior (`ETag`, DAV headers, etc.) must remain stable.
3. Existing route behavior in `dav/urls.py` remains unchanged.
4. Vendored protocol suites are not modified.

## Verification Strategy

For each migration slice:

- run focused DAV tests for affected behavior
- run broader DAV suite after extraction merge
- run Django system checks with dev settings

Command set:

- `uv run python manage.py test --settings=config.settings_test dav.tests`
- `uv run python manage.py check --settings=config.settings_dev`
- optional targeted subset during development:
  - `uv run python manage.py test --settings=config.settings_test dav.tests.DavPureFunctionTests`

## Rollback Strategy

- Keep compatibility wrappers in `dav/views.py` until each extracted module is proven equivalent.
- If a slice regresses behavior, revert only that slice and keep prior extracted modules intact.
- Avoid combined multi-area refactors in a single patch.

## Risks and Mitigations

1. **Risk:** Hidden coupling between helper functions and ORM/request context.
   - **Mitigation:** Introduce explicit adapter boundaries before moving logic.

2. **Risk:** Accidental protocol behavior drift.
   - **Mitigation:** Keep parity tests and preserve wrapper signatures initially.

3. **Risk:** Dataclass churn during early design.
   - **Mitigation:** Start with minimal fields and evolve contracts slice-by-slice.

4. **Risk:** Over-abstraction.
   - **Mitigation:** Extract only branch-heavy or reused logic; keep straightforward wiring in shell.

## Ticket #102 Acceptance Checklist

- [x] Boundaries between pure core and imperative shell defined.
- [x] Dataclass-first contract strategy documented.
- [x] Function/module migration map documented.
- [x] Anti-goals and behavior-preservation constraints documented.
- [x] Slice-based rollout + verification + rollback strategy documented.
