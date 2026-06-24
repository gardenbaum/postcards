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

# Make the toolchain available. The legacy runtime deps (postcard-creator==2.2,
# Js2Py, etc.) do not install cleanly on Python 3.12/3.13, so we install the
# package with --no-deps and then add the dev tools as a separate step. M1
# replaces the runtime dep set.
if [[ "${SKIP_INSTALL:-0}" != "1" ]]; then
    echo ">> installing package (editable, no runtime deps) + dev tools"
    "$PYTHON" -m pip install --upgrade pip >/dev/null
    "$PYTHON" -m pip install --no-deps -e .
    "$PYTHON" -m pip install \
        "ruff>=0.5" \
        "mypy>=1.10" \
        "pytest>=8" \
        "pytest-cov>=5"
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
