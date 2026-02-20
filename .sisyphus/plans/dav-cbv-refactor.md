# DAV: Refactor Views To Class-Based Dispatch

## TL;DR
> Convert all `/dav/` endpoints from FBVs with large `request.method` branching to Django class-based views (CBVs) with explicit per-method handlers (including DAV verbs like `PROPFIND`, `REPORT`, `MKCALENDAR`, etc.). Do this incrementally, keep conformance green, and allow small behavior fixes when uncovered.

**Deliverables**
- `/dav/` endpoints served by CBVs with clear method routing (root, principals, calendar homes, calendar collections, calendar objects)
- Guardrail tests for: no redirects, correct `OPTIONS`/`Allow`/`DAV` headers, and custom DAV verb dispatch
- All verification gates passing: `just django-test`, `just litmus-test`, `just caldavtester-test-suite`, and the required `just full-verify`

**Estimated Effort**: XL
**Parallel Execution**: YES (5 waves)
**Critical Path**: Baseline + guardrail tests -> CBV foundation -> collections/homes -> collection objects -> calendar objects -> full verification

---

## Context

### Original Request
Refactor `dav/` views and their callstacks so route handling is less convoluted (CalDAV/WebDAV endpoints handle many HTTP methods). Prefer Django class-based views so method routing is clearer at the class method level.

### Known Hotspots / Current Layout (from repo exploration)
- URLConf: `dav/urls.py`
- Views:
  - `dav/views_collections.py` (root, principals, calendar homes)
  - `dav/views_objects.py` (calendar collections, calendar objects)
- Shared helpers:
  - `dav/views_common.py` (auth helper `_require_dav_user`, `_dav_common_headers`, XML responses, ETag helpers)
  - `dav/views_reports.py` (`_handle_report` and report helpers)
  - `dav/view_helpers/copy_move.py` (COPY/MOVE logic)
- Complexity hotspots:
  - `dav/views_objects.py` `calendar_object_view` (many methods + subtle status/header differences)
  - `dav/views_objects.py` `calendar_collection_view` (REPORT/PROPFIND depth + MKCOL->MKCALENDAR alias)
- No existing CBV usage in the repo today; introducing CBVs is a new pattern.

### Scope
IN:
- All `/dav/` endpoints migrated to CBVs, with incremental staging and frequent verification.
- Small behavior fixes allowed when uncovered, as long as conformance stays green.

OUT (guardrails):
- No DB/model changes or migrations as part of this refactor.
- No changes to vendored CalDAVTester resources (beyond explicitly planned module/feature toggles; avoid by default).

---

## Work Objectives

### Core Objective
Make DAV request routing obvious and maintainable by moving method dispatch into CBV method handlers while preserving protocol semantics (status codes, headers, XML/iCal bodies, precondition handling) and keeping all test/conformance gates green.

### Must Have
- Custom DAV verbs (`PROPFIND`, `PROPPATCH`, `REPORT`, `MKCALENDAR`, `MKCOL`, `COPY`, `MOVE`) dispatch to explicit CBV methods.
- `@csrf_exempt` semantics preserved for all DAV endpoints.
- Auth challenge semantics preserved (session/Basic via `dav/auth.py` and `_require_dav_user`).
- No redirects (esp. trailing slash / APPEND_SLASH) introduced for DAV verbs.
- `OPTIONS` responses preserve `Allow` + `DAV` headers semantics.

### Must NOT Have
- Redirect-based “canonicalization” (301/302/307/308) on DAV endpoints.
- Reformatting XML/iCalendar output in a way that breaks CalDAVTester expectations (whitespace/order/CRLF).
- Introducing new frameworks (e.g., DRF) for this refactor.

---

## Verification Strategy (MANDATORY)

**Zero human intervention**: all verification steps are agent-executed.

### Test Decision
- **Infrastructure exists**: YES
- **Automated tests**: Tests-after (add only focused regression tests needed to pin refactor-sensitive behavior)
- **Primary gate**: `just full-verify`

### Fast Iteration Commands
- `just django-test` (or `uv run python manage.py test dav --settings=config.settings_test` if repo conventions allow; prefer `just`)
- `just caldavtester-test-suite`
- `just litmus-test`

### Evidence Policy
Each task produces evidence under:
- `.sisyphus/evidence/task-{N}-{slug}.txt` (command output)
- `.sisyphus/evidence/task-{N}-{slug}.json` (captured response bodies/headers if needed)

---

## Execution Strategy

### Parallel Execution Waves

