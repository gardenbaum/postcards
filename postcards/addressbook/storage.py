"""Disk persistence for :class:`AddressBook` and :class:`TemplateBook`.

The two loaders / savers are deliberately small — they read /
write the JSON envelope each book defines via its
:meth:`to_dict` / :meth:`from_dict` methods, and they do atomic
writes (temp file + ``os.replace``) so a crash mid-write cannot
leave a half-written book behind.

Why atomic writes
-----------------

``os.replace`` is atomic on POSIX and on Windows when the
destination already exists. We write to a sibling temporary
file in the same directory (so the rename stays on the same
filesystem), :func:`os.fsync` the file so the bytes are on
disk before the rename, and only then call ``os.replace``. The
goal is that the address-book file is *always* either the old
version or the new version — never a truncated mix of both.

Why JSON (not SQLite / not pickle)
----------------------------------

* The book is small (tens, not thousands, of entries) and the
  whole file fits comfortably in memory.
* JSON is portable — a user can inspect and edit the file with
  any text editor, and the schema is human-readable.
* We never load untrusted pickle data; ``pickle`` would be a
  remote-code-execution risk on a corrupted or tampered file.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any

from postcards.addressbook.models import (
    AddressBook,
    AddressBookEntry,
    MessageTemplate,
    TemplateBook,
    TemplateError,
)
from postcards.addressbook.paths import (
    ADDRESS_BOOK_FILENAME,
    TEMPLATE_BOOK_FILENAME,
    address_book_path,
    template_book_path,
)


def load_address_book(path: Path | None = None) -> AddressBook:
    """Return the :class:`AddressBook` persisted at ``path``.

    When ``path`` is ``None`` the function resolves the default
    XDG location (:func:`postcards.addressbook.paths.address_book_path`).
    A missing file is treated as an empty book — this is the
    behaviour users want on first run, where ``addresses add``
    creates the file for them.
    """
    target = path if path is not None else address_book_path()
    if not target.is_file():
        return AddressBook()
    data = _read_json(target)
    return AddressBook.from_dict(data)


def save_address_book(book: AddressBook, path: Path | None = None) -> Path:
    """Persist ``book`` to ``path`` atomically and return the path.

    Creates parent directories on demand. The temporary file
    uses the same parent directory as the destination so the
    rename stays on a single filesystem.
    """
    target = path if path is not None else address_book_path()
    payload = book.to_dict()
    _write_json_atomic(target, payload)
    return target


def load_template_book(path: Path | None = None) -> TemplateBook:
    """Return the :class:`TemplateBook` persisted at ``path``.

    Same missing-file semantics as :func:`load_address_book`.
    """
    target = path if path is not None else template_book_path()
    if not target.is_file():
        return TemplateBook()
    data = _read_json(target)
    return TemplateBook.from_dict(data)


def save_template_book(book: TemplateBook, path: Path | None = None) -> Path:
    """Persist ``book`` to ``path`` atomically and return the path."""
    target = path if path is not None else template_book_path()
    payload = book.to_dict()
    _write_json_atomic(target, payload)
    return target


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any]:
    """Read a JSON object from ``path`` with a helpful error."""
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise TemplateError(
            f"failed to parse {path}: {exc.msg} (line {exc.lineno}, col {exc.colno})"
        ) from exc
    if not isinstance(data, dict):
        raise TemplateError(
            f"{path} must contain a JSON object at the top level, got {type(data).__name__}"
        )
    return data


def _write_json_atomic(target: Path, payload: dict[str, Any]) -> None:
    """Write ``payload`` to ``target`` via a sibling temp file.

    The temp file uses ``.tmp-<random>`` as a suffix so a stale
    temp file from a previous crash does not collide. The
    rename is :func:`os.replace` which is atomic on POSIX and
    on Windows when the destination exists.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = _open_unique_temp(target.parent)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        # If anything goes wrong before the rename, clean up
        # the temp file so the data directory does not accrete
        # ``.tmp-*`` litter over time.
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_path)
        raise


def _open_unique_temp(parent: Path) -> tuple[int, str]:
    """Open a unique ``.tmp-XXXXXXXX`` file in ``parent``.

    Wrapping :func:`os.open` with ``O_EXCL | O_CREAT`` (via
    :func:`tempfile.mkstemp`) is necessary because we want
    exclusive creation — :func:`tempfile.NamedTemporaryFile``
    would open the file before we can ``os.fsync`` it cleanly.
    """
    import tempfile

    fd, name = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=str(parent))
    return fd, name


# Re-export the model classes that loaders / savers produce so
# the CLI can ``from postcards.addressbook.storage import
# AddressBook`` without re-importing from ``models``.
__all__ = [
    "ADDRESS_BOOK_FILENAME",
    "TEMPLATE_BOOK_FILENAME",
    "AddressBook",
    "AddressBookEntry",
    "MessageTemplate",
    "TemplateBook",
    "load_address_book",
    "load_template_book",
    "save_address_book",
    "save_template_book",
]
