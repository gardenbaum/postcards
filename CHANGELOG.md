# Changelog

All notable changes to `postcards` are documented in this file. The format
is loosely based on [Keep a Changelog](https://keepachangelog.com/), and
the project adheres to [Semantic Versioning](https://semver.org/) as far
as is practical for a wrapper around an unofficial upstream API.

## [Unreleased]

### Added

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
    actually sending. Same arguments as ``send``.
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

### Notes

- The gate installs the package with `pip install -e ".[dev]"` (M1
  modernized the runtime dep set, so `--no-deps` is no longer
  required) and falls back to `uv pip install --python X` when pip is
  missing from the venv (the case for `uv venv`-managed venvs in CI).