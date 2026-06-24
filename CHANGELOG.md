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
