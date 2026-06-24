"""User-facing CLI error type.

A :class:`CLIError` is the standard way a Typer command surfaces a
non-zero exit code with a human-readable message. The top-level
runner in :mod:`postcards.cli.runner` catches it and exits
with ``exit_code``.

Catching :class:`CLIError` at a coarse level keeps the individual
command callbacks free of repeated ``raise typer.Exit(...)`` /
``typer.echo(...)`` boilerplate; the commands just ``raise
CLIError("...")`` and the runner does the rest.

The class is a :class:`RuntimeError` subclass (not :class:`Exception`)
because ``typer.Exit`` is already a :class:`click.exceptions.Exit``
subclass — the CLI error path wants to share an interface with
that machinery but be raised from inside business logic, where
``Exception`` would be too broad.
"""

from __future__ import annotations

from typing import NoReturn

import typer

__all__ = ["CLIError", "raise_cli_error"]


class CLIError(RuntimeError):
    """Raised by CLI commands to surface a user-facing error.

    Parameters
    ----------
    message:
        The message printed to ``stderr`` (or stdout in legacy
        tests that capture stdout).
    exit_code:
        Exit code to use. Defaults to ``1`` — the conventional
        "general error" code. Tests can pass ``2`` to mimic
        Click/Typer's usage-error exit.
    """

    def __init__(self, message: str, *, exit_code: int = 1) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


def raise_cli_error(message: str, *, exit_code: int = 1) -> NoReturn:
    """Print ``message`` to stderr and raise :class:`typer.Exit`.

    Click / Typer's :class:`typer.Exit` is the standard way to
    exit a Click-based CLI with a non-zero code. We prefer it
    over :class:`CLIError` (the typed wrapper) for the actual
    exit because:

    * Click catches :class:`typer.Exit` automatically and records
      the exit code in the test result.
    * The exception type makes the intent obvious at the call
      site ("this is a user-facing error, exit now").

    The :class:`CLIError` class still exists for callers that
    want a typed exception (e.g. internal helpers) — they can
    ``raise CLIError(...)`` and the
    :func:`postcards.cli.runner.run` runner will catch and
    convert it to a :class:`typer.Exit`.

    The return type is :data:`typing.NoReturn` so callers can
    rely on mypy to narrow ``str | None`` arguments to ``str``
    after the guard. Click's :class:`typer.Exit` is a
    :class:`SystemExit` subclass so the function literally
    never returns.
    """
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(code=exit_code)
