"""``postcards accounts {add,list,use}`` — manage multi-account configs.

The M2 command tree promotes the legacy single-account config
file to a first-class multi-account list. ``accounts add``
appends a new account, ``accounts list`` prints the resolved
list, and ``accounts use`` sets the *active* account that
``postcards send`` and ``postcards quota`` default to.

Persistence model
-----------------

The accounts live in the config file under the ``accounts`` key
(an array of ``{"username": "...", "password": "..."}`` dicts).
The *active* account name is stored in a sibling key
``active_account``; when that key is missing or points at a
non-existent username, the CLI falls back to "all accounts"
(``postcards send --all-accounts`` semantics).

M2 keeps this state inside the same JSON file rather than
introducing a separate ``accounts.json`` so users only have
one file to gitignore. A future milestone can move the
accounts out into a separate file if the size of the account
list becomes unwieldy.
"""

from __future__ import annotations

from pathlib import Path

import typer

from postcards.cli.app import app
from postcards.cli.config_io import read_config, resolve_config_path, write_config
from postcards.cli.errors import CLIError

accounts_app = typer.Typer(
    name="accounts",
    help="Manage the multi-account list in the config file.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app.add_typer(accounts_app)


def _load_accounts(path: Path) -> tuple[list[dict], dict]:
    """Return ``(accounts_list, full_config)`` from the config file.

    Missing ``accounts`` is treated as an empty list. The full
    config is returned alongside so callers can read sibling
    fields like ``active_account`` without re-reading the file.
    """
    config = read_config(path)
    accounts = config.get("accounts") or []
    if not isinstance(accounts, list):
        raise CLIError(
            f"'accounts' field in {path} must be a list, got {type(accounts).__name__}",
        )
    return list(accounts), config


@accounts_app.command(name="add", help="Add an account to the config file.")
def accounts_add(
    username: str = typer.Argument(..., help="The SwissID username to add."),
    password: str | None = typer.Option(
        None,
        "--password",
        "-p",
        help="The SwissID password. If omitted, you'll be prompted.",
        hide_input=True,
        prompt=False,
    ),
    key: str | None = typer.Option(
        None,
        "-k",
        "--key",
        help="Encrypt the password with this key before storing.",
    ),
    path: Path | None = typer.Option(
        None,
        "-c",
        "--config",
        help="Path to the config file. Defaults to ./config.json.",
    ),
) -> None:
    """Append a new account to the config file.

    If ``--password`` is omitted, Typer prompts the user
    (with input hidden) so passwords do not appear in the
    shell history. ``-k / --key`` encrypts the password with
    the :func:`postcards.postcards.Postcards._encrypt` helper
    before writing it to disk; the loader will decrypt it
    using the same key at send time.
    """
    target = resolve_config_path(path)
    accounts, config = _load_accounts(target)
    if any(a.get("username") == username for a in accounts):
        raise CLIError(f"account {username!r} already exists in {target}")
    if password is None:
        prompted: str = typer.prompt("Password", hide_input=True, confirmation_prompt=False)
        password = prompted
    if not password:
        raise CLIError("password must not be empty", exit_code=2)
    stored = _maybe_encrypt(password, key)
    accounts.append({"username": username, "password": stored})
    config["accounts"] = accounts
    write_config(target, config)
    typer.echo(f"added account {username!r} to {target}")


@accounts_app.command(name="list", help="List the accounts in the config file.")
def accounts_list(
    path: Path | None = typer.Option(
        None,
        "-c",
        "--config",
        help="Path to the config file. Defaults to ./config.json.",
    ),
    show_passwords: bool = typer.Option(
        False,
        "--show-passwords",
        help="Print stored passwords (or encrypted blobs) verbatim.",
    ),
) -> None:
    """Print the account list as a table.

    By default passwords are masked with ``***`` so the output
    is safe to share. ``--show-passwords`` reveals the stored
    values, which is useful for debugging the encrypt/decrypt
    path.
    """
    target = resolve_config_path(path)
    accounts, config = _load_accounts(target)
    active = config.get("active_account")
    if not accounts:
        typer.echo(f"no accounts in {target}; add one with 'postcards accounts add'")
        return
    for entry in accounts:
        marker = "*" if entry.get("username") == active else " "
        username = entry.get("username", "")
        password = entry.get("password", "")
        masked = "***" if password and not show_passwords else password
        typer.echo(f"{marker} {username}\t{masked}")


@accounts_app.command(name="use", help="Set the active account for send / quota.")
def accounts_use(
    username: str = typer.Argument(..., help="The SwissID username to mark as active."),
    path: Path | None = typer.Option(
        None,
        "-c",
        "--config",
        help="Path to the config file. Defaults to ./config.json.",
    ),
) -> None:
    """Mark ``username`` as the active account.

    The active account is what ``postcards send`` and
    ``postcards quota`` default to. ``--all-accounts`` on send
    still sends one card per valid account.
    """
    target = resolve_config_path(path)
    accounts, config = _load_accounts(target)
    if not any(a.get("username") == username for a in accounts):
        raise CLIError(
            f"account {username!r} not in {target}; add it first with 'postcards accounts add'",
            exit_code=2,
        )
    config["active_account"] = username
    write_config(target, config)
    typer.echo(f"active account is now {username!r}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _maybe_encrypt(password: str, key: str | None) -> str:
    """Encrypt ``password`` with ``key`` if a key is supplied.

    The encryption mirrors :meth:`postcards.postcards.Postcards._encrypt`
    so the same key can decrypt it at load time. We instantiate
    ``Postcards()`` to reuse the implementation rather than
    re-implementing the cipher here.
    """
    if not key:
        return password
    from postcards.postcards import Postcards

    cards = Postcards()
    return cards._encrypt(key, password)


__all__ = ["accounts_app"]
