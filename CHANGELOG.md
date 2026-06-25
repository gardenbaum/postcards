# Changelog

All notable changes to `postcards` are documented in this file. The format
is loosely based on [Keep a Changelog](https://keepachangelog.com/), and
the project adheres to [Semantic Versioning](https://semver.org/) as far
as is practical for a wrapper around an unofficial upstream API.

Releases follow the calendar-versioning rule described in
[`docs/RELEASE.md`](docs/RELEASE.md). Cutting a release is a single
`git tag` step; the `[Unreleased]` block below becomes the new
release section verbatim, and the version bump in
`postcards/__init__.py` is the only required code change.

## [4.0.0] — 2026-06-25

The release reorients `postcards` around a visual, interactive
front-end. A postcard is a visual object, so the headline feature is
*seeing the card before you send it*.

### Added

- **`postcards app` — interactive WYSIWYG web app (NiceGUI).**
  A single-page app with a **live, print-accurate preview** of both
  sides: the Front (A6 landscape, 3 mm bleed, safe area) and the Back
  (message, recipient / sender address, postage box) redraw as you type.
  Upload/clear a picture, edit the message and addresses, toggle print
  guides, pick the Mock (default, nothing sent) or SwissID (live)
  backend, keep Dry-run on to validate without consuming quota, then
  send. Opt-in via the new `app` extra (`uv pip install '.[app]'`); the
  command prints an actionable install hint when NiceGUI is missing.
  See [`docs/APP.md`](docs/APP.md).
  - **`postcards.web.service`** — a network- and UI-free core (draft →
    postcard, image processing, live PNG preview, validation, send via
    any `Backend`). 100 % unit-tested against the mock backend.
  - **`postcards.web.app`** — the thin NiceGUI UI layer, plus
    `python -m postcards.web`. Build is smoke-tested headlessly via
    NiceGUI's user simulation (no browser, no network).
  - **`postcards[app]` extra** (`nicegui`) and the renderer now draws
    WYSIWYG guides (3 mm bleed, safe area, stamp box, address zone) and
    exposes `render_png_bytes()`.
  - **In-app credential management.** The SwissID section resolves
    accounts (env → keyring → config), prefills the form, and offers
    *Load password*, *Save to keyring* and *Check login & quota* — all
    via the existing `ConfigLayer` / `KeyringStore`, so no CLI round-trip
    is needed. `service.resolve_auth` / `save_to_keyring` / `check_login`
    are network-free (mock-testable except the live login probe).

- **Real SwissID sending (the vendored client is no longer a stub).**
  `postcards._vendor.postcard_creator` now ships a modernized, working
  re-implementation of the SwissID OAuth + SAML token flow (PKCE) and
  the Postcard Creator mobile API (`/user/quota`, `/card/upload` with
  base64 front 1819×1311 + text-cover 720×744), using only `requests` /
  `beautifulsoup4` / Pillow — no `Js2Py`. Both the web app and the CLI
  `send` now reach the live service; `SwissIdConsumerBackend.login`
  performs the real flow and maps failures to `AuthenticationError`.
  Every network method takes an injectable session so the suite drives
  the full flow against a fake session — the live API is **never**
  called in tests/CI.
  - **Browser-assisted login for 2FA accounts.** Because SwissID's
    second factor (push / passkey / SMS) cannot be automated headlessly,
    the app/backend can hand the login to the user's browser: open the
    SwissID authorize URL, complete 2FA there, then paste the returned
    `ch.post.pcc://…?code=…` back; the app exchanges it (PKCE) for a
    token (`Token.build_authorize_url` / `exchange_code`,
    `SwissIdConsumerBackend.begin_browser_login` /
    `complete_browser_login`, `service.begin/complete_browser_login`).
    Direct e-mail + password still works for accounts without 2FA.
  - **Caveats:** the unofficial endpoints can drift server-side; a real
    send remains the user's manual step. For unattended/business use,
    Swiss Post's official PostCard Creator API (OAuth2 + contract) is the
    robust route (not wired in here).

### Removed

- **The Textual TUI** (`postcards tui`, the `postcards.tui` package, the
  `gui` / `textual` extra, `docs/TUI.md`). Its "preview" only opened a
  temp PNG externally — no real WYSIWYG — so the web app replaces it.
- **The `postcards legacy` argparse escape-hatch command.** (The
  internal `postcards.postcards` engine that `send` / `batch` / `accounts`
  reuse is unchanged; only the user-facing argparse passthrough is gone.)

These removals are breaking, hence the major version bump.

## [Unreleased]

### Added

- **M6 — optional local TUI.**
  The `postcards tui` subcommand launches a small Textual-
  based terminal UI for composing, previewing, and (with an
  explicit confirmation step) sending a postcard. The TUI is
  opt-in via the new `gui` extra: `pip install 'postcards[gui]'`.
  Without the extra, `postcards tui` exits with a clear
  "install `postcards[gui]`" message — the core CLI keeps
  working. See [`docs/TUI.md`](docs/TUI.md) for the user
  guide.

  - **`postcards[gui]` extra.** `pyproject.toml` adds an
    optional `gui = ["textual>=0.85"]` dep set. `textual`
    pulls in `rich` and a small set of well-behaved
    transitive deps. No new runtime dep is forced on users
    who never run the TUI.
  - **`postcards tui` command.** New Typer subcommand in
    [`postcards/cli/commands/tui.py`](postcards/cli/commands/tui.py).
    Flags: `--config / -c` (config file), `--accounts-file /
    -a`, `--send` (disable the default dry-run). Imports
    :mod:`textual` lazily so a missing dep surfaces as an
    actionable `pip install` error.
  - **TUI package at [`postcards/tui/`](postcards/tui/).**
    Four modules: `state.py` (the `ComposeForm` value
    object), `app.py` (`PostcardsApp` — the bridge between
    the form and the existing CLI pipeline), `screens.py`
    (the six screens: MainMenu, Compose, AddressBook,
    TemplateBook, Preview, SendConfirm, Help). Each screen
    is a thin wrapper around a dynamic `textual.screen.Screen`
    subclass so the TUI tests can drive them through
    `textual.pilot.Pilot` without a real terminal.
  - **Reuses the existing pipeline.** The TUI does not
    duplicate any logic: `PostcardsApp.build_in_memory_config`
    builds the `recipient` / `sender` dicts the legacy
    `do_command_send` flow expects (mirroring
    `_address_to_legacy_dict` from `postcards/cli/commands/send.py`),
    `PostcardsApp.build_send_namespace` builds the same
    `argparse.Namespace` shape `do_command_send` accepts, and
    `PostcardsApp.render_preview` delegates to
    `postcards.render.render_postcard`. The TUI never calls
    the network directly.
  - **Dry-run by default.** The "Send real" button stays
    disabled until the user un-checks the dry-run box AND
    types `YES` (uppercase) at the confirm modal. The
    safety model mirrors the CLI's `--dry-run` flag while
    keeping the user in the loop.
  - **Read-only address book + template browser.** The TUI
    shows the user's address book and templates but never
    writes to them — mutations happen via the existing
    `postcards addresses add ...` and
    `postcards templates add ...` commands, where the
    on-disk format and validation live in one place.
  - **Tests** (`tests/test_tui.py`, 43 tests). State tests
    for `ComposeForm`; app-glue tests for
    `PostcardsApp.{build_in_memory_config, build_send_namespace,
    render_preview, _render_template_message}`; Pilot-driven
    screen tests for every screen (mount + button + input
    events); end-to-end Compose → Send-dry-run flow with the
    same `Token.has_valid_credentials` /
    `PostcardCreatorBase.has_free_postcard` /
    `PostcardCreatorBase.send_free_card` triple the existing
    CLI integration tests use.
  - **User guide** at [`docs/TUI.md`](docs/TUI.md): why a
    TUI (vs web UI), install, screen-by-screen walkthrough,
    safety model, keyboard reference, troubleshooting.

- **M6 — packaging, distribution, docs overhaul.**
  M6 closes the distribution surface: the package is publish-ready
  for PyPI (`pipx install .` works end-to-end), the README is a
  complete user guide, and a Dockerfile ships the CLI in a slim
  container. See [`docs/INSTALL.md`](docs/INSTALL.md),
  [`docs/DOCKER.md`](docs/DOCKER.md), and
  [`docs/RELEASE.md`](docs/RELEASE.md) for the new surfaces.

  - **Version 3.0.0.** Bumped from 2.2. The 3.x series reflects the
    full modernization that landed across M0–M5 (Typer CLI,
    vendored postcard-creator shim, plugin registry, address book,
    batch, schedule, retries / quota / keyring / doctor). The
    canonical version lives in `postcards/__init__.py` as
    `__version__`; hatchling reads it via `[tool.hatch.version]`
    so the wheel metadata and the runtime `__version__` cannot
    drift apart.

  - **`pyproject.toml` metadata.** New classifiers (Development
    Status 4-Beta, Intended Audience Developers, CPython, OS
    Independent + Linux/macOS/Windows, Topic Utilities,
    Environment Console); project URLs for Documentation, Issues,
    Changelog (pointing at `gardenbaum/postcards`); the original
    `abertschi/postcards` is preserved as `Upstream`. README is
    declared as the long description (`text/markdown`).

  - **`pipx install .` is the recommended install path.** Verified
    on Python 3.13: installs cleanly into an isolated venv and
    exposes all five console scripts (`postcards`,
    `postcards-folder`, `postcards-yaml`, `postcards-pexels`,
    `postcards-chuck-norris`). `pip install .` from a checkout
    also works.

  - **Docker image.** New `Dockerfile` at the repo root builds a
    slim `python:3.13-slim` image that ships the CLI. The default
    `CMD` is `["postcards", "--help"]`; override with
    `docker run --rm -it postcards:<tag> send ...` or mount a
    config file via `-v $PWD/config.json:/home/postcards/config.json:ro`.
    See [`docs/DOCKER.md`](docs/DOCKER.md) for the full recipe,
    including running `postcards doctor` in a container to
    diagnose a host config without installing the package.

  - **Docs.** Full README overhaul (`README.md`) covering
    install, SwissID setup, send / preview / quota, plugins
    (folder, yaml, pexels, unsplash, url, local, chuck_norris),
    batch, schedule, keyring, troubleshooting, FAQ. New docs:
    [`docs/INSTALL.md`](docs/INSTALL.md) (per-OS install paths
    and `pipx` recipe), [`docs/DOCKER.md`](docs/DOCKER.md)
    (image build, run, and mount patterns),
    [`docs/RELEASE.md`](docs/RELEASE.md) (PyPI publish workflow
    with `twine` + trusted publishing, GitHub release checklist,
    and a per-milestone retrospective template). The legacy
    `--help` excerpt in the README is replaced by a current
    Typer-rendered command list.

  - **Tests for the packaging surface.**
    `tests/test_packaging.py` covers wheel metadata (version,
    classifiers, URLs, requires-python, entry-point group),
    `pip`-style `install_requires` resolution, and the
    `postcards.plugins` entry-point group enumerates every
    in-tree plugin. `tests/test_dockerfile.py` parses the
    `Dockerfile` and asserts the base image, the install layer,
    and the default CMD without requiring Docker to be
    installed locally.

- **M5 — retries, quota awareness, structured logging.**
  The CLI now handles flaky networks, the daily
  1-card quota, and verbose logging as first-class
  concerns. See `docs/ROBUSTNESS.md` for the full
  reference.

  - **Retry / backoff.** New `postcards.retry` module
    with a typed `RetryPolicy` dataclass (4 attempts,
    0.5s base delay, 2x multiplier, 8s ceiling) and a
    `with_retries()` helper implementing AWS-style full
    jitter. The SwissID backend wraps every
    `backend.login`, `backend.quota`, and `backend.send`
    call in it; the classifier retries on
    `TransientBackendError` and on `requests` network
    exceptions (`ConnectionError`, `Timeout`, 5xx
    `HTTPError`). `AuthenticationError`, `QuotaExhaustedError`,
    and `NotImplementedError` (the shim's "not implemented"
    stub) are non-retryable. New
    `postcards.backend.exceptions` module owns the
    typed hierarchy:

    ```
    BackendError(RuntimeError)
    ├── AuthenticationError
    ├── QuotaExhaustedError(next_available_at, retention_days)
    └── TransientBackendError
    ```

  - **Quota awareness.** `postcards quota` gained
    `--wait` (block until the quota opens, with
    `--max-wait` and `--poll` controls) and `--no-fail`
    (exit 0 even on exhaustion, for shell-script gates).
    The next-available timestamp is included in every
    quota-related error message. `schedule run` catches
    `QuotaExhaustedError` and reschedules the affected
    job to the next UTC midnight; the runner's
    `QuotaExhaustedError` subclasses the backend-level
    one so a single `except` catches both.

  - **Structured logging.** New `postcards.log` module
    owns the log-level mapping (`-v` / `-vv` / `-vvv` →
    INFO / DEBUG / TRACE), the TRACE custom level
    (numeric 5, registered at import time), and the
    standard + brief format strings. Every dispatch
    step in the schedule runner emits an
    INFO/DEBUG/WARN line so `schedule run -vv` shows
    exactly where a job got stuck.

  - **Actionable error messages.** New
    `postcards.backend.messages.translate` and the
    CLI-facing `postcards.cli.backend_errors.raise_for_backend_error`
    translate every typed backend exception into a
    `(message, exit_code)` pair. The message ends with
    a hint about the next step (the credential env
    vars, `postcards quota --wait`, `--backend=mock`,
    `--verbose`); the exit code is 1 for permanent
    failures and 75 (`EX_TEMPFAIL`) for transient
    failures so a cron job can distinguish "retry
    later" from "fix the config".

  New `postcards/backend/messages.py` and
  `postcards/cli/backend_errors.py` modules; new
  `MockBackend` failure-injection knobs
  (`transient_errors_remaining`, `send_exception`) drive
  the retry path from tests.

  Test count: 768 → 857 (+89). New tests live in
  `tests/test_log.py` (12 unit tests on the log module),
  `tests/test_retry.py` (18 on the retry helper),
  `tests/test_backend_exceptions.py` (11 on the typed
  exceptions), `tests/test_backend_errors_cli.py`
  (19 on the error translator), plus additions to
  `tests/test_backend_integration.py` (4 retry-driven
  integration tests), `tests/test_schedule_runner.py`
  (3 tests on actionable error messages and
  structured logging), and `tests/test_typer_cli.py`
  (5 tests on the new `quota` flags).

- **M5 — SwissID login diagnostics + keyring.** Two
  user-facing surfaces for the credential and
  authentication flow, both exposed as Typer
  sub-commands:

  - **`postcards doctor`** runs five checks (config
    file, credentials, keyring, connectivity, mock-login
    smoke test) and prints a tabular report. A failure
    on any check exits 1 with a one-line summary and a
    next-step hint. The command never authenticates
    against the live Swiss Post endpoint — the
    connectivity check probes the consumer landing
    page, the mock-login check drives
    :class:`MockBackend`. See `docs/DOCTOR.md`.

  - **`postcards keyring {set,get,delete,list,status}`**
    wraps the OS keyring as a first-class credential
    source. `set` writes a password for a username,
    `get` reports presence (never the plaintext), and
    `delete` is idempotent. The `list` subcommand
    prints a one-line explanation — the OS keyring API
    intentionally does not expose a list-entries call.
    See `docs/KEYRING.md`.

  - **Precise auth-failure messages.** The
    `postcards.backend.messages.translate` translator
    inspects the `AuthenticationError` message for the
    upstream's 2FA / anomaly-detection phrasings and
    emits a scenario-specific next-step hint (open
    https://account.post.ch/ in a browser, complete the
    2FA prompt, or confirm the device). Every
    authentication failure now also points the user at
    `postcards doctor` for a full diagnosis.

  New modules: `postcards/config/keyring.py`
  (`KeyringStore`, `KeyringStatus`, `KeyringError`),
  `postcards/cli/commands/keyring.py`,
  `postcards/cli/commands/doctor.py`. The `keyring`
  package is now a hard runtime dependency (>=24). New
  test files: `tests/test_config_keyring.py` (14
  unit tests on the `KeyringStore` wrapper),
  `tests/test_keyring_cli.py` (15 CLI tests against a
  hand-rolled in-memory backend), `tests/test_doctor_cli.py`
  (25 doctor tests covering every check, every exit
  code, and the end-to-end CLI), and 7 new tests in
  `tests/test_backend_errors_cli.py` for the 2FA /
  anomaly-detection translator branches.

- **M4 — batch send + scheduling.** Multi-recipient
  dispatch plus a local send queue with delayed and
  recurring jobs:

  - `postcards batch` — send one postcard to each of many
    recipients. Recipient sources:

    - `--to-many NAME1,NAME2,...` (inline list)
    - `--to-all-recipients` (every recipient in the address
      book)
    - `--manifest PATH` (CSV or YAML; `.yaml`/`.yml`
      → YAML, otherwise CSV)

    Per-recipient overrides on a manifest row (`picture`,
    `message`, `message_template`, `sender`, `var`) win over
    the shared CLI flags. Per-recipient failures are
    surfaced in a summary; `--stop-on-error` aborts on the
    first failure. Reuses the same send plumbing as
    `postcards send` so every input the latter accepts is
    also accepted by `batch`.

  - `postcards schedule {add,list,show,remove,retry,run}`
    — manage the local send queue.

    - `add` — queue a one-shot (`--at ISO-TIMESTAMP`) or
      a recurring (`--recurring every:Nd` /
      `--recurring weekly:mon[,tue,...]`) job.
    - `list` / `show` / `remove` — local queue
      introspection.
    - `retry` — reset a `failed` job back to `pending`.
    - `run` — dispatch every due job against the
      configured backend. Quota-exhausted jobs are
      rescheduled to the next UTC midnight; failing jobs
      stay in the queue and surface in `last_error`.
      Cron-friendly via `--quiet`.

  New package `postcards.schedule/` with value-type
  models (`ScheduledJob`, `JobStatus`, `RecurrenceRule`,
  `ScheduleBook`), a `Clock` protocol with `SystemClock` +
  `FakeClock` for testable time travel, atomic JSON
  persistence under
  `$XDG_DATA_HOME/postcards/schedule.json`, and a runner
  that walks the book, logs into the backend, checks
  quota, and dispatches via the modern `PostcardBackend`
  protocol. The runner is unit-tested against
  `MockBackend` + `FakeClock` — no live Swiss Post call,
  no real time travel.

  See `docs/BATCH.md` and `docs/SCHEDULE.md` for the
  user-facing guides.

  Tests: 73 schedule-model tests (recurrence parsing and
  advance semantics, ScheduledJob value-type discipline,
  ScheduleBook JSON round-trip, Clock / FakeClock);
  15 schedule-storage tests (atomic write, missing-file,
  schema validation); 18 schedule-runner tests
  (one-shot, recurring every-N-days, weekly, quota
  exhaustion, error paths, dry-run, multi-job walks,
  value-type discipline, picture loading); 18 batch-CLI
  integration tests; 21 schedule-CLI integration tests.
  Local gate: ruff + ruff-format + mypy + 768/768 pytest
  (up from 623 after M4 address book / templates).

- **M4 — address book + message templates.** A persistent
  per-user store under `$XDG_DATA_HOME/postcards/` (overridable
  via `POSTCARDS_DATA_DIR`) holds named recipients / senders
  and reusable message templates with `$name` / `${name}`
  substitution. New command groups:

  - `postcards addresses {add,list,show,update,remove}` —
    manage the address book.
  - `postcards templates {add,list,show,update,render,remove}`
    — manage and render templates; `render` substitutes
    `--var KEY=VALUE` pairs with strict missing-key
    semantics.

  `postcards send` gains three new options that pull from
  these stores:

  - `--to NAME` — recipient from the address book
    (recipient category).
  - `--sender NAME` — sender from the address book
    (sender category).
  - `--message-template NAME` — render a template with
    `--var KEY=VALUE` substitutions (repeatable).

  The new options are layered on top of the existing
  config-file flow rather than replacing it: accounts still
  come from `-c config.json`, and the recipient / sender /
  message are resolved in-memory before delegating to
  `Postcards.do_command_send`. A tiny refactor adds
  optional `config_dict` / `accounts_dict` kwargs to
  `do_command_send` so the on-disk path stays bit-identical
  for existing callers.

  New package `postcards.addressbook/` with value-type
  models (`AddressBook`, `AddressBookEntry`,
  `AddressCategory`, `MessageTemplate`, `TemplateBook`),
  XDG-aware path resolution, atomic JSON persistence (sibling
  temp file + `os.replace` + `fsync`), and a stdlib-based
  template renderer.

  See `docs/ADDRESS_BOOK.md` for the user-facing guide.

  Tests: 105 unit tests across models, paths, storage, and
  variable substitution; 48 CLI tests across the new command
  groups; 13 integration tests that drive the full CLI
  stack (Typer → `do_command_send` → mocked
  `send_free_card`) with address-book and template-book
  entries as inputs. Local gate: ruff + ruff-format + mypy +
  623/623 pytest (up from 457 in M3).

- **M0 — toolchain + CI + constitution.** Replaces `setup.py` with a
  modern `pyproject.toml` (PEP 621, hatchling build backend) targeting
  Python 3.12 and 3.13. Adds and configures `ruff` (lint + format),
  `mypy`, and `pytest` + `pytest-cov`. Introduces `scripts/check.sh` as
  the single-command gate (ruff lint, ruff format check, mypy, pytest)
  and `.github/workflows/ci.yml` running it on a `py3.12/3.13` matrix
  for every push and pull request. Adds `docs/CONSTITUTION.md`
  codifying the project's invariants (unofficial Swiss Post integration,
  no live auth in CI, no committed secrets, the gate, code style). Adds
  a trivial smoke test in `tests/test_version.py` that proves the
  toolchain is wired up end to end. The next milestone, M1, removes
  the M0 lint/type exemptions on the legacy package and brings the
  runtime dependencies up to current releases.

- **M1 — dependency modernization + test suite.** Replaces the upstream
  `postcard-creator==2.2` PyPI package (which transitively depends on
  `Js2Py`/`pyjsparser`, `cookies`, `python-resize-image`, `pytz`,
  `tzlocal`, `six`, `pypexels`, `nltk`, and 2017-era pinned
  `certifi`/`urllib3`/`idna` — none of which install cleanly on Python
  3.12 / 3.13) with an in-tree vendored shim at
  `postcards._vendor.postcard_creator` that exposes the same public
  names (`Token`, `PostcardCreator`, `Postcard`, `Recipient`, `Sender`,
  `PostcardCreatorException`) and constructor signatures. Network
  methods on the shim (`Token.fetch_token`,
  `Token.has_valid_credentials`, `PostcardCreator.send_free_card`,
  `PostcardCreator.has_free_postcard`, `PostcardCreator.get_quota`)
  raise `NotImplementedError` so that any accidental live call is
  caught immediately. Drops `Js2Py`/`pyjsparser` by rewriting
  `plugin_random.random_search_term` in pure Python, drops `nltk` by
  replacing the POS-tagger noun extractor in
  `plugin_chuck_norris` with a regex tokenizer + stoplist, drops
  `pypexels` in favour of `picsum.photos` (no API key required) in
  `plugin_pexels.util.pexels`, and drops `pkg_resources` in favour of
  `importlib.resources`. Removes `requirements.txt` and
  `requirements-dev.txt`; `pyproject.toml` is now the single source of
  truth for both runtime and dev dependencies. Removes the M0
  per-file-ignores / mypy override / format-exclude on the legacy
  `postcards/` package; brings the package up to the same ruff + mypy
  + format baseline as the rest of the codebase. Adds a real test
  suite covering the shim, the CLI surface, the modernized helpers,
  and an end-to-end send-flow integration test against a MOCKED Swiss
  Post backend (`tests/test_send_integration.py`) — the mock is the
  single source of truth for the backend's contract; the live API is
  never exercised in CI.

- **M1 — backend interface + SwissID consumer backend.** Introduces
  the typed `PostcardBackend` Protocol every Swiss Post network call
  must go through (`docs/CONSTITUTION.md` §1.1). The new
  `postcards.backend` package ships:
  - `base.PostcardBackend` — runtime-checkable Protocol with the four
    operations the consumer flow exposes (login, quota, preview,
    send), plus typed payloads (`AddressSpec`, `PostcardSpec`,
    `QuotaInfo`, `PreviewInfo`, `SendResult`) as frozen dataclasses.
  - `mock.MockBackend` — in-memory implementation that records every
    login / preview / send. It is the single source of truth for the
    backend's contract in tests and the `POSTCARDS_BACKEND=mock`
    fallback for developers exercising the CLI surface.
  - `swissid.SwissIdConsumerBackend` — production wrapper around the
    vendored `postcard_creator` shim; translates between the
    protocol's dataclasses and the shim's `Sender` / `Recipient` /
    `Postcard` types.
  - `registry.select_backend` — selection driven by the
    `POSTCARDS_BACKEND` env var or the `backend` field of the config
    file; raises `BackendNotAvailableError` on typos and lists the
    valid names in the message.
  - `postcards.config.ConfigLayer` — typed loader that resolves
    credentials via the constitution's precedence order (CLI >
    `POSTCARDS_USERNAME` + `POSTCARDS_PASSWORD` > OS keyring under
    service `postcards` > gitignored config file). Every
    `AccountConfig` carries a `source` field that records which path
    resolved it for diagnostics.

  No CLI behaviour change yet — the legacy send flow still uses the
  shim directly via `_create_pcc_wrappers`. Routing
  `do_command_send` through `select_backend()` lands in a later
  milestone so the new abstraction becomes the only network path.

  Test count: 73 → 127 (+54). Total coverage: 43% → 73%.
  `postcards/backend/*` is at 94–100% coverage; `postcards/config/*`
  is at 98%. New tests live in `tests/test_backend_selection.py`,
  `tests/test_config_layer.py`, and `tests/test_backend_integration.py`.

