"""``postcards status`` — show the resolved CLI configuration.

``status`` is a read-only diagnostic command. It reports:

* the active config file (after envvar substitution),
* the configured backend (and whether the selection was
  implicit or explicit),
* the active account (if any),
* the count of accounts in the multi-account list,
* the postcard package version and the Python version.

The command does not authenticate, does not touch the
network, and does not require a config file to exist; an
empty config is a valid state and ``status`` reports it as
such.
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

import typer

from postcards import __version__
from postcards.backend import available_backends
from postcards.cli.app import app
from postcards.cli.config_io import resolve_config_path
from postcards.cli.options import config_path_option


@app.command(
    name="status",
    help="Print the resolved CLI configuration (config path, backend, account).",
    no_args_is_help=False,
)
def status_cmd(
    config_file: Path | None = config_path_option(),
) -> None:
    """Print the resolved configuration and version information."""
    target = resolve_config_path(config_file)
    backend = os.environ.get("POSTCARDS_BACKEND") or "(default: swissid)"
    username = os.environ.get("POSTCARDS_USERNAME") or "(unset)"
    password_set = bool(os.environ.get("POSTCARDS_PASSWORD"))

    typer.echo(f"postcards version : {__version__}")
    typer.echo(f"python version    : {platform.python_version()}")
    typer.echo(f"platform          : {sys.platform}")
    typer.echo("")
    typer.echo(f"config path       : {target}")
    typer.echo(f"  exists          : {target.is_file()}")
    typer.echo("")
    typer.echo(f"backend           : {backend}")
    typer.echo(f"  available       : {', '.join(available_backends())}")
    typer.echo("")
    typer.echo("credentials")
    typer.echo(f"  POSTCARDS_USERNAME : {username}")
    typer.echo(f"  POSTCARDS_PASSWORD : {'set' if password_set else 'unset'}")
    typer.echo(f"  POSTCARDS_KEY      : {'set' if os.environ.get('POSTCARDS_KEY') else 'unset'}")


__all__ = ["status_cmd"]
