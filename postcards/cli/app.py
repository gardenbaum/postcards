"""Typer application — the top-level command tree for ``postcards``.

This module owns the :data:`app` instance imported by
:mod:`postcards.cli`. Each subcommand lives in
:mod:`postcards.cli.commands` and registers itself with ``app`` via
:meth:`typer.Typer.add_typer` or :meth:`typer.Typer.command`.

Global callback
---------------

:func:`_root_callback` runs before every subcommand. It is
responsible for:

* Setting the log level from ``-v`` / ``-vv`` / ``-vvv`` (mirrors
  the legacy ``argparse`` parser's ``--verbose`` count).
* Handling ``--version``.

Subcommands
-----------

The command modules are split by domain:

* :mod:`.commands.send`     — ``postcards send``
* :mod:`.commands.batch`    — ``postcards batch`` (M4)
* :mod:`.commands.preview`  — ``postcards preview``
* :mod:`.commands.generate` — ``postcards generate``
* :mod:`.commands.config`   — ``postcards config {init,show,set}``
* :mod:`.commands.accounts` — ``postcards accounts {add,list,use}``
* :mod:`.commands.addresses` — ``postcards addresses {add,list,show,update,remove}``
* :mod:`.commands.templates` — ``postcards templates {add,list,show,update,render,remove}``
* :mod:`.commands.schedule` — ``postcards schedule {add,list,show,remove,retry,run}`` (M4)
* :mod:`.commands.quota`    — ``postcards quota``
* :mod:`.commands.status`   — ``postcards status``
* :mod:`.commands.credentials` — ``postcards encrypt`` / ``postcards decrypt``
* :mod:`.commands.legacy`   — ``postcards legacy ...`` (escape hatch for
  the legacy plugin subcommands; see the module docstring for the
  reasoning)
"""

from __future__ import annotations

import sys

import typer

from postcards import __version__
from postcards.log import (
    DEFAULT_VERBOSITY_LEVELS,
    LOG_LEVEL_TRACE,
)
from postcards.log import configure as configure_logging

#: The top-level :class:`typer.Typer` instance.
#:
#: Tests invoke it via :class:`typer.testing.CliRunner`; the
#: ``postcards`` console script in ``pyproject.toml`` resolves to
#: :func:`postcards.cli.main:main`, which calls :func:`run` on
#: this app.
app = typer.Typer(
    name="postcards",
    help=(
        "Postcards is a CLI for the Swiss Postcard Creator. "
        "See https://github.com/abertschi/postcards for the full documentation."
    ),
    no_args_is_help=True,
    rich_markup_mode="rich",
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _version_callback(value: bool) -> None:
    """Print the package version and exit when ``--version`` is given."""
    if value:
        typer.echo(f"postcards {__version__}")
        raise typer.Exit(code=0)


def _verbose_callback(value: int) -> int:
    """Capture the ``-v`` count and configure logging immediately.

    Typer invokes option callbacks before the command body runs.
    By the time the body executes, the root logger is already at
    the right level, so business logic in the commands does not
    need to touch :mod:`logging` directly. The mapping from
    ``-v`` count to log level lives in
    :data:`postcards.log.DEFAULT_VERBOSITY_LEVELS`.
    """
    from postcards.log import verbosity_to_level

    target_level = verbosity_to_level(value, levels=DEFAULT_VERBOSITY_LEVELS)
    # Always log to stderr so an interactive user's stdout
    # remains a clean record of the cards they sent.
    configure_logging(target_level, stream=sys.stderr)
    _ = LOG_LEVEL_TRACE  # keep the symbol import honest; level itself
    # is propagated via the root logger by ``configure_logging``.
    return value


@app.callback()
def _root_callback(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Print the postcards version and exit.",
    ),
    verbose: int = typer.Option(
        0,
        "-v",
        "--verbose",
        count=True,
        callback=_verbose_callback,
        help="Increase log verbosity. Repeat for more detail (max 3).",
    ),
) -> None:
    """Root callback: handles ``--version`` and verbosity."""


# ------------------------------------------------------------------
# Subcommand registration
# ------------------------------------------------------------------
# Imported here (after ``app`` is defined) so each ``commands``
# module can call ``app.command(...)`` / ``app.add_typer(...)``
# at import time.
from postcards.cli.commands import (  # noqa: E402, F401  (import-after-callback intentional)
    accounts,
    addresses,
    batch,
    config,
    credentials,
    generate,
    legacy,
    plugins,
    preview,
    quota,
    schedule,
    send,
    status,
    templates,
)

__all__ = ["app"]
