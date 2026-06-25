"""``postcards tui`` — launch the local Textual-based TUI.

The TUI is an *optional* extra; the command checks whether
the ``gui`` extra is installed and surfaces a clear
``pip install postcards[gui]`` message when it is not. The
check is performed lazily so the core CLI stays importable
on systems where :mod:`textual` cannot be installed (most
notably, minimal CI containers).

Why a separate subcommand
-------------------------

The TUI runs the full event loop and blocks until the user
quits. We deliberately do not run it via ``postcards``'s
``--tui`` flag because:

* a flag would require the CLI to import :mod:`textual` even
  for users who never run the TUI;
* the entry-point contract (``postcards = postcards.cli.main:main``)
  is documented in the README and would be harder to keep
  accurate if a flag changed behaviour based on the install
  layout.

The dedicated ``postcards tui`` subcommand keeps the
optional-dependency boundary crisp: the import only happens
when the user runs ``postcards tui`` and gets an actionable
error if :mod:`textual` is missing.
"""

from __future__ import annotations

from pathlib import Path

import typer

from postcards.cli.app import app
from postcards.cli.errors import raise_cli_error
from postcards.cli.options import (
    config_path_option,
)


@app.command(
    name="tui",
    help=(
        "Launch the local TUI (Textual). Requires the optional "
        "'gui' extra: pip install 'postcards[gui]'."
    ),
    no_args_is_help=True,
)
def tui_cmd(
    config_file: Path = config_path_option(),
    accounts_file: Path | None = typer.Option(
        None,
        "-a",
        "--accounts-file",
        help="Path to a dedicated accounts file (defaults to the main config).",
    ),
    send: bool = typer.Option(
        False,
        "--send",
        help=(
            "Disable the default dry-run mode. The TUI still asks for an "
            "explicit 'YES' confirmation before sending for real."
        ),
    ),
) -> None:
    """Launch the interactive TUI.

    ``--send`` flips the dry-run default off. The user must
    still type ``YES`` at the confirm modal to send; the flag
    just removes the extra step of un-checking the dry-run
    box in the Compose screen.
    """
    try:
        from postcards.tui import run_tui
    except ImportError as exc:  # pragma: no cover — guarded by package install
        raise raise_cli_error(
            "the TUI requires the 'gui' extra; install with 'pip install postcards[gui]'",
            exit_code=2,
        ) from exc

    run_tui(
        config_path=config_file,
        accounts_file=accounts_file,
        dry_run=not send,
    )


__all__ = ["tui_cmd"]
