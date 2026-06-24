"""CLI entry point and shared test runner.

This module owns the ``main()`` function the ``postcards`` console
script calls, plus a small :func:`run` helper that wraps
:class:`typer.testing.CliRunner` so tests have a single import to
use.

Why a separate test runner
--------------------------

:class:`typer.testing.CliRunner` is the supported way to drive a
Typer app from a test. It captures stdout/stderr into a
:class:`typer.testing.Result` so assertions can inspect
``result.output`` and ``result.exit_code`` without touching the
real terminal. The production path (:func:`main`) MUST NOT use
this helper — calling the app via :class:`CliRunner` would
suppress the help text and the user's error output, because
the runner captures into a buffer instead of writing to the
real :data:`sys.stdout`.

Console-script entry point
--------------------------

``pyproject.toml`` declares::

    postcards = "postcards.cli.main:main"

``main()`` is the function :mod:`setuptools` wires up as the
``postcards`` console script. It calls :data:`app` directly,
which in turn invokes Click's standard :func:`click.testing.CliRunner.invoke`
machinery. Click catches :class:`typer.Exit` and any
:class:`SystemExit` raised by user code and turns them into
the right process exit code, so :func:`main` itself does not
need to do anything other than call the app.

Error handling
--------------

Most command bodies call :func:`postcards.cli.errors.raise_cli_error`
which raises :class:`typer.Exit` directly. Click catches that
and records the right exit code; for the production path
the user sees the message on stderr before the process exits.
The :class:`postcards.cli.errors.CLIError` class is reserved
for internal helpers that want a typed exception — the
:func:`main` wrapper does not catch it (it is not raised on
the production path today) but the type stays available for
future use.
"""

from __future__ import annotations

from collections.abc import Sequence

import typer
from typer.testing import CliRunner

from postcards.cli.app import app

__all__ = ["main", "run"]


def run(argv: Sequence[str] | None = None) -> typer.testing.Result:
    """Invoke :data:`postcards.cli.app` and return the test result.

    Parameters
    ----------
    argv:
        Argument vector to use. ``None`` means "use ``sys.argv[1:]``"
        so the production entry point behaves like a normal
        CLI. Tests pass an explicit list to drive the CLI without
        touching the process argv.

    Returns
    -------
    typer.testing.Result
        The :class:`typer.testing.Result` from the underlying
        :class:`typer.testing.CliRunner.invoke` call. Tests inspect
        ``result.exit_code`` and ``result.output`` to assert
        behaviour. The default ``catch_exceptions=True`` is
        used so a :class:`typer.Exit` raised inside a command
        body becomes a clean exit code in the result.
    """
    runner = CliRunner()
    cli_args: Sequence[str] = list(argv) if argv is not None else []
    return runner.invoke(app, cli_args)


def main() -> None:
    """Entry point for the ``postcards`` console script.

    Calls :data:`postcards.cli.app.app` directly. Click's
    machinery catches :class:`typer.Exit` (raised by
    :func:`postcards.cli.errors.raise_cli_error` and the
    ``--version`` / ``--help`` callbacks) and translates it
    to the right process exit code, so :func:`main` does not
    return — it terminates via :class:`SystemExit`.
    """
    app()


if __name__ == "__main__":
    main()
