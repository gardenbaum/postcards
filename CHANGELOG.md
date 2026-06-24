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

### Notes

- The `pyproject.toml` lists the runtime dependencies exactly as the
  legacy `requirements.txt` did; the runtime dep set is intentionally
  not modernized in M0 (see the M1 card).
- The gate installs the package with `pip install --no-deps -e ".[dev]"`
  in M0 because the legacy runtime deps (`postcard-creator==2.2`,
  `Js2Py==0.50`, etc.) do not install cleanly on Python 3.12 / 3.13.
  M1 replaces them.

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
