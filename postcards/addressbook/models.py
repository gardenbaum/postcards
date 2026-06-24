"""Typed address-book and message-template models.

This module defines the user-facing dataclasses the address book
and template book are built from. They are deliberately small,
immutable, and JSON-friendly so they can be persisted to disk
without a third-party serialisation library.

Persistence shape
-----------------

Both :class:`AddressBook` and :class:`TemplateBook` round-trip
through :meth:`to_dict` / :meth:`from_dict` (the latter being a
:class:`classmethod` constructor). The on-disk schema is::

    {
        "version": 1,
        "entries": [...]   // for the address book
        "templates": [...] // for the template book
    }

The ``version`` field lets future migrations detect older files
without inferring the schema from the shape. Today the loader
only accepts ``version == 1``.

Naming rules
------------

Both books key their entries by ``name``. Names are validated
at construction time:

* non-empty after ``str.strip()``;
* at most :data:`MAX_NAME_LENGTH` characters;
* no leading or trailing whitespace.

The validator raises :class:`TemplateError` (the shared error
type for both books — the surface area is small enough that a
single error class is clearer than two). The CLI converts that
into a :class:`postcards.cli.errors.CLIError` with exit code 2.
"""

from __future__ import annotations

import collections.abc
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from postcards.addressbook.variables import render_template
from postcards.backend.base import AddressSpec

#: Maximum length of an address-book entry name or template name.
#: The cap mirrors typical shell-argument limits (PATH_MAX etc.)
#: and keeps the CLI's name-based lookups readable in tabular
#: output. 64 is plenty for human-meaningful identifiers like
#: ``"oma-luzern"`` or ``"birthday-greeting"``.
MAX_NAME_LENGTH = 64

#: Pattern for valid entry / template names. Lowercase letters,
#: digits, dash, underscore and dot are allowed; spaces and
#: punctuation are rejected so names are shell-safe and stay
#: unambiguous on the command line (``postcards addresses show
#: oma luzern`` would be ambiguous between one name with a space
#: and two).
_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class TemplateError(ValueError):
    """Raised when an address-book or template-book invariant is violated.

    Examples include invalid entry names, duplicate names on
    :meth:`AddressBook.add`, unknown names on
    :meth:`AddressBook.update` / :meth:`remove`, and malformed
    on-disk payloads.

    The error is a :class:`ValueError` subclass so callers that
    don't care about the precise reason can still catch it via
    the standard ``ValueError`` pathway. The CLI converts these
    into user-facing messages with exit code 2.
    """