- **M1 — A6 image pipeline + postcard model.** Adds the typed
  user-facing domain models the CLI builds before handing a card
  to a backend:

  * `postcards.models` — `Recipient`, `Sender` (aliases over
    `AddressSpec` so call sites read like the upstream Swiss Post
    API), `Message` (frozen dataclass, ≤ 500 chars, with a
    `from_text` builder), and `Postcard` (the high-level model with
    a `from_image` classmethod that runs the pipeline).
  * `postcards.image` — the A6 image pipeline:
    - `dimensions.py` — `Orientation` (StrEnum), `A6_LANDSCAPE_*` /
      `A6_PORTRAIT_*` pixel sizes (1500×1062 / 1062×1500, the
      exact dimensions the Swiss Postcard Creator accepts),
      `A6_ASPECT_RATIO` (148/105 ≈ √2), `SUPPORTED_FORMATS`
      (`{"JPEG", "PNG"}`).
    - `pipeline.py` — `load_image` (path / bytes / `BinaryIO`),
      `normalize_orientation` (EXIF transpose, preserves format),
      `validate_format`, `detect_orientation`,
      `center_crop_to_aspect`, `resize_to_a6` (LANCZOS,
      RGBA → white flatten, grayscale → RGB), `encode_jpeg`,
      and the convenience wrapper `prepare_postcard_image`. All
      public entry points raise `ImageError` on failure so callers
      catch a single exception type.

  The protocol's `PostcardBackend.send` / `.preview` now accept the
  user-facing `Postcard` (carrying processed JPEG **bytes**) instead
  of the protocol-level `PostcardSpec` (carrying a file-like
  `BinaryIO`). `SwissIdConsumerBackend.send` translates the
  `Postcard` → shim types internally and wraps the picture bytes
  in `io.BytesIO` for the shim. `PostcardSpec` remains as the
  internal transport payload (frozen dataclass with
  `BinaryIO | None`) for callers that want to short-circuit the
  user-facing layer.

  Test count: 127 → 205 (+78). Total coverage: 73% → 77%.
  `postcards/image/*` and `postcards/models/*` are at 100% coverage.
  New tests live in `tests/test_image_pipeline.py` (46 tests),
  `tests/test_postcard_model.py` (32 tests), and an additional
  end-to-end pipeline → Postcard → MockBackend integration test in
  `tests/test_backend_integration.py`.

