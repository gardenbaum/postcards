"""``postcards encrypt`` / ``postcards decrypt`` — credential crypto.

Migrated from the legacy ``argparse`` ``encrypt`` / ``decrypt``
subcommands. Both commands take a credential and a key, run
the same XOR-based cipher the legacy CLI used, and print the
result on stdout so the user can pipe it into another command.

The cipher is the same as the legacy one; M2 just exposes it
through Typer so the user gets ``--help`` and a clean error
path. The cryptographic strength is the same as the legacy CLI
(intentionally weak; this is obfuscation, not real security).
"""

from __future__ import annotations

import argparse

import typer

from postcards.cli.app import app
from postcards.cli.errors import raise_cli_error
from postcards.cli.options import key_option
from postcards.postcards import DEFAULT_KEY, Postcards


def _run_encrypt(credential: str, key: str) -> None:
    cards = Postcards()
    typer.echo(cards._encrypt(key, credential))


def _run_decrypt(credential: str, key: str) -> None:
    cards = Postcards()
    try:
        typer.echo(cards._decrypt(key, credential))
    except SystemExit:
        raise
    except Exception as exc:
        raise_cli_error(f"could not decrypt: {exc}")


@app.command(
    name="encrypt",
    help="Encrypt a credential for storage in the config file.",
    no_args_is_help=True,
)
def encrypt_cmd(
    credential: str = typer.Argument(..., help="The plaintext credential to encrypt."),
    key: str | None = key_option(),
) -> None:
    """Print the encrypted form of ``credential`` to stdout."""
    _run_encrypt(credential, key or DEFAULT_KEY)


@app.command(
    name="decrypt",
    help="Decrypt a credential that was stored encrypted in the config file.",
    no_args_is_help=True,
)
def decrypt_cmd(
    credential: str = typer.Argument(..., help="The encrypted credential to decrypt."),
    key: str | None = key_option(),
) -> None:
    """Print the decrypted form of ``credential`` to stdout."""
    _run_decrypt(credential, key or DEFAULT_KEY)


# ---------------------------------------------------------------------------
# Legacy shim
# ---------------------------------------------------------------------------
# The M1 test suite (``tests/test_cli_help.py`` and
# ``tests/test_send_integration.py``) imports the legacy
# ``Postcards`` class directly. The encrypt / decrypt methods
# are part of that public surface; keep a thin shim here so
# existing test code that calls ``cards.do_command_encrypt(args)``
# / ``cards.do_command_decrypt(args)`` continues to work.


def _build_encrypt_args(credential: str, key: str | None) -> argparse.Namespace:
    return argparse.Namespace(credential=credential, key=key or DEFAULT_KEY)


def _legacy_encrypt(args: argparse.Namespace) -> None:
    _run_encrypt(args.credential, args.key or DEFAULT_KEY)


def _legacy_decrypt(args: argparse.Namespace) -> None:
    _run_decrypt(args.credential, args.key or DEFAULT_KEY)


__all__ = [
    "_legacy_decrypt",
    "_legacy_encrypt",
    "decrypt_cmd",
    "encrypt_cmd",
]
