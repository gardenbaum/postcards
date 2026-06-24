"""``postcards send`` — send a postcard.

Migrated from the legacy ``argparse`` ``send`` subcommand in
:mod:`postcards.postcards`. The new Typer command keeps the
existing CLI surface (``--config``, ``--picture``, ``--message``,
``--mock``/``--dry-run``, ``--username``, ``--password``,
``--all-accounts``, ``-k``/``--key``) and delegates the actual
work to :meth:`postcards.postcards.Postcards.do_command_send`.

Why delegate rather than re-implement
-------------------------------------

``Postcards.do_command_send`` owns the credential-resolution
flow, the account-shuffling logic, the per-account
``send_free_card`` invocation, and the network-mocked test
fixtures the M1 tests rely on. Re-implementing it in the
Typer layer would duplicate that code and break the M1 test
suite. Instead, the command builds an :class:`argparse.Namespace`
that matches the legacy parser's shape and calls into the
existing method — so every M1 integration test (e.g.
``tests/test_send_integration.py``) continues to work
unchanged.

The Typer layer is still the source of truth for the *user
interface*: the new options (``--dry-run``, the
``POSTCARDS_BACKEND`` env var wiring, the rich help) live here.
The legacy method just gets a Namespace built from the Typer
options.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import typer

from postcards import __version__ as _postcards_version
from postcards.cli.app import app
from postcards.cli.errors import CLIError
from postcards.cli.options import (
    all_accounts_option,
    config_path_option,
    dry_run_option,
    key_option,
    mock_option,
    password_option,
    picture_option,
    username_option,
)
from postcards.postcards import Postcards

# ``_postcards_version`` is imported to keep the build graph honest
# (the CLI uses it via ``--version``); the import itself is the
# coupling.
_ = _postcards_version


def _build_namespace(
    *,
    config_file: Path,
    picture: str | None,
    message: list[str],
    dry_run: bool,
    mock: bool,
    username: str | None,
    password: str | None,
    all_accounts: bool,
    key: str | None,
    accounts_file: Path | None,
) -> argparse.Namespace:
    """Build the :class:`argparse.Namespace` ``do_command_send`` expects.

    The shape is identical to what the legacy parser would have
    produced. Keeping the namespace construction local to this
    module means a future refactor that drops the dependency on
    :mod:`argparse` can change exactly one function.
    """
    return argparse.Namespace(
        config_file=[str(config_file)],
        accounts_file=str(accounts_file) if accounts_file is not None else False,
        picture=picture,
        message=message,
        mock=bool(dry_run or mock),
        test_plugin=False,
        username=username or "",
        password=password or "",
        all_accounts=all_accounts,
        # The legacy ``--key`` flag uses ``nargs="?"`` with a tuple
        # default ``(None,)`` to mean "no key" vs. ``None`` to mean
        # "use the default key". ``Typer``'s ``Optional[str]``
        # cannot easily express that three-valued state, so we
        # normalise: ``None`` → ``(None,)`` (use the default key).
        key=(None,) if key is None else key,
    )


@app.command(
    name="send",
    help="Send a postcard.",
    no_args_is_help=True,
)
def send_cmd(
    config_file: Path = config_path_option(),
    picture: str | None = picture_option(),
    message: list[str] = typer.Option(
        None,
        "-m",
        "--message",
        help=(
            "Postcard message (you can use HTML tags). Pass multiple times "
            "to assemble a multi-line message; the parts are joined with "
            "single spaces."
        ),
    ),
    dry_run: bool = dry_run_option(),
    mock: bool = mock_option(),
    username: str | None = username_option(),
    password: str | None = password_option(),
    all_accounts: bool = all_accounts_option(),
    key: str | None = key_option(),
    accounts_file: Path | None = typer.Option(
        None,
        "-a",
        "--accounts-file",
        help="Path to a dedicated accounts file (defaults to the main config).",
    ),
) -> None:
    """Send a postcard using the supplied config file.

    The config file must contain a ``recipient`` block (required
    fields: ``firstname``, ``lastname``, ``street``, ``zipcode``,
    ``city``) and an ``accounts`` block with at least one
    ``username`` / ``password`` pair. See ``postcards config
    init`` for a starter template.
    """
    if picture is None and not message:
        raise CLIError(
            "either --picture or --message is required (or both)",
            exit_code=2,
        )

    cards = Postcards()
    args = _build_namespace(
        config_file=config_file,
        picture=picture,
        message=message or [""],
        dry_run=dry_run,
        mock=mock,
        username=username,
        password=password,
        all_accounts=all_accounts,
        key=key,
        accounts_file=accounts_file,
    )
    cards.do_command_send(args)


__all__ = ["send_cmd"]
