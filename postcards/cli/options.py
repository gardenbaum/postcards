"""Shared Typer options used by multiple command modules.

Centralising the common options here keeps the per-command
modules free of repeated ``typer.Option(...)`` boilerplate and
makes it easy to keep the help text, default values, and envvar
bindings in sync.

The functions return :class:`typer.Option` instances; Typer
treats them as annotation values just like a literal
``typer.Option(...)`` expression at the call site.
"""

from __future__ import annotations

from pathlib import Path

import typer

# ---------------------------------------------------------------------------
# File / path options
# ---------------------------------------------------------------------------


def config_path_option(
    default: Path = Path("config.json"),
    help_text: str = (
        "Path to the config file. Defaults to ./config.json. "
        "Honours the POSTCARDS_CONFIG environment variable when not given."
    ),
) -> Path:
    """``--config / -c`` — location of the config file."""
    return typer.Option(
        default,
        "-c",
        "--config",
        help=help_text,
        envvar="POSTCARDS_CONFIG",
        show_default=True,
    )


def picture_option(
    required: bool = False,
    help_text: str = (
        "Path or URL to the picture to print on the front of the card. Omit for a text-only card."
    ),
) -> str | None:
    """``--picture / -p`` — path or URL to the postcard picture."""
    return typer.Option(
        None,
        "-p",
        "--picture",
        help=help_text,
        show_default=False,
    )


# ---------------------------------------------------------------------------
# Credential / account options
# ---------------------------------------------------------------------------


def username_option() -> str | None:
    """``--username`` — override the username from env / config / keyring."""
    return typer.Option(
        None,
        "--username",
        envvar="POSTCARDS_USERNAME",
        help="SwissID username. Overrides POSTCARDS_USERNAME and the config file.",
        show_default=False,
    )


def password_option() -> str | None:
    """``--password`` — override the password from env / keyring / config."""
    return typer.Option(
        None,
        "--password",
        envvar="POSTCARDS_PASSWORD",
        help="SwissID password. Overrides POSTCARDS_PASSWORD and the keyring.",
        show_default=False,
        hide_input=True,
    )


def key_option() -> str | None:
    """``-k / --key`` — credential-encryption key."""
    return typer.Option(
        None,
        "-k",
        "--key",
        envvar="POSTCARDS_KEY",
        metavar="KEY",
        help=(
            "Key used to decrypt credentials stored in the config file. "
            "When omitted, the default key is used."
        ),
        show_default=False,
    )


# ---------------------------------------------------------------------------
# Behaviour options
# ---------------------------------------------------------------------------


def dry_run_option(
    help_text: str = "Do not actually send the postcard. Show what would happen.",
) -> bool:
    """``--dry-run`` — same semantics as the legacy ``--mock`` flag."""
    return typer.Option(
        False,
        "--dry-run",
        help=help_text,
    )


def mock_option() -> bool:
    """``--mock`` — legacy alias for ``--dry-run`` (kept for compat)."""
    return typer.Option(
        False,
        "--mock",
        help=(
            "Deprecated alias for --dry-run. "
            "Prefer --dry-run; --mock will be removed in a future release."
        ),
        hidden=True,
    )


def backend_option() -> str | None:
    """``--backend`` — name of the PostcardBackend to use."""
    return typer.Option(
        None,
        "--backend",
        envvar="POSTCARDS_BACKEND",
        help=(
            "Backend to use: 'swissid' (production) or 'mock' (in-memory). "
            "Defaults to the value of the config file's 'backend' field."
        ),
        show_default=False,
    )


def all_accounts_option() -> bool:
    """``--all-accounts`` — send one card per valid account."""
    return typer.Option(
        False,
        "--all-accounts",
        help=(
            "Send one card per valid account (the default stops at the first "
            "account that has a free quota)."
        ),
    )


def yes_option() -> bool:
    """``--yes / -y`` — skip the confirmation prompt."""
    return typer.Option(
        False,
        "-y",
        "--yes",
        help="Skip the confirmation prompt.",
    )


__all__ = [
    "all_accounts_option",
    "backend_option",
    "config_path_option",
    "dry_run_option",
    "key_option",
    "mock_option",
    "password_option",
    "picture_option",
    "username_option",
    "yes_option",
]