- **M2 — Typer-based CLI.** Migrates the user-facing
  ``postcards`` console script from the legacy ``argparse`` parser
  in :mod:`postcards.postcards` to a Typer-based command tree
  under the new :mod:`postcards.cli` package. The new commands are:

  * ``postcards send`` — send a card. Honours ``--dry-run`` and
    ``--all-accounts``; falls back to the active account from the
    config file when ``--username`` / ``--password`` are not
    passed.
  * ``postcards preview`` — show what ``send`` would do, without
    actually sending. Same arguments as ``send``. Accepts
    ``--output PATH`` (a ``.png`` / ``.jpg`` / ``.jpeg`` / ``.pdf``
    path) to render the would-be card to a local file via the new
    :mod:`postcards.render` module — no network call, no SwissID
    login, no quota consumption. URL pictures are rejected so the
    preview stays a strict offline dry-run.
  * ``postcards generate`` — write the bundled starter config to
    a given path. Refuses to clobber an existing file unless
    ``--force`` is passed.
  * ``postcards config {init,show,set}`` — manage the config
    file. ``init`` is an alias for ``generate``; ``show`` prints
    the resolved config (passwords masked by default; opt in via
    ``--no-redact``); ``set`` mutates a dotted key path
    (``recipient.city``, ``accounts.0.username``, ...).
  * ``postcards accounts {add,list,use}`` — manage the
    multi-account list. ``add`` appends a username / password
    pair (and refuses to create a duplicate); ``list`` shows
    the accounts with passwords masked; ``use`` sets the
    ``active_account`` field.
  * ``postcards quota`` — print the free-card quota for an
    account. Honours ``--backend mock`` for offline exercises
    of the code path.
  * ``postcards status`` — print the resolved CLI configuration
    (config path, backend, account, version).
  * ``postcards encrypt`` / ``postcards decrypt`` — the
    credential crypto commands, migrated from the legacy
    ``argparse`` form.
  * ``postcards legacy run`` — escape hatch that delegates to
    the pre-M2 ``argparse`` parser. The dedicated plugin entry
    points (``postcards-folder``, ``postcards-yaml``,
    ``postcards-pexels``, ``postcards-random``,
    ``postcards-chuck-norris``) keep their argparse-based
    implementation and are unaffected by the M2 migration.

  All subcommands render rich ``--help`` with grouped options
  and use the constitution's ``raise_cli_error`` helper for
  user-facing errors (typed ``NoReturn`` so ``str | None``
  arguments are narrowed to ``str`` by mypy after a guard).
  The production entry point :func:`postcards.cli.main.main`
  calls :data:`postcards.cli.app.app` directly (not the
  ``typer.testing.CliRunner``) so help text and error messages
  reach the real stdout / stderr. The test entry point
  :func:`postcards.cli.runner.run` continues to wrap
  ``typer.testing.CliRunner`` for hermetic test assertions.

  Test count: 205 → 239 (+34). The new tests live in
  ``tests/test_typer_cli.py`` and cover: every subcommand's
  ``--help`` (10 subcommands), top-level help listing all
  subcommands, ``postcards`` with no args, ``--version``,
  ``send`` validation (requires ``--picture`` or ``--message``,
  ``--dry-run`` flow against a mocked Swiss Post shim),
  ``preview`` end-to-end (mocked shim), ``generate`` clobber
  protection, ``config init`` / ``show`` / ``set`` (including
  password redaction defaults and list-index paths),
  ``accounts add`` / ``list`` / ``use`` (including duplicate
  detection and active-account marking), ``status`` output,
  ``quota --backend mock`` plus the missing-username error
  path, ``encrypt`` / ``decrypt`` round-trip and wrong-key
  behavior, the ``legacy`` subcommand, the ``CLIError`` class
  contract, ``-vv`` logging configuration, and the
  :func:`main` entry point.

