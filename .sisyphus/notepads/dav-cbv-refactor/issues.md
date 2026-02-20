# Issues

## Task 5: Method Dispatch Risk Assessment (2026-02-20)

### High-Risk Complexity Factors

1. **calendar_object_view (views_objects.py:279-651) - 11 methods**
   - Risk: Extreme method dispatch complexity in single function
   - Coupling: COPY/MOVE share code path (lines 340-352), PROPPATCH has nested logic (lines 354-404)
   - Conditional branching: 7 permission checks based on writable.slug != "litmus"
   - Mitigation needed: Consider splitting into separate handler methods per verb

2. **calendar_collection_view (views_objects.py:59-264) - 9 methods**
   - Risk: MKCOL→MKCALENDAR method rewriting (lines 84-91) is non-standard pattern
   - Coupling: DELETE and PROPPATCH interleave with GET/HEAD checks
   - Mitigation needed: Standardize on explicit method handlers

3. **Inline auth checks after OPTIONS**
   - Risk: `_require_dav_user` called at different points in each endpoint
   - Example: views_collections.py:230 vs views_objects.py:76 - inconsistent auth placement
   - Mitigation needed: CBV mixin for authentication

4. **Precondition coupling in PUT flow**
   - Risk: If-Match/If-None-Match handling (lines 501-509) tightly coupled with PUT logic
   - Dependencies: core_write_ops module imported and used inline
   - Mitigation needed: Extract to write strategy class

5. **Literal string method comparisons**
   - Risk: All method checks use `request.method == "VERB"` rather than constants/enums
   - Example: `if request.method == "MKCALENDAR":` appears 5+ times
   - Mitigation needed: Use Django's HttpMethodMatcher or constant definitions

### Subtle Behavioral Couplings

1. **Litmus test special-casing** (views_objects.py:317,325,407)
   - Behavior: Hardcoded permission bypass for slug="litmus"
   - Risk: Hidden precondition that breaks if slug handling changes
   - Impact: Affects PROPPATCH, COPY, MOVE, MKCOL

2. **Collection marker resolution** (views_objects.py:360-365,461-465,609-610)
   - Behavior: Filenames ending "/" resolve to marker files
   - Risk: Implicit behavior in DELETE, PROPPATCH, GET flows
   - Impact: Could be extracted to path resolution helper

3. **Method mutation** (views_objects.py:87-90)
   - Behavior: `request.method = "MKCALENDAR"` - direct mutation
   - Risk: Side effect that obscures control flow
   - Impact: Makes CBV conversion harder

4. **Depth header coupling** (views_collections.py:67,233,272; views_objects.py:233)
   - Behavior: Depth header affects PROPFIND response shape
   - Risk: Scattered depth handling across entrypoints
   - Impact: Need consistent depth strategy in CBV

### Testing Implications

- Current tests in `tests.py` cover method dispatch implicitly
- Hotspots need explicit unit tests for each verb before CBV conversion
- Litmus-specific behavior needs dedicated test coverage

### Conversion Readiness

- LOW: calendar_object_view (needs significant decomposition)
- MEDIUM: calendar_collection_view (moderately complex)
- MEDIUM: calendar_home_view (5 methods, but REPORT coupling)
- HIGH: principal_view, dav_root (4 methods each, straightforward)

## Task 2 Follow-up Concerns (2026-02-20)

- Guardrail tests now pin canonical DAV URLs (`/dav/` and `/dav/calendars/{username}/{slug}/`) against redirect/method-dispatch regressions, but do not yet add equivalent non-canonical REPORT no-trailing-slash guardrails; existing coverage for no-slash paths remains mostly PROPFIND-focused.

## Django CBV Method Dispatch Pitfalls (2026-02-20)

### Critical Pitfalls

1. **Case Mismatch Between request.method and Handler Names**
   - Symptom: 405 Method Not Allowed for valid DAV verbs
   - Cause: Defining `def PROPFIND()` instead of `def propfind()`
   - Fix: Always use lowercase handler method names matching lowercase http_method_names entries
   - Reference: Django dispatches via `method = request.method.lower()` (line 138 of View.dispatch)

2. **http_method_names Without Corresponding Handler**
   - Symptom: Verb not in Allow header on OPTIONS response
   - Cause: Adding "propfind" to http_method_names but not defining `propfind()` method
   - Fix: Ensure every entry in http_method_names has a matching handler method
   - Reference: `_allowed_methods()` uses `hasattr(self, m)` check (line 180)

