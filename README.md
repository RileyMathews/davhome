## Test Coverage

Branch coverage reporting is configured with Coverage.py for first-party app code:

- `accounts`
- `calendars`
- `dav`

Coverage is currently enforced at 54% branch coverage for this scope.

Commands:

- `just django-test` - run Django tests (no parallel mode)
- `just django-test-cov` - run tests with branch coverage and fail below 100%
- `just django-test-cov-html` - same as above, then write HTML report to `htmlcov/`
- `just django-test-cov-xml` - same as above, then write `coverage.xml`