Wave 1 (Baseline + guardrails + CBV foundation)
├── Task 1: Capture baseline conformance outputs [quick]
├── Task 2: Add guardrail tests (no redirects, OPTIONS/Allow/DAV, custom verb dispatch) [deep]
├── Task 3: Introduce DAV CBV foundation (base + mixins) without rewiring endpoints yet [deep]
├── Task 4: Add shims/export strategy so URLConf remains stable during conversion [quick]
└── Task 5: LSP/usage map for high-risk endpoints before moving code (references audit) [quick]

Wave 2 (Convert low-risk endpoints)
├── Task 6: Convert `dav_root` to CBV + parity checks [unspecified-high]
├── Task 7: Convert principal endpoints to CBVs [unspecified-high]
└── Task 8: Convert shared collection-style endpoints to CBVs (principals/calendars collections) [unspecified-high]

Wave 3 (Convert homes + calendar collections)
├── Task 9: Convert calendar home endpoints to CBVs (includes REPORT delegation) [unspecified-high]
└── Task 10: Convert calendar collection endpoints to CBVs (PROPFIND/REPORT/MKCALENDAR/PROPPATCH/DELETE) [deep]

Wave 4 (Convert calendar objects: biggest hotspot)
├── Task 11: Convert calendar object endpoint (read path: OPTIONS/GET/HEAD/PROPFIND) [deep]
├── Task 12: Convert calendar object endpoint (write path: PUT/DELETE + preconditions) [ultrabrain]
├── Task 13: Convert calendar object endpoint (PROPPATCH/COPY/MOVE/MKCOL/MKCALENDAR) + conformance check [ultrabrain]
└── Task 14: Parity/edge-case pass: headers, preconditions, COPY/MOVE semantics, CRLF/iCal bodies [deep]

Wave 5 (Cleanup + integration verification)
├── Task 15: Remove dead FBV dispatch branches; keep helpers stable; ensure no redirect regressions [unspecified-high]
└── Task 16: Run required full gate and capture evidence (`just full-verify`) [quick]

Critical Path: 1 -> 2 -> 3 -> 6/7/8 -> 9/10 -> 11/12/13/14 -> 16

---

## TODOs

Note: tasks are written so multiple agents can work in parallel when files don’t overlap. If a task would touch 4+ files, split it.

- [x] 1. Capture Baseline Conformance Outputs

  **What to do**:
  - From a clean state, run these and save full outputs as evidence:
    - `just django-test`
    - `just litmus-test`
    - `just caldavtester-test-suite`
  - If runtime is acceptable, also run `just full-verify` once and save output (this becomes the “golden baseline”).

  **Must NOT do**:
  - Do not change any code or test configuration while capturing baseline.

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: (none)

  **Parallelization**:
  - **Can Run In Parallel**: YES (independent)
  - **Blocked By**: None
  - **Blocks**: Tasks 16 (final gate uses these for comparison)

  **References**:
  - `justfile` - source of truth for verification commands.
  - `dav/tests.py` - primary DAV unit coverage.
  - `caldavtester-lab/caldav-suite-modules.txt` - conformance module list.

  **Acceptance Criteria**:
  - [ ] Evidence files exist:
    - `.sisyphus/evidence/task-1-django-test.txt`
    - `.sisyphus/evidence/task-1-litmus-test.txt`
    - `.sisyphus/evidence/task-1-caldavtester.txt`
    - (optional) `.sisyphus/evidence/task-1-full-verify.txt`

  **QA Scenarios**:
  ```
  Scenario: Baseline unit/conformance snapshots captured
    Tool: Bash
    Steps:
      1. Run: just django-test
      2. Run: just litmus-test
      3. Run: just caldavtester-test-suite
    Expected Result: All exit 0; outputs saved to evidence files
    Evidence: .sisyphus/evidence/task-1-*.txt

  Scenario: Baseline full gate (optional)
    Tool: Bash
    Steps:
      1. Run: just full-verify
    Expected Result: Exit 0; output saved
    Evidence: .sisyphus/evidence/task-1-full-verify.txt
  ```