3. **HEAD Fallthrough to GET**
   - Symptom: HEAD requests return GET response body
   - Cause: Django's View.setup() automatically assigns `self.head = self.get` if get exists but head doesn't
   - Impact: For DAV, this may be undesirable if GET returns large bodies
   - Fix: Define explicit `head()` handler if different behavior needed

4. **OPTIONS Not in http_method_names but options() Exists**
   - Symptom: OPTIONS appears in Allow header unexpectedly
   - Cause: Django always includes "options" in default http_method_names AND always has built-in options() handler
   - Impact: If you remove "options" from http_method_names but want custom OPTIONS handling, Django will still use built-in
   - Fix: Override options() method for custom behavior (it's called regardless)

5. **405 Response Lacks Allow Header**
   - Symptom: Client doesn't know what methods are supported
   - Cause: Custom http_method_not_allowed that doesn't call super() or set Allow header
   - Fix: Use Django's default http_method_not_allowed or ensure response includes Allow header

### DAV-Specific Considerations

1. **WebDAV Methods Not in Default http_method_names**
   - Methods: PROPFIND, PROPPATCH, REPORT, ACL, MKCOL, MOVE, COPY, LOCK, UNLOCK
   - Impact: Must explicitly add to http_method_names
   - Mitigation: Always extend http_method_names when supporting DAV verbs

2. **Method Rewriting Pattern (MKCALENDAR)**
   - Symptom: Current codebase uses `request.method = "MKCALENDAR"` pattern
   - Impact: This won't work with CBV dispatch - the method is already lowercased before handler lookup
   - Mitigation: Use explicit handler for MKCOL that detects calendar-create intent via URL or content-type

3. **Depth Header Handling**
   - Symptom: Depth header affects response but implementation scattered
   - Impact: Need consistent strategy - extract to method or mixin
   - Reference: Current code has depth handling in multiple entrypoints

4. **Auth Placement After OPTIONS**
   - Symptom: Current auth checks happen at different points in each endpoint
   - Impact: CBV conversion needs consistent auth mixin pattern
   - Reference: Issues noted in Task 5 risk assessment

### Testing Verification Checklist

- [ ] OPTIONS request returns correct Allow header with all supported methods
- [ ] Unsupported method returns 405 with Allow header
- [ ] PROPFIND, PROPPATCH, REPORT verbs handled correctly (lowercase in code)
- [ ] Custom method in http_method_names but no handler = not in Allow header
- [ ] HEAD requests behave as expected (body vs no body)

## Task 3 Foundation Caveats (2026-02-20)

- `DavView.dispatch()` currently applies `_dav_common_headers` to all returned responses, including 401 auth challenges. This is intended for stronger header consistency but can differ from legacy paths where some early auth responses did not always include `DAV`.
- `DavOptionsMixin` defaults `Allow` derivation from `http_method_names` + implemented handlers. Endpoint migrations should set explicit `allowed_methods` lists when order/parity must match existing FBV `Allow` header strings exactly.
- `DavAuthMixin` defaults `require_dav_auth = True`; endpoints like DAV root that currently do method-specific auth behavior (e.g., OPTIONS unauthenticated, GET/HEAD authenticated) will need per-view override/hook logic during migration.
- Custom 405 handling now routes through `_not_allowed()` (good for logging parity), but endpoint conversion should continue to pass identifying context kwargs to preserve existing audit richness.

## Task 7: Test Risk Items and Coverage Gaps (2026-02-20)

### High-Risk Testing Gaps

#### 1. Allow Header Verification (HIGH RISK)

**Issue**: Zero tests verify the Allow header on ANY endpoint

**Current**: Only tests check for status code 204 on OPTIONS requests

**Risk**: CBV migration could drop Allow header, breaking CalDAV clients

**Candidate Test**:
```python
def test_calendar_collection_options_includes_allow_header(self):
    response = self.client.options(f"/dav/calendars/{owner}/{calendar}/")
    self.assertEqual(response.status_code, 204)
    allow = response.headers.get("Allow", "")
    self.assertIn("PROPFIND", allow)
    self.assertIn("REPORT", allow)
    self.assertIn("GET", allow)
    self.assertIn("HEAD", allow)
    self.assertIn("DELETE", allow)
```

#### 2. DAV Header on Sub-Endpoints (HIGH RISK)

**Issue**: Only /dav/ and /dav (no trailing slash) have DAV header tests

**Current**: 2 tests in tests.py:151-159

**Risk**: Calendar collection, calendar object, principal endpoints may return incorrect DAV header after CBV conversion

**Candidate Test**:
```python
def test_calendar_home_options_advertises_dav(self):
    response = self.client.options(f"/dav/calendars/{owner}/")
    self.assertEqual(response.status_code, 204)
    self.assertIn("calendar-access", response.headers.get("DAV", ""))
```

#### 3. No-Redirect Behavior (MEDIUM RISK)

**Issue**: Only tests verify redirects FROM /.well-known/, not absence of redirects TO endpoints

**Current**: 2 tests for /.well-known/ redirects only

**Risk**: CBV might introduce unwanted trailing-slash redirects

**Candidate Test**:
```python
def test_dav_root_no_redirect(self):
    response = self.client.options("/dav/")
    self.assertEqual(response.status_code, 204)  # Not 301/302/307/308
    
def test_calendar_home_no_redirect(self):
    response = self.client.options(f"/dav/calendars/{owner}/")
    self.assertEqual(response.status_code, 204)  # Not 301/302/307/308
```

#### 4. 405 Method Not Allowed (MEDIUM RISK)

**Issue**: No tests verify 405 is returned for unsupported methods

**Current**: Implicitly tested via other methods succeeding, but no explicit 405

**Risk**: CBV could fail to return 405 for unsupported verbs

**Candidate Test**:
```python
def test_calendar_object_put_unsupported_on_principal(self):
    # PUT should not be allowed on principal collection
    response = self.client.put(f"/dav/principals/{owner}/", data=b"BODY")
    self.assertEqual(response.status_code, 405)
    allow = response.headers.get("Allow", "")
    self.assertIn("PROPFIND", allow)
```

### Authentication Challenge Gaps

#### 5. Auth Challenge on Sub-Endpoints (MEDIUM RISK)

**Issue**: Only dav_root has 401 auth challenge tests (tests.py:120-149)

**Missing Coverage**:
- calendar_home unauthenticated PROPFIND → should return 401
- calendar_collection unauthenticated PROPFIND → should return 401
- principal unauthenticated PROPFIND → should return 401

**Current**: 2 tests at tests.py:120-149

**Candidate Test**:
```python
def test_calendar_home_propfind_requires_authentication(self):
    response = self.client.generic("PROPFIND", f"/dav/calendars/{owner}/", data="")
    self.assertEqual(response.status_code, 401)
    self.assertIn("Basic", response.headers.get("WWW-Authenticate", ""))
```

### Regression Risk Assessment

| Test Area | Current Coverage | CBV Risk | Priority |
|-----------|-----------------|----------|----------|
| OPTIONS + Allow header | None | HIGH | P0 |
| OPTIONS + DAV header (non-root) | None | HIGH | P0 |
| No-redirect behavior | Partial | MEDIUM | P1 |
| 405 for unsupported methods | None | MEDIUM | P1 |
| Auth challenge (non-root) | Partial | MEDIUM | P1PFIND depth handling |
| PRO | Good | LOW | P2 |
| REPORT dispatch | Good | LOW | P2 |
| COPY/MOVE status codes | Good | LOW | P2 |

### Test Execution Recommendations

1. **Before CBV conversion**: Run existing tests to establish baseline
2. **During CBV conversion**: Add guardrail assertions incrementally
3. **After CBV conversion**: Full test suite should pass with new guardrails

### Files Requiring Modification (for Task 2)

To add guardrails without changing existing tests, create new test methods in:
- `dav/tests.py` - Add new test methods to `DavDiscoveryTests` class
- Or create new file: `dav/test_cbv_guardrails.py`

### Dependency Notes

- Task 2 (guardrail tests) feeds from this coverage map
- Task 14/15 (parity checks) will reuse this inventory
- The 26 test files form the baseline for all future comparison
- Issue: Some tests fail due to calendar_home_uid_view slug parameter mismatch after introducing shim. Action: adjust signature to accept slug or wrap logic to map GUID to user context.
- Issue: Calendar UID alias tests expect 207 status; current wrapper logic returns 404 in some scenarios. Action: refine GUID-to-username resolution or adjust routing for UID alias paths.
Blocker/Risk: Unresolved dynamic imports and runtime callsites may exist beyond static analysis; follow-up validation required.