def _validate_name(value: str, *, kind: str) -> str:
    """Return ``value`` if it satisfies the name rules.

    Raises :class:`TemplateError` otherwise. The ``kind``
    argument names the entity being validated (``"entry"`` /
    ``"template"``) so the error message reads naturally.
    """
    if not isinstance(value, str):
        raise TemplateError(f"{kind} name must be a string, got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        raise TemplateError(f"{kind} name must not be empty")
    if stripped != value:
        raise TemplateError(f"{kind} name must not have leading or trailing whitespace")
    if len(value) > MAX_NAME_LENGTH:
        raise TemplateError(
            f"{kind} name {value!r} is too long ({len(value)} > {MAX_NAME_LENGTH} characters)"
        )
    if not _NAME_PATTERN.match(value):
        raise TemplateError(
            f"{kind} name {value!r} must match {_NAME_PATTERN.pattern!r} "
            "(lowercase letters, digits, dot, dash, underscore; "
            "must start with a letter or digit)"
        )
    return value


class AddressCategory(StrEnum):
    """Whether an :class:`AddressBookEntry` is a sender or recipient.

    The string value matches the canonical Swiss Post API
    terminology (``recipient`` / ``sender``) and is also the
    value stored in the JSON file. Inheriting from
    :class:`enum.StrEnum` keeps the on-disk format readable and
    lets the CLI match against ``category.value`` directly when
    filtering.
    """

    RECIPIENT = "recipient"
    SENDER = "sender"

    @classmethod
    def from_string(cls, value: str) -> AddressCategory:
        """Parse a CLI / on-disk string into an :class:`AddressCategory`.

        Accepts the canonical names (``recipient`` / ``sender``)
        and a handful of common aliases (``to`` → recipient,
        ``from`` → sender) so the CLI can be forgiving.
        """
        normalized = value.strip().lower()
        aliases: dict[str, AddressCategory] = {
            "recipient": cls.RECIPIENT,
            "to": cls.RECIPIENT,
            "sender": cls.SENDER,
            "from": cls.SENDER,
        }
        if normalized not in aliases:
            valid = ", ".join(sorted(member.value for member in cls))
            raise TemplateError(f"unknown address category {value!r}; expected one of {valid}")
        return aliases[normalized]


@dataclass(frozen=True)
class AddressBookEntry:
    """A named sender or recipient stored in the address book.

    The ``address`` is the same :class:`AddressSpec` the Swiss
    Post backend accepts — sharing the type with the rest of the
    codebase means an entry can be fed straight into
    :class:`postcards.models.Postcard` without translation.

    ``notes`` is an optional human-readable field for the user's
    own bookkeeping (``"summer-vacation"``, ``"work"``). It is
    never sent to the backend.
    """

    name: str
    category: AddressCategory
    address: AddressSpec
    notes: str = ""

    def __post_init__(self) -> None:
        # Frozen dataclasses need ``object.__setattr__`` to mutate
        # ``self.name`` from the validator; the validator returns
        # the canonical form so the rest of the field is untouched.
        object.__setattr__(self, "name", _validate_name(self.name, kind="entry"))
        if not isinstance(self.category, AddressCategory):
            # Coerce strings (e.g. from JSON loaders) into the enum.
            object.__setattr__(self, "category", AddressCategory(self.category))
        if not isinstance(self.address, AddressSpec):
            raise TemplateError(
                f"entry address must be an AddressSpec, got {type(self.address).__name__}"
            )
        if not isinstance(self.notes, str):
            raise TemplateError("entry notes must be a string")

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-friendly representation of this entry."""
        return {
            "name": self.name,
            "category": self.category.value,
            "address": {
                "prename": self.address.prename,
                "lastname": self.address.lastname,
                "street": self.address.street,
                "zip_code": self.address.zip_code,
                "place": self.address.place,
                "company": self.address.company,
                "country": self.address.country,
                "salutation": self.address.salutation,
                "company_addition": self.address.company_addition,
            },
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AddressBookEntry:
        """Build an :class:`AddressBookEntry` from its JSON-friendly dict.

        Raises :class:`TemplateError` when the payload is missing
        required keys or carries the wrong types. Missing
        optional fields default to ``""`` (matching
        :class:`AddressSpec`'s defaults).
        """
        try:
            name = payload["name"]
            category = payload["category"]
            address_payload = payload["address"]
        except KeyError as exc:
            raise TemplateError(f"address entry missing required field: {exc.args[0]}") from exc
        if not isinstance(address_payload, Mapping):
            raise TemplateError("address entry 'address' must be a mapping")
        address = AddressSpec(
            prename=str(address_payload.get("prename", "")),
            lastname=str(address_payload.get("lastname", "")),
            street=str(address_payload.get("street", "")),
            zip_code=str(address_payload.get("zip_code", "")),
            place=str(address_payload.get("place", "")),
            company=str(address_payload.get("company", "")),
            country=str(address_payload.get("country", "")),
            salutation=str(address_payload.get("salutation", "")),
            company_addition=str(address_payload.get("company_addition", "")),
        )
        return cls(
            name=name,
            category=category,
            address=address,
            notes=str(payload.get("notes", "")),
        )


@dataclass(frozen=True)
class AddressBook:
    """An ordered collection of :class:`AddressBookEntry` records.

    The book is a value type — :meth:`add`, :meth:`update` and
    :meth:`remove` return *new* books rather than mutating
    ``self`` (consistent with the rest of the project's
    frozen-dataclass discipline). Tests and call sites can
    therefore build new books by expression without worrying
    about aliasing.

    ``entries`` is stored as a tuple ordered by insertion time;
    lookups by name are linear, which is fine because the
    address book is expected to hold dozens, not thousands, of
    entries.
    """

    entries: tuple[AddressBookEntry, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for entry in self.entries:
            if entry.name in seen:
                raise TemplateError(f"duplicate entry name {entry.name!r} in address book")
            seen.add(entry.name)

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def get(self, name: str) -> AddressBookEntry:
        """Return the entry named ``name``.

        Raises :class:`TemplateError` when no such entry exists;
        callers that want a ``None`` return should use
        :meth:`find` instead.
        """
        for entry in self.entries:
            if entry.name == name:
                return entry
        raise TemplateError(f"no address-book entry named {name!r}")

    def find(self, name: str) -> AddressBookEntry | None:
        """Return the entry named ``name`` or ``None`` if absent."""
        for entry in self.entries:
            if entry.name == name:
                return entry
        return None

    def names(self) -> tuple[str, ...]:
        """Return the entry names in insertion order."""
        return tuple(entry.name for entry in self.entries)

    def filter(self, *, category: AddressCategory | None = None) -> AddressBook:
        """Return a new book containing only entries of ``category``.

        ``category=None`` returns a copy of the whole book. The
        copy preserves insertion order.
        """
        if category is None:
            return AddressBook(entries=self.entries)
        return AddressBook(entries=tuple(e for e in self.entries if e.category == category))

    def is_empty(self) -> bool:
        return not self.entries

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> collections.abc.Iterator[AddressBookEntry]:
        # ``frozen=True`` dataclasses honour ``__iter__`` for
        # sequence semantics; returning an iterator (rather
        # than the underlying tuple) keeps the public type
        # honest and avoids confusing static analysers that
        # expect ``Iterable`` to be lazy.
        return iter(self.entries)

    # ------------------------------------------------------------------
    # Mutations (return new books)
    # ------------------------------------------------------------------

    def add(self, entry: AddressBookEntry) -> AddressBook:
        """Return a new book with ``entry`` appended.

        Raises :class:`TemplateError` if an entry with the same
        name already exists.
        """
        if any(e.name == entry.name for e in self.entries):
            raise TemplateError(f"address-book entry {entry.name!r} already exists")
        return AddressBook(entries=(*self.entries, entry))

    def update(self, entry: AddressBookEntry) -> AddressBook:
        """Return a new book with ``entry`` replacing the existing one.

        Raises :class:`TemplateError` if no entry with that name
        exists. The position of the replaced entry is preserved
        so list ordering stays stable for the user.
        """
        for index, existing in enumerate(self.entries):
            if existing.name == entry.name:
                new_entries = list(self.entries)
                new_entries[index] = entry
                return AddressBook(entries=tuple(new_entries))
        raise TemplateError(f"cannot update unknown address-book entry {entry.name!r}")

    def remove(self, name: str) -> AddressBook:
        """Return a new book without the entry named ``name``.

        Raises :class:`TemplateError` if no such entry exists.
        """
        new_entries = tuple(e for e in self.entries if e.name != name)
        if len(new_entries) == len(self.entries):
            raise TemplateError(f"cannot remove unknown address-book entry {name!r}")
        return AddressBook(entries=new_entries)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AddressBook:
        try:
            version = payload["version"]
        except KeyError as exc:
            raise TemplateError("address-book file missing 'version' field") from exc
        if version != 1:
            raise TemplateError(
                f"unsupported address-book version {version!r}; this build only reads version 1"
            )
        raw_entries = payload.get("entries", [])
        if not isinstance(raw_entries, list):
            raise TemplateError("address-book 'entries' must be a list")
        entries = tuple(AddressBookEntry.from_dict(item) for item in raw_entries)
        return cls(entries=entries)


@dataclass(frozen=True)
class MessageTemplate:
    """A reusable message with ``{variable}`` placeholders.

    The body uses Python's :class:`string.Template` syntax
    (``$name`` and ``${name}``), which is the conventional
    choice for user-authored templates because the dollar sign
    is unlikely to appear in a real postcard message and the
    substitution rules are well-defined.

    ``description`` is a free-form field the user can fill in
    to remember what a template is for. It is shown in the
    ``templates list`` output but never inserted into a
    rendered message.
    """

    name: str
    body: str
    description: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _validate_name(self.name, kind="template"))
        if not isinstance(self.body, str):
            raise TemplateError("template body must be a string")
        if not isinstance(self.description, str):
            raise TemplateError("template description must be a string")

    def render(self, variables: Mapping[str, object]) -> str:
        """Render this template with ``variables``.

        The rendering is delegated to :func:`render_template`
        so the substitution rules live in one place. Strict
        missing-key semantics — a referenced variable that is
        not supplied raises :class:`TemplateRenderError` —
        prevents silent rendering of an unfinished template.
        """
        return render_template(self.body, variables)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "body": self.body,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> MessageTemplate:
        try:
            name = payload["name"]
            body = payload["body"]
        except KeyError as exc:
            raise TemplateError(f"template missing required field: {exc.args[0]}") from exc
        return cls(
            name=name,
            body=body,
            description=str(payload.get("description", "")),
        )


@dataclass(frozen=True)
class TemplateBook:
    """An ordered collection of :class:`MessageTemplate` records.

    Same value-type discipline as :class:`AddressBook` —
    :meth:`add`, :meth:`update`, :meth:`remove` return new
    instances.
    """

    templates: tuple[MessageTemplate, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for template in self.templates:
            if template.name in seen:
                raise TemplateError(f"duplicate template name {template.name!r} in template book")
            seen.add(template.name)

    def get(self, name: str) -> MessageTemplate:
        for template in self.templates:
            if template.name == name:
                return template
        raise TemplateError(f"no template named {name!r}")

    def find(self, name: str) -> MessageTemplate | None:
        for template in self.templates:
            if template.name == name:
                return template
        return None

    def names(self) -> tuple[str, ...]:
        return tuple(t.name for t in self.templates)

    def is_empty(self) -> bool:
        return not self.templates

    def __len__(self) -> int:
        return len(self.templates)

    def __iter__(self) -> collections.abc.Iterator[MessageTemplate]:
        return iter(self.templates)

    def add(self, template: MessageTemplate) -> TemplateBook:
        if any(t.name == template.name for t in self.templates):
            raise TemplateError(f"template {template.name!r} already exists")
        return TemplateBook(templates=(*self.templates, template))

    def update(self, template: MessageTemplate) -> TemplateBook:
        for index, existing in enumerate(self.templates):
            if existing.name == template.name:
                new_templates = list(self.templates)
                new_templates[index] = template
                return TemplateBook(templates=tuple(new_templates))
        raise TemplateError(f"cannot update unknown template {template.name!r}")

    def remove(self, name: str) -> TemplateBook:
        new_templates = tuple(t for t in self.templates if t.name != name)
        if len(new_templates) == len(self.templates):
            raise TemplateError(f"cannot remove unknown template {name!r}")
        return TemplateBook(templates=new_templates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "templates": [t.to_dict() for t in self.templates],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> TemplateBook:
        try:
            version = payload["version"]
        except KeyError as exc:
            raise TemplateError("template file missing 'version' field") from exc
        if version != 1:
            raise TemplateError(
                f"unsupported template-book version {version!r}; this build only reads version 1"
            )
        raw = payload.get("templates", [])
        if not isinstance(raw, list):
            raise TemplateError("template file 'templates' must be a list")
        templates = tuple(MessageTemplate.from_dict(item) for item in raw)
        return cls(templates=templates)


__all__ = [
    "MAX_NAME_LENGTH",
    "AddressBook",
    "AddressBookEntry",
    "AddressCategory",
    "MessageTemplate",
    "TemplateBook",
    "TemplateError",
]
