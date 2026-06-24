"""``postcards send`` — send a postcard.

Migrated from the legacy ``argparse`` ``send`` subcommand in
:mod:`postcards.postcards`. The new Typer command keeps the
existing CLI surface (``--config``, ``--picture``, ``--message``,
``--mock``/``--dry-run``, ``--username``, ``--password``,
``--all-accounts``, ``-k``/``--key``) and delegates the actual
work to :meth:`postcards.postcards.Postcards.do_command_send`.

M4 additions
------------

* ``--to NAME`` overrides the recipient with the address-book
  entry named ``NAME`` (must be a ``recipient``).
* ``--sender NAME`` overrides the sender with the address-book
  entry named ``NAME`` (must be a ``sender``).
* ``--message-template NAME`` renders the named template from
  the template book, substituting ``--var KEY=VALUE`` pairs.
  ``--var`` may be repeated.
* ``--var`` is meaningful only with ``--message-template``;
  supplying it without a template is rejected.

These options are layered on top of the existing config-file
flow rather than replacing it: accounts still come from the
config file (``-c``), and the recipient / sender / message are
resolved in-memory so we never have to write a derived config
back to disk.

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

The M4 in-memory config trick uses the new
``do_command_send(config_dict=..., accounts_dict=...)``
keyword arguments: when present, ``do_command_send`` skips
the on-disk read and uses the supplied dicts directly. This
keeps the legacy on-disk path bit-identical for existing
callers while letting the modern CLI resolve address-book
and template entries before delegating.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import typer

from postcards import __version__ as _postcards_version
from postcards.addressbook.models import (
    AddressBookEntry,
    AddressCategory,
    TemplateError,
)
from postcards.addressbook.storage import load_address_book, load_template_book
from postcards.addressbook.variables import TemplateRenderError
from postcards.cli.app import app
from postcards.cli.config_io import read_config
from postcards.cli.errors import raise_cli_error
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


def _parse_var(arg: str) -> tuple[str, str]:
    """Parse a ``KEY=VALUE`` pair for ``--var``.

    Same rules as :func:`postcards.cli.commands.templates._parse_var`;
    duplicated here rather than re-exported because that
    function lives in a sibling command module and a shared
    helper would muddy the command / helpers split.
    """
    if "=" not in arg:
        raise_cli_error(f"--var {arg!r} is malformed; expected KEY=VALUE", exit_code=2)
    key, value = arg.split("=", 1)
    key = key.strip()
    if not key:
        raise_cli_error(f"--var {arg!r} has an empty key", exit_code=2)
    return key, value


def _resolve_recipient_entry(
    name: str,
    *,
    book,
) -> AddressBookEntry:
    """Return the recipient entry named ``name``.

    Raises a CLI error when the entry does not exist or is not
    categorised as a recipient (``sender`` entries are rejected
    so a typo in ``--to``/``--sender`` is caught early).
    """
    entry = book.find(name)
    if entry is None:
        raise_cli_error(
            f"no address-book entry named {name!r}; create it with 'postcards addresses add' first",
            exit_code=2,
        )
    if entry.category is not AddressCategory.RECIPIENT:
        raise_cli_error(
            f"address-book entry {name!r} is a {entry.category.value}, not a recipient; "
            "use --sender for senders",
            exit_code=2,
        )
    return entry


def _resolve_sender_entry(
    name: str,
    *,
    book,
) -> AddressBookEntry:
    """Return the sender entry named ``name``.

    Mirror of :func:`_resolve_recipient_entry` for senders —
    rejects recipient entries so the wrong flag is caught
    early.
    """
    entry = book.find(name)
    if entry is None:
        raise_cli_error(
            f"no address-book entry named {name!r}; create it with 'postcards addresses add' first",
            exit_code=2,
        )
    if entry.category is not AddressCategory.SENDER:
        raise_cli_error(
            f"address-book entry {name!r} is a {entry.category.value}, not a sender; "
            "use --to for recipients",
            exit_code=2,
        )
    return entry


def _address_to_legacy_dict(entry: AddressBookEntry) -> dict[str, str]:
    """Render an address-book entry as the legacy config-file dict shape.

    The vendored shim reads ``recipient`` / ``sender`` dicts via
    :meth:`postcards.postcards.Postcards._create_recipient` /
    ``_create_sender``, which expect ``firstname`` /
    ``zipcode`` / ``city`` keys (the *config-file* shape, not
    the canonical :class:`AddressSpec` field names). This
    helper bridges the two so an address-book entry feeds
    straight into the legacy send flow without translation at
    the call site.
    """
    addr = entry.address
    payload: dict[str, str] = {
        "firstname": addr.prename,
        "lastname": addr.lastname,
        "street": addr.street,
        "zipcode": addr.zip_code,
        "city": addr.place,
    }
    if addr.company:
        payload["company"] = addr.company
    if addr.country:
        payload["country"] = addr.country
    if addr.salutation:
        payload["salutation"] = addr.salutation
    if addr.company_addition:
        payload["companyAddition"] = addr.company_addition
    return payload


def _resolve_message(
    *,
    message: list[str] | None,
    template_name: str | None,
    var_args: Sequence[str] | None,
) -> list[str]:
    """Return the message parts to hand to the legacy send flow.

    The legacy code expects ``args.message`` to be a list of
    strings that get joined with single spaces
    (see :meth:`postcards.postcards.Postcards._handle_message_argument`).
    We preserve that contract by always returning a list.
    """
    if template_name is None:
        if var_args:
            # ``--var`` only makes sense with a template; reject
            # the combination explicitly so the user is not left
            # wondering why their variables had no effect.
            raise_cli_error(
                "--var was supplied without --message-template; "
                "either remove --var or pass --message-template NAME",
                exit_code=2,
            )
        return message if message is not None else [""]
    book = load_template_book()
    template = book.find(template_name)
    if template is None:
        raise_cli_error(
            f"no template named {template_name!r}; create it with 'postcards templates add' first",
            exit_code=2,
        )
    variables: dict[str, str] = {}
    for raw in var_args or []:
        key, value = _parse_var(raw)
        variables[key] = value
    try:
        rendered = template.render(variables)
    except TemplateRenderError as exc:
        raise_cli_error(str(exc), exit_code=2)
    return [rendered]


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
            "single spaces. Ignored when --message-template is given."
        ),
    ),
    to: str | None = typer.Option(
        None,
        "--to",
        help=(
            "Name of a recipient in the address book. Overrides the "
            "'recipient' block of the config file."
        ),
    ),
    sender: str | None = typer.Option(
        None,
        "--sender",
        help=(
            "Name of a sender in the address book. Overrides the 'sender' block of the config file."
        ),
    ),
    message_template: str | None = typer.Option(
        None,
        "--message-template",
        help=(
            "Name of a message template. The template is rendered "
            "with --var substitutions and used as the message. "
            "Mutually exclusive with --message."
        ),
    ),
    var: list[str] | None = typer.Option(
        None,
        "--var",
        "-V",
        help=(
            "Template variable in KEY=VALUE form. Repeat to pass multiple. "
            "Only meaningful with --message-template."
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

    The config file must contain at least one ``accounts`` entry
    (see ``postcards config init`` for a starter template). The
    recipient, sender, and message can be supplied inline or via
    the address book / template book (``--to`` / ``--sender`` /
    ``--message-template``).
    """
    if picture is None and message is None and message_template is None:
        raise_cli_error(
            "either --picture, --message, or --message-template is required",
            exit_code=2,
        )
    if message_template is not None and message:
        raise_cli_error(
            "--message and --message-template are mutually exclusive",
            exit_code=2,
        )

    # Resolve the in-memory config: read the file (if it exists)
    # and overlay address-book overrides for recipient / sender.
    # A missing config file is acceptable when no override is
    # requested AND the user has supplied a username / password
    # via flags or env vars; we still need a config dict shape
    # to pass to ``do_command_send`` so we seed it with ``{}``.
    config_data: dict = {}
    config_path = config_file
    if config_path.is_file():
        config_data = read_config(config_path)

    book = load_address_book() if (to is not None or sender is not None) else None
    if book is not None and to is not None:
        entry = _resolve_recipient_entry(to, book=book)
        config_data["recipient"] = _address_to_legacy_dict(entry)
    if book is not None and sender is not None:
        entry = _resolve_sender_entry(sender, book=book)
        config_data["sender"] = _address_to_legacy_dict(entry)

    message_parts = _resolve_message(
        message=message,
        template_name=message_template,
        var_args=var,
    )

    cards = Postcards()
    args = _build_namespace(
        config_file=config_path,
        picture=picture,
        message=message_parts,
        dry_run=dry_run,
        mock=mock,
        username=username,
        password=password,
        all_accounts=all_accounts,
        key=key,
        accounts_file=accounts_file,
    )
    try:
        cards.do_command_send(args, config_dict=config_data)
    except TemplateError as exc:
        # Defensive: ``_resolve_*`` already converts these to
        # CLI errors; this catch only exists if a future
        # refactor introduces a TemplateError at a new code path.
        raise_cli_error(str(exc), exit_code=2)


__all__ = ["send_cmd"]