- **M2 — offline preview rendering.** Adds the
  :mod:`postcards.render` package and a ``--output`` option on
  ``postcards preview`` so the user can render the would-be
  card (front image + back with message and addresses) to a
  local PNG / JPEG / PDF without contacting Swiss Post. The
  renderer runs the A6 image pipeline on the supplied picture
  so the rendered front is the exact JPEG bytes the backend
  would transmit. The back panel paints the recipient block
  at the top of the right half and the sender block at the
  bottom (with a ``From:`` label) using a word-wrapped message
  on the left half; HTML in the message is normalised to
  plain text with ``<br>`` becoming a newline. URL pictures
  are rejected so the preview stays a strict offline
  dry-run; unsupported output extensions raise
  :class:`postcards.render.RenderError`. The renderer is
  dependency-light (Pillow only — no network, no SwissID
  code, no quota consumption).

  Test count: 239 → 269 (+30). The new tests live in
  ``tests/test_postcard_render.py`` (25 unit tests on the
  renderer: front / back dimensions, placeholder,
  picture-byte decoding, garbage-bytes error, file
  extension dispatch, PNG / JPEG / PDF output sanity,
  parent-directory creation, address formatting, HTML
  normalisation, word-wrap edge cases) and
  ``tests/test_typer_cli.py`` (5 CLI integration tests
  on the ``--output`` flow: PNG / PDF writes, text-only
  cards, URL rejection, unsupported-extension rejection).

