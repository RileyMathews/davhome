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

## Typing

- Agents should incrementally improve type checking in code they touch.
- Prefer expressing guaranteed shapes in signatures and shared typed abstractions instead of adding redundant runtime type guards.
- In Django views, rely on typed URL parameters when routing already guarantees string path components.
- Keep runtime validation at genuinely dynamic boundaries such as parsed payloads, rewritten dispatch kwargs, unauthenticated requests, or external input that the type system cannot guarantee.
- Run `just type-check` or `uv run mypy` during implementation when a change affects typed code.


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

# Refactor goals
We want to try to refactor this app as we continue working on it to match the future architecture vision.
We want views to be very clear to follow and have minimal indirection. An ideal view function would follow this pattern

1. validate inputs
2. return validation errors if needed
3. query a database model directly
4. serialize model(s) to response shape

Whenever Django or our own abstractions can guarantee an input shape, prefer pushing that guarantee into type annotations instead of repeating defensive runtime `isinstance` checks in the view body.
Use runtime validation for protocol data and other truly dynamic inputs, but let typed method signatures carry router-provided values like `username`, `slug`, and `filename`.

The serialization should be built into the model system with functions like `to_xml` or similar.
Share these serialization functions on a base model when it would be helpful.

The goal here is to maximize for clarity. Currently logic is scattered among lots of util/helper files.
A human should be able to pull up any view function and immediatley get a clear picture of its high level
flow. Then digging into functions and model methods it calls should slowly add context if needed.

In any cases where implementation might be ambiguous follow django best practices.
