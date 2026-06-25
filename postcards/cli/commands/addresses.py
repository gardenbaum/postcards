"""``postcards addresses {add,list,show,update,remove}`` — manage the address book.

This command group is the user-facing surface for the
:class:`postcards.addressbook.models.AddressBook` data. It
mirrors the shape of the legacy :mod:`postcards.cli.config_io`
helpers but operates on the per-user data directory
(``$XDG_DATA_HOME/postcards/addressbook.json`` by default,
overridable via :data:`POSTCARDS_DATA_DIR`).

CLI surface
-----------

* ``addresses add NAME [--category recipient|sender] [--prename ...] ...``
  — create a new entry.
* ``addresses list [--category recipient|sender]`` — tabular
  summary of the book.
* ``addresses show NAME`` — print a single entry, JSON-style.
* ``addresses update NAME [--prename ...] ...`` — patch one or
  more fields of an existing entry.
* ``addresses remove NAME`` — delete an entry.

Persistence is delegated to :mod:`postcards.addressbook.storage`,
so the CLI body stays focused on argument parsing and
user-facing error messages.
"""

from __future__ import annotations

from typing import Annotated

import typer

from postcards.addressbook.models import (
    AddressBookEntry,
    AddressCategory,
    TemplateError,
)
from postcards.addressbook.storage import (
    load_address_book,
    save_address_book,
)
from postcards.backend.base import AddressSpec
from postcards.cli.app import app
from postcards.cli.errors import raise_cli_error

addresses_app = typer.Typer(
    name="addresses",
    help="Manage the address book (recipients + senders) under $XDG_DATA_HOME/postcards.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app.add_typer(addresses_app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_ADDRESS_FIELDS: tuple[str, ...] = (
    "prename",
    "lastname",
    "street",
    "zip_code",
    "place",
    "company",
    "country",
    "salutation",
    "company_addition",
)


def _build_address_from_args(
    *,
    prename: str | None,
    lastname: str | None,
    street: str | None,
    zip_code: str | None,
    place: str | None,
    company: str | None,
    country: str | None,
    salutation: str | None,
    company_addition: str | None,
    existing: AddressSpec | None = None,
) -> AddressSpec:
    """Compose an :class:`AddressSpec` from CLI overrides layered on ``existing``.

    ``None`` overrides mean "keep the existing value"; ``""``
    overrides mean "explicitly clear the field". This split is
    what makes ``addresses update`` useful — it can keep the
    prename but change the street without forcing the user to
    re-supply every field.
    """
    base = existing or AddressSpec(
        prename="",
        lastname="",
        street="",
        zip_code="",
        place="",
    )

    def pick(new: str | None, current: str) -> str:
        return current if new is None else new

    return AddressSpec(
        prename=pick(prename, base.prename),
        lastname=pick(lastname, base.lastname),
        street=pick(street, base.street),
        zip_code=pick(zip_code, base.zip_code),
        place=pick(place, base.place),
        company=pick(company, base.company),
        country=pick(country, base.country),
        salutation=pick(salutation, base.salutation),
        company_addition=pick(company_addition, base.company_addition),
    )


def _coerce_category(value: str) -> AddressCategory:
    """Parse the ``--category`` flag with a CLI-friendly error."""
    try:
        return AddressCategory.from_string(value)
    except TemplateError as exc:
        raise_cli_error(str(exc), exit_code=2)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


@addresses_app.command(
    name="add",
    help="Add a new recipient or sender to the address book.",
)
def addresses_add(
    name: Annotated[
        str,
        typer.Argument(help="Unique identifier for the entry (e.g. 'oma', 'alice-work')."),
    ],
    category: Annotated[
        str,
        typer.Option(
            "--category",
            "-c",
            help="Whether this is a 'recipient' or a 'sender'.",
        ),
    ] = "recipient",
    prename: str | None = typer.Option(None, "--prename", help="First name."),
    lastname: str | None = typer.Option(None, "--lastname", help="Last name."),
    street: str | None = typer.Option(None, "--street", help="Street address."),
    zip_code: str | None = typer.Option(None, "--zip-code", help="Postal / ZIP code."),
    place: str | None = typer.Option(None, "--place", help="City or town."),
    company: str | None = typer.Option(None, "--company", help="Company (optional)."),
    country: str | None = typer.Option(
        None, "--country", help="Country code (e.g. 'CH'). Senders only."
    ),
    salutation: str | None = typer.Option(
        None, "--salutation", help="Greeting line (e.g. 'Mr.', 'Ms.'). Recipients only."
    ),
    company_addition: str | None = typer.Option(
        None,
        "--company-addition",
        help="Department / role (optional).",
    ),
    notes: str | None = typer.Option(
        None, "--notes", help="Free-form notes (e.g. 'vacation 2024')."
    ),
) -> None:
    """Append ``name`` to the address book.

    The required postal fields (``--prename``, ``--lastname``,
    ``--street``, ``--zip-code``, ``--place``) can be omitted
    on creation — a partial entry is allowed — but the entry
    will not be accepted by ``postcards send`` until it carries
    enough data to satisfy :meth:`AddressSpec.is_valid`.
    """
    parsed_category = _coerce_category(category)
    address = _build_address_from_args(
        prename=prename,
        lastname=lastname,
        street=street,
        zip_code=zip_code,
        place=place,
        company=company,
        country=country,
        salutation=salutation,
        company_addition=company_addition,
    )
    try:
        entry = AddressBookEntry(
            name=name,
            category=parsed_category,
            address=address,
            notes=notes or "",
        )
    except TemplateError as exc:
        raise_cli_error(str(exc), exit_code=2)
    book = load_address_book()
    try:
        new_book = book.add(entry)
    except TemplateError as exc:
        raise_cli_error(str(exc), exit_code=2)
    save_address_book(new_book)
    typer.echo(f"added {parsed_category.value} {name!r} to the address book")


