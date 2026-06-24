#!/usr/bin/env bash
# scripts/check.sh — the M0 gate.
#
# Runs the four tools that constitute the project quality gate:
#   1. ruff check   (lint)
#   2. ruff format  (formatting)
#   3. mypy         (static types)
#   4. pytest       (unit + integration tests with coverage)
#
# Every command MUST exit 0; the script exits non-zero on the first failure.
# Designed to be the single command invoked by CI (.github/workflows/ci.yml)
# and by developers locally: `bash scripts/check.sh`.
#
# Usage: scripts/check.sh [extra args passed to pytest]
#
# Environment:
#   PYTHON          Python interpreter to use (default: "python3").
#   SKIP_INSTALL=1  Skip the editable install step (useful when the venv
#                   is already provisioned, e.g. inside the CI cache step).

set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"

# Make the toolchain available. M1 modernized the runtime dep set so it
# installs cleanly on Python 3.12 / 3.13; the [dev] extra bundles ruff,
# mypy, pytest, and pytest-cov.
#
# pip discovery: prefer `python -m pip` (works for `python -m venv`-style
# venvs); if pip is missing from the venv (the case for `uv venv`-managed
# venvs in CI), fall back to `uv pip install`. This lets the same gate
# script run unchanged in both setups.
if [[ "${SKIP_INSTALL:-0}" != "1" ]]; then
    echo ">> installing package (editable, with [dev] extra) + dev tools"
    if "$PYTHON" -m pip --version >/dev/null 2>&1; then
        "$PYTHON" -m pip install --upgrade pip >/dev/null 2>&1 || true
        "$PYTHON" -m pip install -e ".[dev]"
    elif command -v uv >/dev/null 2>&1; then
        uv pip install --python "$PYTHON" --upgrade pip >/dev/null 2>&1 || true
        uv pip install --python "$PYTHON" -e ".[dev]"
    else
        echo "error: neither pip nor uv is available to install the dev tools" >&2
        exit 1
    fi
fi

echo
echo "========================================"
echo "  1/4  ruff check ."
echo "========================================"
"$PYTHON" -m ruff check .

echo
echo "========================================"
echo "  2/4  ruff format --check ."
echo "========================================"
"$PYTHON" -m ruff format --check .

echo
echo "========================================"
echo "  3/4  mypy ."
echo "========================================"
"$PYTHON" -m mypy .

echo
echo "========================================"
echo "  4/4  pytest -q"
echo "========================================"
"$PYTHON" -m pytest -q "$@"

echo
echo ">> all four checks passed."
