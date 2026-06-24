"""XDG-aware filesystem location for the schedule book.

Mirrors :mod:`postcards.addressbook.paths`: the schedule book is
user data, not project data, so it lives under
``$XDG_DATA_HOME/postcards/`` (falling back to
``$HOME/.local/share``) and tests can pin the location with
:data:`POSTCARDS_DATA_DIR`.

The schedule book is intentionally a **single file** rather than
a per-job file because:

* the queue is expected to hold dozens of jobs, not thousands;
* atomic JSON-overwrite is the same pattern the address book
  uses, so the storage layer is uniform;
* a single file is easier to back up and to inspect by hand.
"""

from __future__ import annotations

from pathlib import Path

from postcards.addressbook.paths import data_dir

#: Filename of the schedule-book JSON file inside the data dir.
SCHEDULE_BOOK_FILENAME = "schedule.json"


def schedule_path(override: str | None = None) -> Path:
    """Return the absolute path of the on-disk schedule book.

    See :func:`postcards.addressbook.paths.data_dir` for the
    resolution rules. ``override`` honours the same semantics as
    the address-book / template-book paths.
    """
    return data_dir(override) / SCHEDULE_BOOK_FILENAME


__all__ = ["SCHEDULE_BOOK_FILENAME", "schedule_path"]