- [x] 2. Add Guardrail Tests For Refactor-Sensitive Behavior

  **What to do**:
  - Add/extend Django tests that pin the behavior most likely to regress during CBV migration:
    - **No redirects** for DAV verbs on canonical URLs (ensure no 301/308 due to slash normalization)
    - **`OPTIONS` parity**: `Allow` includes the expected DAV verbs; `DAV` header present
    - **Custom verb dispatch**: at least one endpoint returns a non-405 response for `PROPFIND`/`REPORT` when authenticated
  - Keep tests as strict as current behavior allows, but avoid overly brittle ordering assertions unless existing behavior is order-stable.

  **Must NOT do**:
  - Do not change production behavior in this task; tests only.

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: (none)

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 1 and 3)
  - **Blocked By**: None
  - **Blocks**: Tasks 6-16

  **References**:
  - `dav/tests.py` - add guardrails close to existing DAV endpoint tests.
  - `dav/urls.py` - canonical URL shapes to test.
  - `dav/views_common.py` - auth + common headers behavior.

  **Acceptance Criteria**:
  - [ ] New tests added under `dav/tests.py` (or existing core test modules) covering “no redirects” + “OPTIONS/Allow/DAV” + “custom verb dispatch”.
  - [ ] `just django-test` passes.
  - [ ] Evidence captured: `.sisyphus/evidence/task-2-django-test.txt`.

  **QA Scenarios**:
  ```
  Scenario: Guardrail tests fail before refactor regressions can land
    Tool: Bash
    Steps:
      1. Run: just django-test
    Expected Result: Exit 0; guardrail tests present and executed
    Evidence: .sisyphus/evidence/task-2-django-test.txt

  Scenario: No-redirect checks cover DAV verbs
    Tool: Django TestCase (agent executes via just)
    Steps:
      1. In tests, issue client.generic('PROPFIND', '/dav/...') on canonical URLs
      2. Assert status_code NOT IN {301, 302, 307, 308}
    Expected Result: Redirects never occur for DAV verbs
    Evidence: .sisyphus/evidence/task-2-django-test.txt
  ```

- [x] 3. Introduce DAV CBV Foundation (Base + Mixins)

  **What to do**:
  - Create a minimal CBV foundation to support DAV verbs safely (recommended location: `dav/cbv/base.py` and `dav/cbv/mixins.py`):
    - A base view that extends `django.views.View`
    - Extend `http_method_names` to include DAV verbs used in this repo (`propfind`, `proppatch`, `report`, `mkcalendar`, `mkcol`, `copy`, `move`)
    - Ensure CBV-level `csrf_exempt` parity with current DAV FBVs
    - Centralize auth challenge in `dispatch()` (using existing `dav/auth.py` / `_require_dav_user` pattern)
    - Centralize `_dav_common_headers` application so every response preserves `DAV: ...`
    - Provide an `options()` implementation that produces `Allow` consistent with existing endpoints
  - Do NOT rewire any URLs/endpoints yet; this task only introduces reusable infrastructure.

  **Must NOT do**:
  - Do not introduce DRF or new middleware.
  - Do not change URL patterns or converters.

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: (none)

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 1-2)
  - **Blocked By**: None
  - **Blocks**: Tasks 6-16

  **References**:
  - `dav/views_common.py` - `_require_dav_user`, `_dav_common_headers`, `_not_allowed` behavior to preserve.
  - `dav/auth.py` - Basic/session auth and `WWW-Authenticate` challenge behavior.
  - `dav/views_objects.py` + `dav/views_collections.py` - current allowed-method lists and `OPTIONS` patterns.

  **Acceptance Criteria**:
  - [ ] CBV foundation code exists (recommended: `dav/cbv/base.py`, `dav/cbv/mixins.py`), with explicit support for the DAV verbs used by this server.
  - [ ] `just django-test` passes.
  - [ ] Evidence captured: `.sisyphus/evidence/task-3-django-test.txt`.

  **QA Scenarios**:
  ```
  Scenario: CBV foundation imports cleanly
    Tool: Bash
    Steps:
      1. Run: just django-test
    Expected Result: Exit 0; no import errors
    Evidence: .sisyphus/evidence/task-3-django-test.txt

  Scenario: Custom verbs are recognized by dispatch
    Tool: Django TestCase (agent executes via just)
    Steps:
      1. Instantiate a CBV subclass with a propfind() handler
      2. Issue a request with method='PROPFIND'
      3. Assert handler called (status not 405)
    Expected Result: Non-standard verbs route to methods when listed in http_method_names
    Evidence: .sisyphus/evidence/task-3-django-test.txt
  ```

