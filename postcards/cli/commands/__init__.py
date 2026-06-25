"""Subcommand registration for the Typer-based CLI.

Each module in this package calls :func:`app.command` (or
:func:`app.add_typer` for grouped subcommands like ``config`` and
``accounts``) to attach itself to the top-level :data:`app`.

Importing this package is what triggers registration; the
top-level :mod:`postcards.cli.app` does that after :data:`app` is
constructed.
"""

from __future__ import annotations

# Importing the submodules is enough — they each register their
# commands at import time.
from postcards.cli.commands import (
    accounts,
    config,
    credentials,
    generate,
    preview,
    quota,
    send,
    status,
)

__all__ = [
    "accounts",
    "config",
    "credentials",
    "generate",
    "preview",
    "quota",
    "send",
    "status",
]
