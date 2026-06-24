"""Persistent address book and message templates.

This package owns the user-facing long-lived data the CLI reads
and writes outside the per-project ``config.json``:

* :class:`AddressBook` — named recipients and senders stored
  under the XDG data directory (``$XDG_DATA_HOME/postcards/`` by
  default, overridable via :data:`POSTCARDS_DATA_DIR`).
* :class:`TemplateBook` — named message templates with simple
  ``{variable}`` substitution, stored in the same directory.

The split between the two is intentional — they are different
shapes of user data and the CLI exposes them as separate command
groups (``postcards addresses`` and ``postcards templates``).
The storage layout keeps one JSON file per book so the two can
evolve independently without cross-file migrations.

Persistence rules (see ``docs/CONSTITUTION.md`` §2 adapted):

1. Storage lives outside the repository — never commit an
   ``addressbook.json`` or ``templates.json`` to the repo. The
   default location honours ``$XDG_DATA_HOME`` (falls back to
   ``$HOME/.local/share``); tests can pin the location with
   :data:`POSTCARDS_DATA_DIR`.
2. Writes are atomic — the file is written to a sibling temp
   path and renamed into place, so a crash mid-write cannot
   corrupt an existing book.
3. Entries are referenced by name. The CLI refuses to add a
   duplicate name; ``update`` rejects unknown names so typos
   surface as a usage error rather than a silent no-op.

Public surface
--------------

* :mod:`.models` — :class:`AddressBookEntry`, :class:`AddressBook`,
  :class:`MessageTemplate`, :class:`TemplateBook`,
  :class:`AddressCategory`.
* :mod:`.paths` — :func:`data_dir` (XDG resolution).
* :mod:`.storage` — :func:`load_address_book`,
  :func:`save_address_book`, :func:`load_template_book`,
  :func:`save_template_book`.
* :mod:`.variables` — :func:`render_template` (``{name}``-style
  substitution with strict missing-key semantics).
"""

from __future__ import annotations

from postcards.addressbook.models import (
    MAX_NAME_LENGTH,
    AddressBook,
    AddressBookEntry,
    AddressCategory,
    MessageTemplate,
    TemplateBook,
    TemplateError,
)
from postcards.addressbook.storage import (
    ADDRESS_BOOK_FILENAME,
    TEMPLATE_BOOK_FILENAME,
    load_address_book,
    load_template_book,
    save_address_book,
    save_template_book,
)
from postcards.addressbook.variables import (
    TemplateRenderError,
    render_template,
)

__all__ = [
    "ADDRESS_BOOK_FILENAME",
    "MAX_NAME_LENGTH",
    "TEMPLATE_BOOK_FILENAME",
    "AddressBook",
    "AddressBookEntry",
    "AddressCategory",
    "MessageTemplate",
    "TemplateBook",
    "TemplateError",
    "TemplateRenderError",
    "load_address_book",
    "load_template_book",
    "render_template",
    "save_address_book",
    "save_template_book",
]