- [x] 4. Introduce Stable DAV Endpoint Entry-Points (Shim Layer)

  **What to do**:
  - Reduce churn during incremental conversion by creating a single “entrypoints” module that `dav/urls.py` imports from (recommended: `dav/entrypoints.py`).
  - Initially, have entrypoints call through to the existing FBVs (no behavior change).
  - Later tasks switch individual entrypoints from FBV -> CBV without touching URL patterns.

  **Must NOT do**:
  - Do not change URL patterns, converters, or ordering in `dav/urls.py`.
  - Do not introduce redirects or change trailing slash handling.

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: (none)

  **Parallelization**:
  - **Can Run In Parallel**: YES (after Tasks 1-3)
  - **Blocked By**: Task 3 (for naming/structure consistency)
  - **Blocks**: Tasks 6-16

  **References**:
  - `dav/urls.py` - must remain shape-identical; only import source changes.
  - `dav/views_collections.py`, `dav/views_objects.py` - current FBV entrypoints.

  **Acceptance Criteria**:
  - [ ] URL patterns are unchanged (same regex/path patterns), but import points to `dav/entrypoints.py`.
  - [ ] `just django-test` passes.
  - [ ] Evidence captured: `.sisyphus/evidence/task-4-django-test.txt`.

  **QA Scenarios**:
  ```
  Scenario: URLConf rewiring is behavior-neutral
    Tool: Bash
    Steps:
      1. Run: just django-test
    Expected Result: Exit 0
    Evidence: .sisyphus/evidence/task-4-django-test.txt

  Scenario: No redirects introduced by URLConf change
    Tool: Django TestCase (agent executes via just)
    Steps:
      1. Run DAV guardrail tests from Task 2
    Expected Result: No 301/308 for DAV verbs
    Evidence: .sisyphus/evidence/task-4-django-test.txt
  ```

- [x] 5. Reference/Usage Audit For Hotspot Handlers (Pre-Move Safety)

  **What to do**:
  - Before converting complex endpoints, map all usages of the hotspot handlers and the helpers they rely on.
  - Use `lsp_find_references` (preferred) plus `grep` to find dynamic usages.
  - Focus on:
    - `dav/views_objects.py` `calendar_object_view`
    - `dav/views_objects.py` `calendar_collection_view`
    - `dav/views_collections.py` `calendar_home_view`
    - `dav/views_reports.py` `_handle_report`
    - `dav/views_common.py` `_require_dav_user`, `_dav_common_headers`, `_xml_response`
  - Produce a short, concrete reference map in an evidence file (paths + what calls what).

  **Must NOT do**:
  - Do not refactor code in this task; analysis/evidence only.

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: (none)

  **Parallelization**:
  - **Can Run In Parallel**: YES (after Task 4)
  - **Blocked By**: None
  - **Blocks**: Tasks 10-14 (conversion needs this map)

  **References**:
  - `dav/views_objects.py` - hotspot view functions.
  - `dav/views_collections.py` - home/root/principal handlers.
  - `dav/views_reports.py`, `dav/views_common.py` - shared behavior.

  **Acceptance Criteria**:
  - [ ] Evidence file exists: `.sisyphus/evidence/task-5-reference-map.txt`.

  **QA Scenarios**:
  ```
  Scenario: Reference map produced
    Tool: LSP + Grep
    Steps:
      1. Run lsp_find_references on key functions
      2. Grep for string-based/dynamic references
      3. Save findings
    Expected Result: Complete callsite map for hotspot handlers
    Evidence: .sisyphus/evidence/task-5-reference-map.txt

  Scenario: Map is actionable
    Tool: Human-free review (agent)
    Steps:
      1. Ensure every moved function has accounted callsites
    Expected Result: No “mystery callsites” left unaccounted
    Evidence: .sisyphus/evidence/task-5-reference-map.txt
  ```

- [x] 6. Convert DAV Root Endpoint To CBV

  **What to do**:
  - Implement a CBV for `/dav/` that provides explicit handlers for the methods currently supported (at minimum: `OPTIONS`, `GET/HEAD`, `PROPFIND`).
  - Ensure:
    - Auth challenge behavior matches current root behavior (via shared auth mixin)
    - `_dav_common_headers` applied to responses
    - `OPTIONS` `Allow` header includes the same verb set as before
  - Switch only the root entrypoint to CBV (via the shim from Task 4) while leaving other endpoints on FBVs.

  **Must NOT do**:
  - Do not change URL patterns.
  - Do not change XML/iCal rendering helpers.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: (none)

  **Parallelization**:
  - **Can Run In Parallel**: NO (touches shared entrypoint module)
  - **Blocked By**: Tasks 2-4
  - **Blocks**: Tasks 7-16

  **References**:
  - `dav/views_collections.py` - existing `dav_root` FBV behavior to match.
  - `dav/views_common.py` - headers/auth/xml helpers.
  - `dav/tests.py` - discovery tests for root/principal/home.

  **Acceptance Criteria**:
  - [ ] Root endpoint is served by CBV (explicit method handlers) with no behavior regressions.
  - [ ] `just django-test` passes.
  - [ ] Evidence: `.sisyphus/evidence/task-6-django-test.txt`.

  **QA Scenarios**:
  ```
  Scenario: Root endpoint semantics preserved
    Tool: Bash
    Steps:
      1. Run: just django-test
    Expected Result: Exit 0 (includes discovery/root tests)
    Evidence: .sisyphus/evidence/task-6-django-test.txt

  Scenario: OPTIONS/Allow/DAV guardrails stay green
    Tool: Django TestCase (agent executes via just)
    Steps:
      1. Run guardrail tests from Task 2
    Expected Result: No header/redirect regressions
    Evidence: .sisyphus/evidence/task-6-django-test.txt
  ```

