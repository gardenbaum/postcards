# Releasing `postcards`

This document covers the maintainer workflow for cutting a release.
The M6 milestone makes the package publish-ready (wheel builds,
`pipx install .` works, classifiers + URLs complete), but **does not
publish to PyPI** — that step needs the maintainer's PyPI token and
must be triggered explicitly.

## Versioning

The project follows [Semantic Versioning](https://semver.org/) with
one concession to the upstream: the package wraps an **unofficial**
consumer API (see [`CONSTITUTION.md` §1](CONSTITUTION.md#1-the-swiss-post-integration-is-unofficial)),
so a minor upstream breakage is a `patch` for us, not a `minor`.

- **MAJOR** — Breaking CLI / config changes. The 2.x → 3.0 jump
  (M0–M5) was MAJOR because the dependency set, the entry points,
  and the runtime require-python all changed.
- **MINOR** — New features that are backwards-compatible (new
  subcommand, new plugin, new env var). The current 3.x series is
  free to grow here.
- **PATCH** — Bug fixes, doc updates, dependency upgrades, upstream
  workarounds.

The canonical version is **`postcards/__init__.py`**:

```python
__version__ = "3.0.0"
```

`pyproject.toml` declares `version` as a hatchling dynamic field and
reads it from `postcards/__init__.py` via `[tool.hatch.version]`, so
the wheel metadata cannot drift from the runtime constant.

## Cutting a release

The release process is intentionally small. There are five steps:

### 1. Open a "release prep" card (optional)

If the release needs code changes, open a kanban card under the
milestone you are shipping. If the release is purely a docs /
CHANGELOG polish, skip this step.

### 2. Move `[Unreleased]` to a dated version section in CHANGELOG.md

Edit `CHANGELOG.md`. The top of the file currently looks like:

```
## [Unreleased]

### Added

- **M6 — packaging, distribution, docs overhaul.** ...
```

On release day, replace `[Unreleased]` with `[X.Y.Z] - YYYY-MM-DD`
and start a fresh `[Unreleased]` block above it. Example:

```
## [Unreleased]

### Added

- nothing yet.

## [3.0.0] - 2026-06-25

### Added

- **M6 — packaging, distribution, docs overhaul.** ...
- **M5 — retries, quota awareness, structured logging.** ...
```

Keep the milestone-style subsections (`M5`, `M4`, …) inside the
release section — they preserve the historical record of which
milestone introduced which feature.

### 3. Bump `__version__`

Edit `postcards/__init__.py`:

```python
__version__ = "3.0.0"
```

Update `tests/test_version.py` so `EXPECTED_VERSION` matches.

### 4. Verify the gate

```sh
bash scripts/check.sh
```

All four checks (`ruff check`, `ruff format --check`, `mypy`,
`pytest`) must pass on **both** `python3.12` and `python3.13`. The
CI workflow at `.github/workflows/ci.yml` runs the matrix; do not
tag a release until CI is green.

### 5. Tag + push

```sh
git checkout main
git pull --ff-only
git tag -s -a v4.0.0 -m "postcards 4.0.0: NiceGUI WYSIWYG web app"
git push origin v4.0.0
```

The `-s` flag signs the tag with your GPG key; signed tags are the
PyPI / GitHub Release standard.

## Building distributable artifacts

The wheel and sdist are built locally and uploaded to PyPI via
`twine`. The repo's `pyproject.toml` uses `hatchling` as the build
backend — no `setup.py` is needed.

```sh
# 1. Clean any stale build artifacts.
rm -rf dist/ build/ *.egg-info

# 2. Build the wheel + sdist.
python -m pip install --upgrade build
python -m build

# 3. Inspect.
ls -la dist/
# → postcards-3.0.0-py3-none-any.whl
# → postcards-3.0.0.tar.gz
```

The sdist is what `pip install postcards[sdist-url]` consumes; the
wheel is what `pip install postcards` consumes by default.

## Publishing to PyPI

The package is **not** published to PyPI as part of the M6
milestone — that step needs the maintainer's PyPI token and is
intentionally out of scope for the kanban worker. The recipe below
is the maintainer's reference for when they decide to publish.

### Recommended: PyPI Trusted Publishing

[Trusted publishing](https://docs.pypi.org/trusted-publishers/) lets
a GitHub Actions workflow publish without a long-lived API token.
Add the workflow at `.github/workflows/publish.yml`:

```yaml
name: publish
on:
  push:
    tags: ['v*']
jobs:
  pypi:
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write   # for trusted publishing
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.13'
      - run: python -m pip install --upgrade build
      - run: python -m build
      - uses: pypa/gh-action-pypi-publish@release/v1
```

Register the workflow as a trusted publisher on PyPI once
(https://pypi.org/manage/account/publishing/). After that, every
`git push vX.Y.Z` publishes automatically — no token to leak.

### Manual: `twine`

If you prefer manual control:

```sh
# Upload to TestPyPI first.
twine upload --repository testpypi dist/*
# → smoke test the install:
pipx install --pypa testpypi postcards

# If that looks good, upload to the real index.
twine upload dist/*
```

`twine` reads the API token from `$TWINE_USERNAME` / `$TWINE_PASSWORD`
or, preferably, from a `~/.pypirc` with the token in the `pypi`
section:

```ini
[pypi]
username = __token__
password = pypi-AgENd...   # the token, starting with pypi-
```

Store the token in 1Password / `pass` / your secrets manager. Do
**not** check it into the repo or paste it into a chat.

## Cutting a GitHub Release

After `git push vX.Y.Z`:

1. Open https://github.com/gardenbaum/postcards/releases/new.
2. Pick the `vX.Y.Z` tag.
3. Title: `postcards X.Y.Z` (no `v` prefix).
4. Body: paste the relevant slice of `CHANGELOG.md`.
5. Attach the wheel + sdist from `dist/` (drag-and-drop into the
   release page). GitHub Releases hosts them for `pip install` from
   a direct URL.

## Per-milestone retrospective template

After each milestone (M0, M1, M2, …), the closing card should
include a one-paragraph retrospective in the handoff. The
postcards team uses this template:

```
## M<N> retrospective

- **Goal:** (one sentence from the card body)
- **What shipped:** (link to PR, list of new modules / commands)
- **Tests:** (X added, Y total, % coverage delta)
- **Decisions made:** (1-3 bullets)
- **Open questions for M<N+1>:** (1-3 bullets)
```

The retrospectives feed into the next milestone's body via the
parent-task handoff.

## See also

- [`docs/CONSTITUTION.md`](CONSTITUTION.md) — the project's policy
  root (gate, secrets, change management).
- [`docs/INSTALL.md`](INSTALL.md) — the user-facing install guide.
- [`scripts/check.sh`](../scripts/check.sh) — the local gate.
