"""XDG-aware filesystem locations for the address book and templates.

The package follows the XDG Base Directory Specification for
user data (``$XDG_DATA_HOME`` or ``$HOME/.local/share``) rather
than the per-project ``config.json`` location the rest of the
CLI uses, because the address book and template book are
**user** data — they belong to the user, not to the project.
That means a user with multiple ``postcards`` projects on the
same machine shares one address book and one template book,
which is the conventional behaviour for CLI tools of this
shape.

Resolution rules
----------------

1. If the :data:`POSTCARDS_DATA_DIR` environment variable is set
   and non-empty, it overrides everything else. Tests use this
   to point the storage layer at ``tmp_path`` without touching
   the user's real data.
2. Otherwise honour ``$XDG_DATA_HOME`` (relative paths are
   resolved against ``$HOME`` per XDG spec).
3. Fall back to ``$HOME/.local/share``.

The returned path is created on demand so callers can write to
it immediately. The directory carries ``0o700`` permissions on
POSIX systems so other users on the same machine cannot read
the address book.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

#: Filename of the address-book JSON file inside the data dir.
ADDRESS_BOOK_FILENAME = "addressbook.json"

#: Filename of the message-template JSON file inside the data dir.
TEMPLATE_BOOK_FILENAME = "templates.json"

#: Sub-directory of the XDG data home where ``postcards`` keeps
#: its user data.
_POSTCARDS_DATA_SUBDIR = "postcards"


def data_dir(override: str | os.PathLike[str] | None = None) -> Path:
    """Return the directory the address book and templates live in.

    Parameters
    ----------
    override:
        Explicit path or env-var-style string. When given, the
        function skips the XDG resolution entirely and returns
        the path (after ``expanduser`` / ``resolve``). ``None``
        honours the :data:`POSTCARDS_DATA_DIR` env var, then
        falls back to the XDG rules described in the module
        docstring.
    """
    if override is None:
        env_value = os.environ.get("POSTCARDS_DATA_DIR")
        override = env_value or None
    if override is None:
        xdg_data_home = os.environ.get("XDG_DATA_HOME")
        if xdg_data_home:
            base = Path(xdg_data_home)
        else:
            home = os.environ.get("HOME") or str(Path.home())
            base = Path(home) / ".local" / "share"
        candidate = base / _POSTCARDS_DATA_SUBDIR
    else:
        candidate = Path(override).expanduser()
    candidate = candidate.resolve()
    candidate.mkdir(parents=True, exist_ok=True)
    _restrict_permissions(candidate)
    return candidate


def address_book_path(override: str | os.PathLike[str] | None = None) -> Path:
    """Return the absolute path of the on-disk address book."""
    return data_dir(override) / ADDRESS_BOOK_FILENAME


def template_book_path(override: str | os.PathLike[str] | None = None) -> Path:
    """Return the absolute path of the on-disk template book."""
    return data_dir(override) / TEMPLATE_BOOK_FILENAME


def _restrict_permissions(path: Path) -> None:
    """Best-effort ``0o700`` perms on the data directory.

    On non-POSIX platforms :func:`os.chmod` is a no-op for the
    high bits, so the call is wrapped in a ``try`` to keep the
    cross-platform contract honest. We intentionally do not
    propagate :class:`OSError` — a directory we can read and
    write to is enough for the CLI to function, even if we
    cannot tighten its permissions.
    """
    with contextlib.suppress(OSError):
        os.chmod(path, 0o700)


__all__ = [
    "ADDRESS_BOOK_FILENAME",
    "TEMPLATE_BOOK_FILENAME",
    "address_book_path",
    "data_dir",
    "template_book_path",
]