- **M3 — modern plugin architecture.** Replaces the
  inheritance-based plugin system (each plugin subclassed
  ``postcards.postcards.Postcards`` and overrode
  ``get_img_and_text`` / ``build_plugin_subparser`` /
  ``can_handle_command``) with a small typed, registry-based
  API. The new public surface lives in ``postcards.plugins``:

  - ``Plugin`` (``Protocol``) — three methods (`configure`,
    `render`, `cli_help`) and two `ClassVar`s (`name`,
    `description`); `@runtime_checkable` so tests can use
    `isinstance`.
  - ``PluginResult`` — frozen dataclass carrying `image`
    (`BinaryIO`) + optional `message` (`str`) + optional
    `metadata`.
  - ``PluginContext`` — frozen dataclass carrying per-render
    `options` (`Mapping[str, Any]`) + scoped `logger`.
  - ``Registry`` — `name → plugin class` lookup with
    `importlib.metadata` entry-point discovery under the
    ``postcards.plugins`` group. The in-tree plugins
    (``folder``, ``folder_yaml``, ``pexels``,
    ``chuck_norris``) register themselves at import time
    and are also advertised via the entry-point group so
    third-party tools can enumerate them.
  - ``load_plugin(name, payload, registry=None)`` — build,
    configure, and return a ready-to-render plugin instance.
    Wraps plugin `configure` exceptions in `PluginConfigError`.
  - Typed exception hierarchy: ``PluginError`` and
    subclasses (``PluginNotFoundError``,
    ``PluginConfigError``, ``PluginRenderError``).

  The four in-tree plugins are ported to the new API:

  - ``folder`` — pick a random picture from a local
    directory. Supports a ``.priority/`` subdirectory that
    is sampled exclusively when populated, and an optional
    ``move=True`` that relocates the chosen picture into
    ``sent/``. Reads the file into memory and returns an
    in-memory `BytesIO` so callers do not manage a file
    handle.
  - ``folder_yaml`` — pick the first `(text, image)` pair
    from a YAML playlist; optionally truncate the document
    after picking and optionally move the picture.
  - ``pexels`` — fetch a random photo from
    `picsum.photos` via the legacy
    `postcards.plugin_pexels.util.pexels` helper. The
    `keyword` payload field becomes the picsum seed so
    different keywords land on different photos.
  - ``chuck_norris`` — pick a random joke from a bundled
    JSON dataset and fetch a matching picture. Supports
    `category` filtering and a `duplicate_file` exclusion
    list. The keyword extractor is a regex-based stopword
    filter (no `nltk`).

  - ``url`` — fetch a picture from a user-supplied
    `http://` or `https://` URL. Accepts optional
    custom `headers` (for `Authorization` etc.) and a
    per-request `timeout`. Network errors and HTTP
    4xx/5xx responses are surfaced as
    `PluginRenderError` so the CLI prints a clean
    message instead of a `requests` traceback.

  - ``local`` — deterministic round-robin picker over a
    local folder. The first render returns the
    alphabetically-first matching picture; subsequent
    renders advance through the sorted list and wrap to
    0. Supports an optional `pattern` glob (e.g.
    `landscape/*.jpg`) and an optional `cursor_file`
    that persists the round-robin position across
    processes (for cron-driven sends). Complements the
    `folder` plugin, which picks uniformly at random.

  - ``unsplash`` — fetch a random photo from the
    Unsplash API. Reads the access token from the
    environment variable
    `POSTCARDS_UNSPLASH_ACCESS_KEY` (no config-file
    fallback — secrets never live in the repo, see
    `docs/CONSTITUTION.md` §2). Supports an optional
    `query` (search term), `orientation`
    (`landscape`/`portrait`/`squarish`, default
    landscape), and `count` (1-30, picks one at
    random from the returned list). Issues two HTTP
    calls per render: the `/photos/random` lookup and
    the JPEG download — both with the default 30 s
    timeout.

  The new plugins are documented in
  `docs/WRITING_PLUGINS.md`, which walks through the
  protocol, the `PluginResult` return type, the
  configuration payload, the typed exception
  hierarchy, and the entry-point publishing protocol
  for third-party plugins. The document also lists
  every in-tree plugin as a real-world example.

  The ``send`` CLI subcommand and ``postcards.postcards.Postcards.do_command_send``
  pick up the new plugin path automatically: when
  ``config.json`` carries a ``payload.plugin`` field, the
  modern registry path runs; otherwise the legacy
  ``_is_plugin()`` branch (used by the `postcards-folder`,
  `postcards-yaml`, ... console scripts) is preserved for
  backward compatibility. CLI ``-m`` / ``-p`` options win
  over the plugin's `message` / `image`.

  A new ``postcards plugins list`` Typer subcommand
  enumerates the registered plugins (in-tree +
  entry-point).

  The legacy ``postcards.plugin_random`` Bing-image-scraper
  plugin is **removed**. Bing's image-search HTML format
  dropped the `murl` JSON attribute on `<a class="iusc">`
  elements in 2023, so the plugin's scraper returns zero
  results on every request. The `pexels` plugin covers the
  "I just want a random picture" use case. The
  ``postcards-random`` console script, the
  ``postcards.plugin_random.*`` packages, the bundled
  ``random.html``/``random.js``/``random_search_term``
  assets, and the ``tests/test_random_search_term.py``
  tests are all deleted.

  ``Postcards._read_picture`` now reads the picture into
  memory and returns a `BytesIO` instead of an open file
  handle. The legacy behavior leaked file descriptors in
  callers that forgot to close the handle; the M2
  integration tests started triggering `ResourceWarning`
  errors under ``filterwarnings = "error"`` after the
  legacy `_is_plugin()` path was retired.

  Test count: 269 → 454 (+185). The new tests live in
  ``tests/test_plugin_base.py`` (10 unit tests on the
  `Plugin` Protocol / `PluginResult` dataclass /
  `PluginBase` helper), ``tests/test_plugin_errors.py`` (5
  exception tests), ``tests/test_plugin_registry.py`` (16
  registry tests including entry-point discovery),
  ``tests/test_plugin_loader.py`` (10 loader tests),
  ``tests/test_plugin_folder.py`` (15 tests),
  ``tests/test_plugin_folder_yaml.py`` (12 tests),
  ``tests/test_plugin_pexels.py`` (9 tests, network
  mocked via `urllib.request.urlopen`),
  ``tests/test_plugin_chuck_norris.py`` (19 tests, network
  mocked, keyword extractor unit tests),
  ``tests/test_plugin_url.py`` (21 tests, network
  mocked via `requests.get`),
  ``tests/test_plugin_local.py`` (24 tests, round-robin
  cursor + glob pattern + cursor_file persistence),
  ``tests/test_plugin_unsplash.py`` (37 tests, network
  mocked — covers the two-step API + download flow,
  env-var config, error envelopes), and
  ``tests/test_plugin_send_integration.py`` (7
  end-to-end tests that drive ``do_command_send`` through
  the mocked upstream `Token` / `PostcardCreator` with a
  config-driven plugin producing the picture).

### Notes

- The gate installs the package with `pip install -e ".[dev]"` (M1
  modernized the runtime dep set, so `--no-deps` is no longer
  required) and falls back to `uv pip install --python X` when pip is
  missing from the venv (the case for `uv venv`-managed venvs in CI).