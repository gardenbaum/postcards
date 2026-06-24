"""User-facing CLI error type.

A :class:`CLIError` is the standard way a Typer command surfaces a
non-zero exit code with a human-readable message. The top-level
Typer callback in :mod:`postcards.cli.app` catches it and exits
with ``exit_code``.

Catching :class:`CLIError` at a coarse level keeps the individual
command callbacks free of repeated ``raise typer.Exit(...)`` /
``typer.echo(...)`` boilerplate; the commands just ``raise
CLIError("...")`` and the app-level callback does the rest.

The class is a :class:`RuntimeError` subclass (not :class:`Exception`)
because ``typer.Exit`` is already a :class:`click.exceptions.Exit`
subclass — the CLI error path wants to share an interface with
that machinery but be raised from inside business logic, where
``Exception`` would be too broad.
"""

from __future__ import annotations


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


__all__ = ["CLIError"]