- [x] 7. Convert Principal Endpoints To CBVs

  **What to do**:
  - Implement CBV(s) for principal collection and principal resources.
  - Ensure explicit handlers for `OPTIONS`, `GET/HEAD`, `PROPFIND` (matching current principal behavior).
  - Switch only principal-related entrypoints to CBVs (users/uid variants can share a single class if kwargs differ).

  **Must NOT do**:
  - Do not change how username/guid URL kwargs are interpreted.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: (none)

  **Parallelization**:
  - **Can Run In Parallel**: NO (touches shared entrypoint module)
  - **Blocked By**: Task 6
  - **Blocks**: Tasks 8-16

  **References**:
  - `dav/views_collections.py` - principal views and `_collection_view` patterns.
  - `dav/tests.py` - principal discovery/propfind assertions.

  **Acceptance Criteria**:
  - [ ] Principal endpoints served by CBVs; guardrail tests remain green.
  - [ ] `just django-test` passes; evidence saved.

  **QA Scenarios**:
  ```
  Scenario: Principal routing works for username and uid paths
    Tool: Bash
    Steps:
      1. Run: just django-test
    Expected Result: Exit 0
    Evidence: .sisyphus/evidence/task-7-django-test.txt

  Scenario: PROPFIND dispatch hits CBV propfind()
    Tool: Django TestCase
    Steps:
      1. Exercise principal PROPFIND tests
    Expected Result: 207 (or current expected) preserved; no 405
    Evidence: .sisyphus/evidence/task-7-django-test.txt
  ```

- [x] 8. Convert “Collection-Style” Endpoints To CBVs (Principals/Calendars Collections)

  **What to do**:
  - Replace `_collection_view`-style duplicated FBV dispatch with a CBV that cleanly handles:
    - `OPTIONS`
    - `GET/HEAD` (simple body)
    - `PROPFIND`
  - Ensure each collection endpoint still returns the correct display-name / prop map expected by clients.
  - Switch principals collection endpoints and calendars collection endpoints entrypoints to use this CBV.

  **Must NOT do**:
  - Do not change which collections exist or their URL shapes.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: (none)

  **Parallelization**:
  - **Can Run In Parallel**: NO (touches shared entrypoint module)
  - **Blocked By**: Task 7
  - **Blocks**: Task 9

  **References**:
  - `dav/views_collections.py` - `_collection_view` current behavior and the collection endpoints that use it.
  - `dav/core/propmap.py` - collection prop map builders.
  - `dav/tests.py` - discovery/collection tests.

  **Acceptance Criteria**:
  - [ ] Collection endpoints served by CBV; `OPTIONS`/`PROPFIND` behavior unchanged.
  - [ ] `just django-test` passes; evidence saved: `.sisyphus/evidence/task-8-django-test.txt`.

  **QA Scenarios**:
  ```
  Scenario: Collection endpoints remain discoverable
    Tool: Bash
    Steps:
      1. Run: just django-test
    Expected Result: Exit 0
    Evidence: .sisyphus/evidence/task-8-django-test.txt

  Scenario: OPTIONS/Allow/DAV remain stable on collection endpoints
    Tool: Django TestCase
    Steps:
      1. Run guardrail tests
    Expected Result: Allow/DAV headers present; no redirects
    Evidence: .sisyphus/evidence/task-8-django-test.txt
  ```