@addresses_app.command(
    name="list",
    help="List address-book entries (filterable by category).",
)
def addresses_list(
    category: Annotated[
        str | None,
        typer.Option(
            "--category",
            "-c",
            help="Filter by category (recipient / sender).",
        ),
    ] = None,
) -> None:
    """Print a tabular summary of the address book.

    The output is two columns (``name`` and ``category``);
    ``postcards addresses show NAME`` is the way to see the
    full address. An empty book prints a hint pointing the
    user at ``addresses add``.
    """
    book = load_address_book()
    parsed_category = _coerce_category(category) if category else None
    filtered = book.filter(category=parsed_category) if parsed_category else book
    if filtered.is_empty():
        typer.echo("address book is empty; add an entry with 'postcards addresses add NAME'")
        return
    typer.echo(f"{'NAME':<24} CATEGORY    PLACE")
    for entry in filtered:
        typer.echo(f"{entry.name:<24} {entry.category.value:<11} {entry.address.place}")


@addresses_app.command(
    name="show",
    help="Show a single address-book entry.",
)
def addresses_show(
    name: Annotated[str, typer.Argument(help="Name of the entry to display.")],
) -> None:
    """Print a single entry's full record as key/value pairs."""
    book = load_address_book()
    entry = book.find(name)
    if entry is None:
        raise_cli_error(f"no address-book entry named {name!r}", exit_code=2)
    typer.echo(f"name      : {entry.name}")
    typer.echo(f"category  : {entry.category.value}")
    typer.echo(f"prename   : {entry.address.prename}")
    typer.echo(f"lastname  : {entry.address.lastname}")
    typer.echo(f"street    : {entry.address.street}")
    typer.echo(f"zip_code  : {entry.address.zip_code}")
    typer.echo(f"place     : {entry.address.place}")
    typer.echo(f"company   : {entry.address.company}")
    typer.echo(f"country   : {entry.address.country}")
    typer.echo(f"salutation: {entry.address.salutation}")
    typer.echo(f"company_a : {entry.address.company_addition}")
    if entry.notes:
        typer.echo(f"notes     : {entry.notes}")


@addresses_app.command(
    name="update",
    help="Patch one or more fields of an existing entry.",
)
def addresses_update(
    name: Annotated[str, typer.Argument(help="Name of the entry to update.")],
    prename: str | None = typer.Option(None, "--prename", help="First name."),
    lastname: str | None = typer.Option(None, "--lastname", help="Last name."),
    street: str | None = typer.Option(None, "--street", help="Street address."),
    zip_code: str | None = typer.Option(None, "--zip-code", help="Postal / ZIP code."),
    place: str | None = typer.Option(None, "--place", help="City or town."),
    company: str | None = typer.Option(None, "--company", help="Company (pass '' to clear)."),
    country: str | None = typer.Option(None, "--country", help="Country code (pass '' to clear)."),
    salutation: str | None = typer.Option(
        None, "--salutation", help="Greeting (pass '' to clear)."
    ),
    company_addition: str | None = typer.Option(
        None, "--company-addition", help="Department / role (pass '' to clear)."
    ),
    notes: str | None = typer.Option(
        None,
        "--notes",
        help="Free-form notes. Pass an empty string to clear.",
    ),
) -> None:
    """Patch fields of an existing :class:`AddressBookEntry`.

    Any option left unset (``None``) keeps the existing value;
    an empty string (``""``) explicitly clears the field. This
    two-valued semantics mirrors ``typer.Option``'s distinction
    between "not supplied" and "supplied with an empty value".
    """
    book = load_address_book()
    existing = book.find(name)
    if existing is None:
        raise_cli_error(
            f"no address-book entry named {name!r}; create it first with 'postcards addresses add'",
            exit_code=2,
        )

    new_address = _build_address_from_args(
        prename=prename,
        lastname=lastname,
        street=street,
        zip_code=zip_code,
        place=place,
        company=company,
        country=country,
        salutation=salutation,
        company_addition=company_addition,
        existing=existing.address,
    )
    new_notes = existing.notes if notes is None else notes
    updated = AddressBookEntry(
        name=existing.name,
        category=existing.category,
        address=new_address,
        notes=new_notes,
    )
    try:
        new_book = book.update(updated)
    except TemplateError as exc:
        raise_cli_error(str(exc), exit_code=2)
    save_address_book(new_book)
    typer.echo(f"updated address-book entry {name!r}")


@addresses_app.command(
    name="remove",
    help="Remove an entry from the address book.",
)
def addresses_remove(
    name: Annotated[str, typer.Argument(help="Name of the entry to remove.")],
    yes: Annotated[
        bool,
        typer.Option(
            "-y",
            "--yes",
            help="Skip the confirmation prompt.",
        ),
    ] = False,
) -> None:
    """Delete the entry named ``name``.

    ``--yes`` skips the confirmation prompt for scripting. The
    command refuses to operate on an unknown name rather than
    silently succeeding.
    """
    book = load_address_book()
    if book.find(name) is None:
        raise_cli_error(
            f"no address-book entry named {name!r}",
            exit_code=2,
        )
    if not yes:
        confirmed = typer.confirm(f"remove address-book entry {name!r}?", default=False)
        if not confirmed:
            typer.echo("aborted")
            raise typer.Exit(code=1)
    try:
        new_book = book.remove(name)
    except TemplateError as exc:
        raise_cli_error(str(exc), exit_code=2)
    save_address_book(new_book)
    typer.echo(f"removed {name!r} from the address book")


__all__ = ["addresses_app"]
