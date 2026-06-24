"""Top-level entry point for the ``postcards`` console script.

``pyproject.toml`` declares::

    postcards = "postcards.cli.main:main"

This module exists so the entry-point string resolves to a
``main`` function. The actual work lives in
:mod:`postcards.cli.runner`; this module is a one-line
forwarder that re-exports :func:`postcards.cli.runner.main`.

Why a separate module
---------------------

Splitting the entry-point target out of :mod:`runner` lets the
tests import the runner module without pulling the entry-point
name into the import graph. The runner is the testable
seam; the entry-point is a wiring concern.

Backward-compatible ``main(argv)`` signature
--------------------------------------------

The pre-M2 ``postcards.postcards.main(argv)`` accepted an
``argv`` positional argument (used by ``tests/test_cli_help.py``
and the legacy integration tests). Typer's
:class:`typer.testing.CliRunner` reads ``sys.argv`` rather
than the function's positional args, so the ``argv`` argument
is effectively a no-op today; we keep the parameter so the
existing test code that calls ``main(["postcards", "--help"])``
continues to work without modification.
"""

from __future__ import annotations

from collections.abc import Sequence

from postcards.cli.runner import main as _main

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> None:
    """Entry point for the ``postcards`` console script.

    ``argv`` is accepted for backward compatibility with the
    pre-M2 ``postcards.postcards.main`` signature; the Typer
    app reads :data:`sys.argv` instead, so the parameter is
    intentionally ignored. Future M2 callers should drop the
    argument.
    """
    _main()  # argv is intentionally unused — see docstring.