- [x] 9. Convert Calendar Home Endpoints To CBVs (Includes REPORT)

  **What to do**:
  - Convert `calendar_home_view` and its uid/users variants to CBV with explicit methods:
    - `OPTIONS`, `GET/HEAD`, `PROPFIND`, `REPORT`
  - Preserve Depth handling and visible-calendar enumeration semantics.
  - Preserve REPORT delegation via `dav/views_reports.py` `_handle_report` with `allow_sync_collection=False`.

  **Must NOT do**:
  - Do not change access control / visibility behavior.
  - Do not change ETag/Last-Modified conditional behavior.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: (none)

  **Parallelization**:
  - **Can Run In Parallel**: NO (touches shared entrypoint module)
  - **Blocked By**: Task 8
  - **Blocks**: Task 10

  **References**:
  - `dav/views_collections.py` - `calendar_home_view` current behavior.
  - `dav/views_reports.py` - `_handle_report` contract.
  - `dav/tests.py` - report tests hitting calendar home.

  **Acceptance Criteria**:
  - [ ] Home endpoints served by CBV; REPORT/PROPFIND behavior preserved.
  - [ ] `just django-test` passes; evidence: `.sisyphus/evidence/task-9-django-test.txt`.

  **QA Scenarios**:
  ```
  Scenario: Home REPORT still works
    Tool: Bash
    Steps:
      1. Run: just django-test
    Expected Result: Exit 0 (includes DavCollectionReportTests)
    Evidence: .sisyphus/evidence/task-9-django-test.txt

  Scenario: No-redirect for home paths
    Tool: Django TestCase
    Steps:
      1. Run no-redirect guardrail tests
    Expected Result: Never 301/308 on DAV verbs
    Evidence: .sisyphus/evidence/task-9-django-test.txt
  ```

- [x] 10. Convert Calendar Collection Endpoints To CBVs

  **What to do**:
  - Convert `calendar_collection_view` (and uid/users variants) to CBV with explicit methods for the existing verb set:
    - `OPTIONS`, `GET/HEAD`, `PROPFIND`, `REPORT`, `MKCALENDAR`, `MKCOL` (alias behavior), `PROPPATCH`, `DELETE`
  - Preserve the existing MKCOL->MKCALENDAR alias semantics.
  - Preserve REPORT delegation via `_handle_report(..., allow_sync_collection=True)`.
  - Keep calendar resolution + permission logic identical.
  - Allow “small behavior fixes” only when proven by failing tests/conformance (record in commit message).

  **Must NOT do**:
  - Do not change URL decoding / path segment parsing.
  - Do not rewrite XML builders or prop map builders.

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: (none)

  **Parallelization**:
  - **Can Run In Parallel**: NO (touches shared entrypoint module)
  - **Blocked By**: Task 9
  - **Blocks**: Task 11

  **References**:
  - `dav/views_objects.py` - current `calendar_collection_view` behavior and method handling.
  - `dav/views_reports.py` - REPORT behavior.
  - `dav/views_common.py` - ETag/conditional logic and response helpers.
  - `dav/tests.py` - MKCALENDAR, PROPFIND depth, REPORT, delete tests.

  **Acceptance Criteria**:
  - [ ] Collection endpoints served by CBV; methods behave as before.
  - [ ] `just django-test` passes; evidence: `.sisyphus/evidence/task-10-django-test.txt`.
  - [ ] `just caldavtester-test-suite` passes; evidence: `.sisyphus/evidence/task-10-caldavtester.txt`.

  **QA Scenarios**:
  ```
  Scenario: Unit tests for collections pass
    Tool: Bash
    Steps:
      1. Run: just django-test
    Expected Result: Exit 0
    Evidence: .sisyphus/evidence/task-10-django-test.txt

  Scenario: Conformance suite remains green after collection conversion
    Tool: Bash
    Steps:
      1. Run: just caldavtester-test-suite
    Expected Result: Exit 0
    Evidence: .sisyphus/evidence/task-10-caldavtester.txt
  ```


- [x] 11. Convert Calendar Object Endpoint To CBV (Read Path)

  **What to do**:
  - Convert `calendar_object_view` (and uid/users variants) to CBV for the read-oriented methods:
    - `OPTIONS`, `GET/HEAD`, `PROPFIND`
  - Preserve:
    - Conditional GET/HEAD (`ETag`/`Last-Modified` semantics)
    - CRLF-sensitive iCalendar output and Content-* header behavior
    - `OPTIONS` `Allow`/`DAV` header parity

  **Must NOT do**:
  - Do not change URL decoding / path segment parsing.
  - Do not touch PUT/DELETE/COPY/MOVE behavior yet (that is Tasks 12-13).

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: (none)

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocked By**: Tasks 5 and 10
  - **Blocks**: Task 12

  **References**:
  - `dav/views_objects.py` - current `calendar_object_view` read branches.
  - `dav/views_common.py` - ETag/conditional and response/header helpers.
  - `dav/tests.py` - object GET/PROPFIND wiring assertions.

  **Acceptance Criteria**:
  - [ ] Object endpoint read paths served by CBV; `just django-test` passes.
  - [ ] Evidence: `.sisyphus/evidence/task-11-django-test.txt`.

  **QA Scenarios**:
  ```
  Scenario: Object read paths remain correct
    Tool: Bash
    Steps:
      1. Run: just django-test
    Expected Result: Exit 0
    Evidence: .sisyphus/evidence/task-11-django-test.txt

  Scenario: No redirects and OPTIONS headers remain stable
    Tool: Django TestCase
    Steps:
      1. Run guardrail tests
    Expected Result: Guardrails remain green
    Evidence: .sisyphus/evidence/task-11-django-test.txt
  ```

