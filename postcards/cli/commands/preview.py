"""``postcards preview`` — show what ``postcards send`` would do.

The preview command is a strict dry-run: it never reaches the
network, never authenticates against SwissID, and never consumes
the daily quota. It loads the same config file as :mod:`.send`,
validates the recipient / picture / message, and prints a
human-readable summary of the would-be send.

Why this lives in its own command
---------------------------------

The legacy CLI's ``--mock`` flag on ``send`` was the only way
to preview a card. M2 promotes that flow to a first-class
command so the user can ``postcards preview`` and then
``postcards send`` as two distinct steps — which is closer to
how the upstream Postcard Creator web UI works (separate
"compose" and "send" screens).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import typer

from postcards.cli.app import app
from postcards.cli.errors import CLIError
from postcards.cli.options import (
    config_path_option,
    key_option,
    password_option,
    picture_option,
    username_option,
)
from postcards.config import ConfigError, ConfigLayer
from postcards.postcards import Postcards


def _build_namespace(
    *,
    config_file: Path,
    picture: str | None,
    message: list[str],
    username: str | None,
    password: str | None,
    key: str | None,
    accounts_file: Path | None,
) -> argparse.Namespace:
    """Shape the args namespace the legacy ``do_command_send`` expects.

    The preview path is ``--mock=True`` with ``--all-accounts``
    off, so the legacy method stops at the first valid account
    without ever calling ``send_free_card``. The picture and
    message are validated against the config; the network is
    untouched.
    """
    return argparse.Namespace(
        config_file=[str(config_file)],
        accounts_file=str(accounts_file) if accounts_file is not None else False,
        picture=picture,
        message=message,
        mock=True,
        test_plugin=False,
        username=username or "",
        password=password or "",
        all_accounts=False,
        key=(None,) if key is None else key,
    )


@app.command(
    name="preview",
    help="Show what 'postcards send' would do, without sending.",
    no_args_is_help=True,
)
def preview_cmd(
    config_file: Path = config_path_option(),
    picture: str | None = picture_option(),
    message: list[str] = typer.Option(
        None,
        "-m",
        "--message",
        help="Postcard message (you can use HTML tags).",
    ),
    username: str | None = username_option(),
    password: str | None = password_option(),
    key: str | None = key_option(),
    accounts_file: Path | None = typer.Option(
        None,
        "-a",
        "--accounts-file",
        help="Path to a dedicated accounts file (defaults to the main config).",
    ),
) -> None:
    """Print a human-readable preview of the would-be send.

    The preview walks the same code path as :func:`send_cmd`
    with ``--dry-run`` set, but it captures the validation
    errors as :class:`postcards.cli.errors.CLIError` instead
    of letting the legacy code ``sys.exit(1)``. That way the
    Typer runner sees a clean error path and tests can assert
    on it.
    """
    if picture is None and not message:
        raise CLIError(
            "either --picture or --message is required (or both)",
            exit_code=2,
        )

    # Resolve accounts and the recipient/sender through the typed
    # loader so the preview can summarise them without touching
    # the network. The actual picture is validated by
    # ``do_command_send`` (which raises ``ImageError`` on bad
    # input); the preview catches that and surfaces it as a
    # CLI error rather than a stack trace.
    try:
        layer = ConfigLayer(config_path=config_file)
        accounts = layer.load_accounts(
            username_override=username,
            password_override=password,
        )
        recipient = layer.load_recipient()
        sender = layer.load_sender()
    except ConfigError as exc:
        raise CLIError(str(exc)) from exc

    typer.echo("Preview (no card will be sent):")
    typer.echo("")
    typer.echo(f"  Recipient : {recipient.prename} {recipient.lastname}")
    typer.echo(f"             {recipient.street}, {recipient.zip_code} {recipient.place}")
    if sender is not None and (sender.prename or sender.lastname):
        typer.echo(f"  Sender    : {sender.prename} {sender.lastname}")
    else:
        typer.echo("  Sender    : (same as recipient)")
    typer.echo(f"  Picture   : {picture or '(none — text only)'}")
    if message:
        joined = " ".join(message)
        typer.echo(f"  Message   : {joined[:80]}{'...' if len(joined) > 80 else ''}")
    else:
        typer.echo("  Message   : (none)")
    typer.echo(
        f"  Accounts  : {len(accounts)} resolved "
        f"(sources: {', '.join(sorted({a.source for a in accounts}))})"
    )
    typer.echo("")

    # Validate the picture / message by walking the send path with
    # ``--mock=True`` (which short-circuits the network call). If
    # the picture is malformed or the message exceeds the limit,
    # the legacy code raises — we forward that as a CLI error.
    cards = Postcards()
    args = _build_namespace(
        config_file=config_file,
        picture=picture,
        message=message or [""],
        username=username,
        password=password,
        key=key,
        accounts_file=accounts_file,
    )
    try:
        cards.do_command_send(args)
    except (FileNotFoundError, ValueError) as exc:
        raise CLIError(f"preview failed: {exc}") from exc

    typer.echo("Preview OK.")


__all__ = ["preview_cmd"]
