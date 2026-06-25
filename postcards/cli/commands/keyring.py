"""``postcards keyring {set,get,delete,list,status}`` — OS keyring for SwissID.

The keyring subcommand is the M5 user-facing surface for the
:class:`postcards.config.KeyringStore` wrapper. The CLI
follows the same conventions as the rest of the project
(Typer, ``-c / --config``, ``--no-fail`` style for the
``status`` subcommand) and never prints a stored password to
stdout — ``get`` only reports whether a value is present.

Why a Typer sub-group rather than a flat command
-----------------------------------------------

The keyring has five orthogonal operations (set, get, delete,
list, status). A flat ``postcards keyring-set`` / ``postcards
keyring-get`` pair would surface the same verbs but double the
number of top-level entries in ``postcards --help``. The
``keyring`` sub-group is the convention the existing
``accounts`` and ``config`` sub-groups follow; matching the
existing shape keeps the help output consistent.

Security notes
--------------

* ``keyring set`` echoes the password length, not the
  password, so a user running ``set`` with a typo sees
  "stored password (length 12)" rather than the plaintext.
* ``keyring get`` does not print the password at all — it
  prints ``present`` or ``absent``. The reasoning is that the
  user already typed the password into ``set``; the read path
  exists for scripts that want to confirm a value is there,
  not for the user to retrieve the plaintext.
* ``keyring list`` only prints the usernames, never the
  passwords. The OS keyring API does not expose a "list all
  passwords for service X" method (intentionally — that would
  be a security hole on macOS/Windows where the application
  is supposed to access only its own entries). The ``list``
  subcommand therefore returns nothing useful by itself; it
  exists to make the CLI shape consistent with
  ``accounts list`` and to let ``doctor`` reuse the same
  printing helper.
"""

from __future__ import annotations

import typer

from postcards.cli.app import app
from postcards.cli.errors import raise_cli_error
from postcards.config import KeyringError, KeyringStatus, KeyringStore

#: The keyring sub-group is mounted under ``postcards keyring ...``.
#: The ``no_args_is_help=True`` makes ``postcards keyring`` print the
#: subcommand list rather than fail with a Click "missing command" error.
keyring_app = typer.Typer(
    name="keyring",
    help=(
        "Read and write SwissID credentials in the OS keyring. "
        "Supports 'set', 'get', 'delete', 'list', and 'status'."
    ),
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app.add_typer(keyring_app)


def _make_store() -> KeyringStore:
    """Return a default :class:`KeyringStore` for production use.

    Tests inject a custom ``backend`` via this function's
    argument by calling the public APIs with a stubbed store;
    production code paths just call this helper to get the
    real OS-backed store.
    """
    return KeyringStore()


@keyring_app.command(
    name="set",
    help="Store the password for USERNAME in the OS keyring.",
)
def keyring_set(
    username: str = typer.Argument(..., help="The SwissID username (the keyring 'account' field)."),
    password: str | None = typer.Option(
        None,
        "--password",
        "-p",
        help=("The password to store. If omitted, you'll be prompted (input is hidden)."),
        hide_input=True,
        prompt=False,
    ),
) -> None:
    """Store ``password`` for ``username`` in the OS keyring.

    The :class:`KeyringStore` writes to the ``postcards`` service
    namespace. The CLI echoes ``stored password (length N)`` so
    the user can confirm the call without revealing the
    plaintext; the actual password never reaches stdout or the
    shell history.
    """
    if not username:
        raise_cli_error("username must not be empty", exit_code=2)
    if password is None:
        prompted: str = typer.prompt("Password", hide_input=True, confirmation_prompt=False)
        password = prompted
    if not password:
        raise_cli_error("password must not be empty", exit_code=2)

    store = _make_store()
    try:
        store.set(username, password)
    except KeyringError as exc:
        raise_cli_error(str(exc))
    typer.echo(f"stored password for {username!r} (length {len(password)}) in the keyring")


@keyring_app.command(
    name="get",
    help="Check whether a password is stored in the OS keyring.",
)
def keyring_get(
    username: str = typer.Argument(..., help="The SwissID username to look up."),
) -> None:
    """Report whether ``username`` has a stored password.

    The command prints ``present`` or ``absent`` rather than
    the password itself. The rationale is documented in the
    module docstring: ``get`` exists so scripts can confirm
    a value is there, not so the user retrieves the
    plaintext via the terminal. (A user wanting the
    plaintext for a copy-paste should use the OS's own
    keyring UI — Keychain Access, GNOME Keyring, ... —
    rather than the CLI.)
    """
    if not username:
        raise_cli_error("username must not be empty", exit_code=2)
    store = _make_store()
    value = store.get(username)
    if value is None:
        typer.echo("absent")
    else:
        typer.echo(f"present (length {len(value)})")


@keyring_app.command(
    name="delete",
    help="Remove a password from the OS keyring.",
)
def keyring_delete(
    username: str = typer.Argument(..., help="The SwissID username to remove."),
) -> None:
    """Remove the keyring entry for ``username``.

    Idempotent: deleting a username that has no stored entry
    is treated as success (the post-condition — "no entry
    present" — holds). The CLI prints the result so the user
    can confirm what happened without having to read docs.
    """
    if not username:
        raise_cli_error("username must not be empty", exit_code=2)
    store = _make_store()
    try:
        removed = store.delete(username)
    except KeyringError as exc:
        raise_cli_error(str(exc))
    if removed:
        typer.echo(f"removed keyring entry for {username!r}")
    else:
        typer.echo(f"no keyring entry for {username!r}")


@keyring_app.command(
    name="list",
    help="Print a note about listing — see the module docstring.",
)
def keyring_list() -> None:
    """Explain why ``list`` is a no-op on the keyring.

    The OS keyring API (macOS Keychain, Windows Credential
    Manager, Secret Service, KWallet) does not expose a
    "list entries for service X" call. The subcommand
    therefore prints a single line explaining the limitation
    rather than an empty list, which would be misleading.
    """
    typer.echo(
        "the OS keyring does not expose a list-entries call; "
        "use 'postcards accounts list' (from the config file) or your "
        "OS's keyring UI to see stored credentials"
    )


@keyring_app.command(
    name="status",
    help="Show whether the OS keyring is reachable and which backend is in use.",
)
def keyring_status_cmd() -> None:
    """Print a structured :class:`KeyringStatus` for the active host.

    The command is the same shape :func:`postcards.doctor` uses
    internally; exposing it standalone lets the user run a
    one-line check without invoking the full diagnostics
    suite.
    """
    store = _make_store()
    status: KeyringStatus = store.status()
    if status.available:
        typer.echo(f"keyring: available (backend={status.backend_name!r})")
    else:
        typer.echo(f"keyring: unavailable ({status.reason})")
        # Exit non-zero so a shell script can use the command as a
        # gate: ``postcards keyring status || echo "no keyring"``.
        raise typer.Exit(code=1)


__all__ = ["keyring_app"]