- [x] 12. Convert Calendar Object Endpoint To CBV (Write Path)

  **What to do**:
  - Add the write-oriented methods to the calendar-object CBV:
    - `PUT`, `DELETE`
  - Preserve:
    - PUT preconditions (`If-Match` / `If-None-Match`) and correct 201/204 semantics
    - Correct ETag/Last-Modified setting post-write
    - Existing error/status mapping (412/415/etc.)
  - Keep helper usage (`dav/core/write_ops.py`) stable; only change behavior when failing tests prove a bug.

  **Must NOT do**:
  - Do not change COPY/MOVE/PROPPATCH/MKCOL semantics yet (Task 13).

  **Recommended Agent Profile**:
  - **Category**: `ultrabrain`
  - **Skills**: (none)

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocked By**: Task 11
  - **Blocks**: Task 13

  **References**:
  - `dav/core/write_ops.py` - preconditions + payload validation plan.
  - `dav/views_objects.py` - current PUT/DELETE branches to preserve.
  - `dav/tests.py` - DavObjectTests and DavWriteTests for PUT/conditional behaviors.

  **Acceptance Criteria**:
  - [ ] PUT/DELETE paths served by CBV; `just django-test` passes.
  - [ ] Evidence: `.sisyphus/evidence/task-12-django-test.txt`.

  **QA Scenarios**:
  ```
  Scenario: PUT/DELETE semantics preserved
    Tool: Bash
    Steps:
      1. Run: just django-test
    Expected Result: Exit 0
    Evidence: .sisyphus/evidence/task-12-django-test.txt

  Scenario: Conditional write behavior still correct
    Tool: Django TestCase
    Steps:
      1. Exercise conditional PUT tests
    Expected Result: 201/204/412 outcomes unchanged
    Evidence: .sisyphus/evidence/task-12-django-test.txt
  ```

- [x] 13. Convert Calendar Object Endpoint To CBV (Metadata + Copy/Move)

  **What to do**:
  - Add remaining methods to the calendar-object CBV:
    - `PROPPATCH`, `COPY`, `MOVE`, `MKCOL`, `MKCALENDAR`
  - Preserve:
    - Litmus-only restrictions currently enforced (until explicitly removed)
    - COPY/MOVE semantics via `dav/view_helpers/copy_move.py`
    - Correct 201/204/409/412 behaviors and Location header rules
  - After these methods are live, run conformance suites to validate.

  **Must NOT do**:
  - Do not broaden scope into new DAV features (LOCK/UNLOCK, scheduling, etc.).

  **Recommended Agent Profile**:
  - **Category**: `ultrabrain`
  - **Skills**: (none)

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocked By**: Task 12
  - **Blocks**: Task 14

  **References**:
  - `dav/view_helpers/copy_move.py` - COPY/MOVE behavior.
  - `dav/views_objects.py` - PROPPATCH/COPY/MOVE/MKCOL branches.
  - `dav/tests.py` - DavWriteTests coverage for these verbs.

  **Acceptance Criteria**:
  - [ ] `just django-test` passes.
  - [ ] `just caldavtester-test-suite` passes.
  - [ ] `just litmus-test` passes.
  - [ ] Evidence:
    - `.sisyphus/evidence/task-13-django-test.txt`
    - `.sisyphus/evidence/task-13-caldavtester.txt`
    - `.sisyphus/evidence/task-13-litmus.txt`

  **QA Scenarios**:
  ```
  Scenario: Unit tests cover PROPPATCH/COPY/MOVE
    Tool: Bash
    Steps:
      1. Run: just django-test
    Expected Result: Exit 0
    Evidence: .sisyphus/evidence/task-13-django-test.txt

  Scenario: Conformance suites validate protocol behavior
    Tool: Bash
    Steps:
      1. Run: just caldavtester-test-suite
      2. Run: just litmus-test
    Expected Result: Both exit 0
    Evidence: .sisyphus/evidence/task-13-caldavtester.txt and .sisyphus/evidence/task-13-litmus.txt
  ```

