"""``postcards preview`` — show what ``postcards send`` would do.

The preview command is a strict dry-run: it never reaches the
network, never authenticates against SwissID, and never consumes
the daily quota. It loads the same config file as :mod:`.send`,
validates the recipient / picture / message, and either:

* prints a human-readable summary of the would-be send (the
  default behaviour), **or**
* when ``--output`` is given, renders the postcard (front +
  back) to a local PNG / JPEG / PDF file so the user can
  inspect what would actually be printed without sending.

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
from PIL import UnidentifiedImageError

from postcards.cli.app import app
from postcards.cli.errors import CLIError, raise_cli_error
from postcards.cli.options import (
    config_path_option,
    key_option,
    password_option,
    picture_option,
    username_option,
)
from postcards.config import ConfigError, ConfigLayer
from postcards.image import ImageError, prepare_postcard_image
from postcards.models import Message, Postcard
from postcards.postcards import Postcards
from postcards.render import RenderError, render_postcard


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


def _read_local_picture_bytes(picture: str) -> bytes:
    """Read a local picture file and return its bytes.

    Raises :class:`postcards.cli.errors.CLIError` (via
    :func:`raise_cli_error`) when the picture path is not a
    local file or the file does not exist. The preview is a
    strict offline command, so HTTP URLs are rejected here even
    though ``Postcards._read_picture`` accepts them — fetching
    a remote picture during preview would contradict the
    "no network" guarantee.
    """
    if picture.startswith("http://") or picture.startswith("https://"):
        raise_cli_error(
            "preview --output does not support URL pictures (offline-only); "
            "download the picture first and pass a local path",
            exit_code=2,
        )
    path = Path(picture)
    if not path.is_file():
        raise_cli_error(f"picture file not found: {picture}", exit_code=2)
    return path.read_bytes()


def _build_preview_postcard(
    *,
    config_file: Path,
    picture: str | None,
    message: list[str],
    username: str | None,
    password: str | None,
) -> Postcard:
    """Build the user-facing :class:`Postcard` model for the preview render.

    The picture is run through the same A6 image pipeline the
    send command uses, so the rendered preview matches the
    exact JPEG bytes the backend would transmit. The recipient
    and sender are pulled from the typed config layer; the
    sender falls back to the recipient when the config does
    not declare one (mirrors the legacy send behaviour).
    """
    try:
        layer = ConfigLayer(config_path=config_file)
        recipient = layer.load_recipient()
        sender = layer.load_sender() or recipient
        # ``load_accounts`` is what validates the credentials are
        # well-formed — call it even though the preview does not
        # consume them, so a misconfigured credentials block is
        # surfaced here rather than at ``send`` time.
        _ = layer.load_accounts(
            username_override=username,
            password_override=password,
        )
    except ConfigError as exc:
        raise_cli_error(str(exc))

    message_text = " ".join(message).strip() if message else ""
    msg = Message.from_text(message_text)

    picture_bytes: bytes | None = None
    if picture is not None:
        try:
            picture_bytes = _read_local_picture_bytes(picture)
        except CLIError:
            raise
        try:
            picture_bytes = prepare_postcard_image(picture_bytes)
        except (ImageError, UnidentifiedImageError) as exc:
            raise_cli_error(f"cannot process picture for preview: {exc}")

    return Postcard(
        sender=sender,
        recipient=recipient,
        message=msg,
        picture=picture_bytes,
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
    output: Path | None = typer.Option(
        None,
        "-o",
        "--output",
        help=(
            "Render the postcard (front + back) to a local image/PDF "
            "instead of printing a textual summary. Format is inferred "
            "from the file extension: .png / .jpg / .jpeg / .pdf."
        ),
    ),
) -> None:
    """Preview a postcard without contacting Swiss Post.

    Without ``--output``, prints a human-readable summary of the
    would-be send. With ``--output PATH``, renders the postcard
    (front image + back panel with message and addresses) to a
    local PNG / JPEG / PDF file so the user can verify the card
    before invoking :func:`send_cmd`.
    """
    if picture is None and not message:
        raise_cli_error("either --picture or --message is required (or both)", exit_code=2)

    if output is not None:
        _render_preview(
            config_file=config_file,
            picture=picture,
            message=message or [""],
            username=username,
            password=password,
            output=output,
        )
        return

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
        raise_cli_error(str(exc))

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
        raise_cli_error(f"preview failed: {exc}")

    typer.echo("Preview OK.")


def _render_preview(
    *,
    config_file: Path,
    picture: str | None,
    message: list[str],
    username: str | None,
    password: str | None,
    output: Path,
) -> None:
    """Render the postcard to ``output`` and print a confirmation line.

    Pulled out of :func:`preview_cmd` so the textual and
    render-to-file code paths do not share state. The renderer
    is purely local — no network, no SwissID, no quota — so
    rendering never consumes the daily card allowance.
    """
    postcard = _build_preview_postcard(
        config_file=config_file,
        picture=picture,
        message=message,
        username=username,
        password=password,
    )
    try:
        written = render_postcard(postcard, output)
    except RenderError as exc:
        raise_cli_error(f"cannot render preview: {exc}")
    typer.echo(f"Preview written to {written} ({written.stat().st_size} bytes).")


__all__ = ["preview_cmd"]
