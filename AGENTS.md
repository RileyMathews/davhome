# AGENTS.md

Guidance for coding agents working in this repository.

## Project Snapshot

- Stack: Django 6 + Python 3.12.
- Package/runtime tooling: `uv` (see `pyproject.toml`, `uv.lock`).
- Task runner: `just` (`justfile` recipes are the primary workflow entry points).
- Main apps: `accounts`, `calendars`, `dav`.
- Local/dev DB uses SQLite (`config.settings_dev`); production settings use Postgres (`config.settings`).

## Rule Files (Cursor / Copilot)

- `.cursor/rules/`: not present.
- `.cursorrules`: not present.
- `.github/copilot-instructions.md`: not present.
- No extra IDE agent rule set is currently enforced in-repo.

## Environment Setup

- Install dependencies:
  - `uv sync`
- Activate environment if needed:
  - `source .venv/bin/activate`
- For agent-driven local work, prefer `config.settings_test` or `config.settings_dev` to avoid Postgres/env requirements.

## Build / Run Commands
Use a single command for correctness verification:

- `just full-verify`

`just full-verify` is the required verification sweep for agents. It builds and starts a fresh Docker container, applies migrations and integration fixtures inside that container, runs integration suites, runs Django unit tests, and then tears the container down.

Do not treat partial test runs as completion verification when `just full-verify` is available.


### CalDAVTester source of truth

- The CalDAVTester repos are vendored under `caldavtester-lab/`:
  - `caldavtester-lab/ccs-caldavtester`
  - `caldavtester-lab/ccs-pycalendar`
- `ccs-caldavtester` is a vendored upstream conformance suite and should be
  treated as spec authority for supported modules.
- Do not rewrite existing vendored tests/resources to make failures pass.
  In normal feature work, only adjust which tests run by changing:
  - module inclusion in `caldavtester-lab/caldav-suite-modules.txt`
  - feature toggles in
    `caldavtester-lab/ccs-caldavtester/scripts/server/serverinfo.xml`
    (and matching template/pod variants when needed)
- If you think a change to vendored tests/resources is required (beyond
  enabling/disabling modules/features), stop and ask the user for explicit
  clarification before making any such change.
- `just caldavtester-test-suite` runs an explicit list of supported
  `scripts/tests/CalDAV/*.xml` modules from
  `caldavtester-lab/caldav-suite-modules.txt`.
- `caldavtester-lab/ccs-caldavtester/scripts/server/serverinfo.xml` controls
  feature-gated behavior *within* those modules.
  - Tests/suites guarded with `<require-feature>` only run when the matching
    `<feature>` is enabled in that file.
  - To add/remove module-level coverage in the default suite, edit
    `caldavtester-lab/caldav-suite-modules.txt`.
  - To tune suite/test-level coverage inside a module, adjust feature flags in
    `serverinfo.xml`.

## Lint / Format / Type Checks

- No mandatory lint/format tool is configured in this repo (no committed Ruff/Black/isort config).
- Do not invent new tooling in feature PRs unless requested.
- Minimum quality check agents should run before handoff:
  - `just full-verify`
- If you use local optional tools, keep behavior non-invasive and match existing formatting.

## Code Organization Conventions

- Keep Django app boundaries clear:
  - `accounts`: auth and registration flows.
  - `calendars`: domain models, sharing, calendar management UI.
  - `dav`: DAV/CalDAV protocol endpoints and XML/report logic.
- Put URL routing in each app `urls.py`; include from `config/urls.py`.
- Keep protocol-heavy helpers private in `dav/views.py` using underscore-prefixed function names.

## Imports

- Follow existing import order style:
  1) standard library
  2) third-party (Django, ical libs)
  3) local app imports
- Use one import per line where reasonable; keep grouped imports readable.
- Prefer explicit imports over wildcard imports (exception currently exists in `config/settings_test.py`).

## Formatting

- Match existing Python style:
  - 4-space indentation
  - double quotes for strings
  - trailing commas on multiline literals/calls
  - blank lines between top-level defs/classes
- Keep functions focused; extract helpers when protocol logic grows.
- Preserve CRLF-sensitive iCalendar output behavior where present (many functions intentionally emit `\r\n`).

## Types and Typing Discipline

- Type hints are used selectively; add hints where they improve clarity, especially in new utility functions.
- Keep compatibility with Django dynamic patterns; avoid over-constraining model/queryset types.
- Some files intentionally suppress strict Pyright diagnostics at file top; do not remove suppressions unless you are also fixing all resulting type issues.

## Naming Conventions

- Classes: `PascalCase` (`CalendarShare`, `DavReportTests`).
- Functions/variables: `snake_case`.
- Private/internal helpers: leading underscore (`_parse_ical_datetime`).
- URL names: kebab-case in route names (`share-update`, `calendar-home-no-slash`).
- Constants: uppercase (`NS_DAV`, `ROLE_CHOICES`).

## Django and Domain Practices

- Use `get_object_or_404` for UI object fetches where 404 is expected behavior.
- Use permission helpers from `calendars/permissions.py` instead of duplicating role checks.
- Use `timezone.now()` for persisted timestamps.
- Keep model constraints in `Meta.constraints`; rely on DB integrity where appropriate.
- For calendar object content, preserve ETag/content-type behavior expected by DAV tests.

## Error Handling

- Prefer explicit exception handling for expected failures (`DoesNotExist`, `ParseError`, `UnicodeDecodeError`).
- Return protocol-appropriate HTTP statuses in DAV layer (401/403/404/405/409/412/415/501/207).
- For malformed XML or iCal payloads, fail gracefully with clear status responses instead of uncaught exceptions.
- In parser/helper code, returning `None` for invalid input is an established pattern; keep it consistent.

## Testing Style

- Use Django `TestCase`.
- Use `setUpTestData` for shared fixtures; keep per-test setup minimal.
- Prefer `reverse()` for Django route-based tests; use direct DAV paths when testing protocol URL shapes.
- Assert both status codes and relevant response body/headers (notably DAV headers, ETag, XML fragments).
- Add regression tests for protocol edge cases before changing DAV behavior.

## Migrations and Data Changes

- If models change, create migrations and include them in commits.
- Keep migrations deterministic and minimal.
- Avoid editing historical migrations unless explicitly requested.

## Agent Workflow Expectations

- Make small, targeted changes.
- Read nearby code before editing to match local patterns.
- Update/add tests with behavior changes.
- Run `just full-verify` as the default and final verification command.
- If unable to run a command locally, state exactly what was not run and why.
