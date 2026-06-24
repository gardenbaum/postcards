"""Typer-based CLI for :mod:`postcards`.

M2 migrated the user-facing ``postcards`` console script from the
legacy ``argparse`` parser in :mod:`postcards.postcards` to a
Typer-based command tree under :mod:`postcards.cli`. The legacy
``Postcards`` class still does the work (it owns the
``do_command_send`` / ``do_command_generate`` / etc. methods that
the M1 tests cover); the new CLI just dispatches to it.

Public surface
--------------

* :data:`app` — the top-level :class:`typer.Typer` instance. Tests
  use :class:`typer.testing.CliRunner` against it; the ``postcards``
  console script defined in ``pyproject.toml`` points at
  :func:`postcards.cli.main:main` which invokes it.
* :class:`CLIError` — a :class:`typer.Exit`-compatible exception
  the command modules raise to surface a user-facing error with a
  non-zero exit code.
* :func:`run` — convenience wrapper that creates a
  :class:`typer.testing.CliRunner` and invokes :data:`app`. The CLI
  entry point uses this so the same code path runs in tests and at
  the console.

Why Typer
---------

The constitution (post-M2 §5) names Typer as the standard CLI
framework for new subcommands. Typer gives us:

* a single, declarative command tree (``send``, ``preview``,
  ``generate``, ``config``, ``accounts``, ``quota``, ``status``,
  ``encrypt`` / ``decrypt``, plus a ``legacy`` escape hatch for the
  plugin subcommands);
* rich ``--help`` output, free;
* a typed Python API for the command callbacks so mypy and the
  tests catch typos at definition time rather than at the user's
  terminal.

The plugin console scripts (``postcards-folder``, ``postcards-yaml``,
``postcards-pexels``, ``postcards-random``, ``postcards-chuck-norris``)
keep their ``argparse``-based implementation for now; M2 does not
migrate them.
"""

from __future__ import annotations

from postcards.cli.app import app
from postcards.cli.errors import CLIError
from postcards.cli.runner import run

__all__ = ["CLIError", "app", "run"]