- [x] 14. Parity + Edge-Case Pass (Headers, Redirects, Preconditions)

  **What to do**:
  - After all endpoint conversions, do a parity-focused sweep:
    - Ensure `Allow` header verb sets match prior behavior per endpoint.
    - Ensure `WWW-Authenticate` challenge remains correct for unauthenticated DAV verbs.
    - Ensure no 301/308 redirects occur for any DAV verbs on canonical URLs.
    - Ensure 207 Multi-Status responses preserve structure expected by tests.
  - If any drift is found, fix by aligning CBV behavior to existing helpers/patterns (or by minimal behavior fixes supported by failing tests).

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: (none)

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocked By**: Task 13
  - **Blocks**: Task 16

  **References**:
  - `dav/views_common.py` - header helpers and error response builders.
  - `dav/middleware.py` - audit logging expectations.
  - `dav/tests.py` - existing assertions for headers/status codes.

  **Acceptance Criteria**:
  - [ ] `just django-test` passes.
  - [ ] `just caldavtester-test-suite` passes.
  - [ ] `just litmus-test` passes.
  - [ ] Evidence files:
    - `.sisyphus/evidence/task-14-django-test.txt`
    - `.sisyphus/evidence/task-14-caldavtester.txt`
    - `.sisyphus/evidence/task-14-litmus.txt`

  **QA Scenarios**:
  ```
  Scenario: Redirect and header guardrails remain green
    Tool: Bash
    Steps:
      1. Run: just django-test
    Expected Result: Exit 0
    Evidence: .sisyphus/evidence/task-14-django-test.txt

  Scenario: Protocol suites validate parity
    Tool: Bash
    Steps:
      1. Run: just caldavtester-test-suite
      2. Run: just litmus-test
    Expected Result: Both exit 0
    Evidence: .sisyphus/evidence/task-14-caldavtester.txt and .sisyphus/evidence/task-14-litmus.txt
  ```

- [x] 15. Remove Dead FBV Dispatch + Simplify Callstacks (No Behavior Drift)

  **What to do**:
  - Once CBVs are in place and green:
    - Remove the large `if request.method == ...` chains from endpoint entrypoints.
    - Keep protocol-heavy helpers in `dav/views_common.py` and `dav/views_reports.py` stable.
    - Ensure logging middleware still captures errors (no exception leaks).

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: (none)

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Task 16 preparation)
  - **Blocked By**: Task 14
  - **Blocks**: Task 16

  **References**:
  - `dav/views_objects.py`, `dav/views_collections.py` - old FBV dispatch sources to delete.
  - `dav/views_common.py`, `dav/views_reports.py` - keep stable.

  **Acceptance Criteria**:
  - [ ] No endpoint entrypoint still contains large method-branching chains.
  - [ ] `just django-test` passes; evidence: `.sisyphus/evidence/task-15-django-test.txt`.

  **QA Scenarios**:
  ```
  Scenario: Cleanup does not break behavior
    Tool: Bash
    Steps:
      1. Run: just django-test
    Expected Result: Exit 0
    Evidence: .sisyphus/evidence/task-15-django-test.txt

  Scenario: Guardrail tests still detect redirects/headers issues
    Tool: Django TestCase
    Steps:
      1. Run guardrail tests
    Expected Result: Guardrails remain green
    Evidence: .sisyphus/evidence/task-15-django-test.txt
  ```

- [x] 16. Required Gate: Run `just full-verify` And Capture Evidence

  **What to do**:
  - Run the required verification sweep and save output.

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: (none)

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocked By**: Tasks 14-15
  - **Blocks**: Final Verification Wave

  **References**:
  - `justfile` - `full-verify` recipe.
  - Baseline evidence from Task 1 for comparison.

  **Acceptance Criteria**:
  - [ ] `just full-verify` exits 0.
  - [ ] Evidence: `.sisyphus/evidence/task-16-full-verify.txt`.

  **QA Scenarios**:
  ```
  Scenario: Full verification gate passes
    Tool: Bash
    Steps:
      1. Run: just full-verify
    Expected Result: Exit 0
    Evidence: .sisyphus/evidence/task-16-full-verify.txt
  ```

---

## Final Verification Wave

- F1: Plan compliance audit (oracle)
- F2: Code quality review (unspecified-high)
- F3: Execute all QA scenarios + integration (unspecified-high)
- F4: Scope fidelity check (deep)

---

## Commit Strategy
- Prefer small atomic commits per converted endpoint group (root, principals, homes, collections, objects).
- No vendored CalDAVTester changes in these commits.

---

## Success Criteria
- `just full-verify` passes from a clean checkout.
- CalDAVTester + litmus remain green.
- `/dav/` endpoints have explicit CBV methods for DAV verbs; no large `if request.method == ...` chains remain in endpoint entrypoints.
