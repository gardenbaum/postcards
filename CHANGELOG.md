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

### Notes

- The gate installs the package with `pip install -e ".[dev]"` (M1
  modernized the runtime dep set, so `--no-deps` is no longer
  required) and falls back to `uv pip install --python X` when pip is
  missing from the venv (the case for `uv venv`-managed venvs in CI).