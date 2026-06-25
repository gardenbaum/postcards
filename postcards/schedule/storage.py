"""Disk persistence for :class:`ScheduleBook`.

Mirrors :mod:`postcards.addressbook.storage`: small loader /
saver pair, atomic writes (sibling temp file + ``os.replace``),
JSON envelope. The book is small (tens of jobs, not thousands)
so the whole-file approach is fine.

Why a single file (and not per-job files)
-----------------------------------------

The address book and template book each keep one entry per
file because the user manages them individually (each entry has
its own name, and the CLI exposes ``add`` / ``update`` /
``remove`` per entry). The schedule book is different — the
runner atomically replaces the whole queue on every dispatch,
so a per-job layout would just multiply the number of file
operations without simplifying any of them.

Why atomic writes
-----------------

The runner rewrites the file on every ``schedule run`` /
``schedule add`` / ``schedule remove``. A non-atomic write
truncated by a crash (power loss, OOM) would leave a
half-written file the next runner cannot parse. The temp-file +
``os.replace`` pattern is the same one the address book uses
because it is the simplest way to make the write all-or-nothing
on POSIX.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from postcards.schedule.models import (
    ScheduleBook,
    ScheduleError,
)
from postcards.schedule.paths import (
    SCHEDULE_BOOK_FILENAME,
    schedule_path,
)


def load_schedule_book(path: Path | None = None) -> ScheduleBook:
    """Return the :class:`ScheduleBook` persisted at ``path``.

    When ``path`` is ``None`` the function resolves the default
    XDG location (:func:`schedule_path`). A missing file is
    treated as an empty book — this is the behaviour users want
    on first run, where ``schedule add`` creates the file for
    them.
    """
    target = path if path is not None else schedule_path()
    if not target.is_file():
        return ScheduleBook()
    data = _read_json(target)
    return ScheduleBook.from_dict(data)


def save_schedule_book(book: ScheduleBook, path: Path | None = None) -> Path:
    """Persist ``book`` to ``path`` atomically and return the path.

    Creates parent directories on demand. The temporary file
    uses the same parent directory as the destination so the
    rename stays on a single filesystem.
    """
    target = path if path is not None else schedule_path()
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
        raise ScheduleError(
            f"failed to parse {path}: {exc.msg} (line {exc.lineno}, col {exc.colno})"
        ) from exc
    if not isinstance(data, dict):
        raise ScheduleError(
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
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
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


__all__ = [
    "SCHEDULE_BOOK_FILENAME",
    "ScheduleBook",
    "load_schedule_book",
    "save_schedule_book",
]
