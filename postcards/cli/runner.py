"""CLI entry point and shared test runner.

This module owns the ``main()`` function the ``postcards`` console
script calls, plus a small :func:`run` helper that wraps
:class:`typer.testing.CliRunner` so tests have a single import to
use.

Why a dedicated runner
----------------------

:class:`typer.testing.CliRunner` is verbose to instantiate (it
needs a ``mix_stderr=False`` flag, a ``color=False`` flag for
deterministic output in tests, and a fresh instance per test
method to keep state isolated). The tests in
``tests/test_typer_cli.py`` would otherwise duplicate that
boilerplate dozens of times. A single :func:`run` helper keeps
the call site short and lets us swap the runner out in the
future (e.g. for ``click.testing.CliRunner`` directly) without
touching every test.

Console-script entry point
--------------------------

``pyproject.toml`` declares::

    postcards = "postcards.cli.main:main"

``main()`` is the function :mod:`setuptools` wires up as the
``postcards`` console script. It calls :func:`run` with ``None``
so the runner uses :data:`sys.argv` (matching what a user
types in their shell).
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

import typer
from typer.testing import CliRunner

from postcards.cli.app import app
from postcards.cli.errors import CLIError


def run(argv: Sequence[str] | None = None) -> typer.testing.Result:
    """Invoke :data:`postcards.cli.app` and return the test result.

    Parameters
    ----------
    argv:
        Argument vector to use. ``None`` means "use ``sys.argv[1:]``"
        so the console-script entry point behaves like a normal
        CLI. Tests pass an explicit list to drive the CLI without
        touching the process argv.

    Returns
    -------
    typer.testing.Result
        The :class:`typer.testing.Result` from the underlying
        :class:`typer.testing.CliRunner.invoke` call. Tests inspect
        ``result.exit_code``, ``result.stdout``, and
        ``result.exception`` to assert behaviour.
    """
    runner = CliRunner()
    cli_args: Sequence[str] = list(sys.argv[1:]) if argv is None else list(argv)
    return runner.invoke(app, cli_args, catch_exceptions=False)


def main() -> None:
    """Entry point for the ``postcards`` console script.

    Uses :func:`run` so the test path and the production path
    share their setup logic. The function never returns; it
    terminates the process via :class:`typer.Exit` or a
    :class:`SystemExit` from Click's machinery.
    """
    try:
        result = run()
    except CLIError as exc:
        typer.echo(f"error: {exc.message}", err=True)
        raise SystemExit(exc.exit_code) from exc
    except SystemExit:
        # ``typer.Exit`` and Click's "bad usage" path both raise
        # ``SystemExit``; pass the code through.
        raise
    # When ``catch_exceptions=False`` and the command returned
    # normally, the runner still produces a result with the
    # captured exit code. ``typer.Exit(code=0)`` from a command
    # body is folded into ``result.exit_code``; commands that
    # complete without an explicit ``typer.Exit`` produce
    # ``exit_code=0`` too.
    if result.exit_code != 0:
        raise SystemExit(result.exit_code)


if __name__ == "__main__":
    main()


__all__ = ["main", "run"]
