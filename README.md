## Test Coverage

Branch coverage reporting is configured with Coverage.py for first-party app code:

- `accounts`
- `calendars`
- `dav`

Coverage is currently enforced at 69% branch coverage for this scope.

Commands:

- `just django-test` - run Django tests (no parallel mode)
- `just django-test-cov` - run tests with branch coverage and fail below configured threshold
- `just django-test-cov-html` - same as above, then write HTML report to `htmlcov/`
- `just django-test-cov-xml` - same as above, then write `coverage.xml`

## Testing Strategy

The DAV test strategy follows a functional-core / imperative-shell split:

- Pure core tests live in `dav/test_core_*.py` and target business/protocol logic in `dav/core/*`.
- Shell tests live in `dav/test_shell_adapters.py` and verify mapping boundaries.
- Endpoint tests in `dav/tests.py` remain focused on auth, permissions, status codes, headers, and response wiring.

See `FCIS_TESTING_MAP.md` for mapping examples between endpoint scenarios and pure-core coverage.
