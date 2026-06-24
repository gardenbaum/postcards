"""Persistent local send queue with delayed and recurring jobs.

This package owns the user-facing long-lived data the scheduler
reads and writes outside the per-project ``config.json``:

* :class:`ScheduleBook` — a list of :class:`ScheduledJob` records
  stored as JSON under ``$XDG_DATA_HOME/postcards/schedule.json``
  (overridable via :data:`POSTCARDS_DATA_DIR`).
* :class:`Clock` — pluggable time source so the runner is unit-
  testable without touching the wall clock.

The package is split from :mod:`postcards.addressbook` because
scheduling is a different shape of user data (it carries a next
run time, a recurrence rule, and a status), and the CLI exposes
it as a separate command group (``postcards schedule``).

Persistence rules (see :data:`postcards.addressbook.storage` for
the pattern this package mirrors):

1. Storage lives outside the repository — never commit a
   ``schedule.json`` to the repo. The default location honours
   :data:`XDG_DATA_HOME` (falls back to ``$HOME/.local/share``);
   tests can pin the location with :data:`POSTCARDS_DATA_DIR`.
2. Writes are atomic — the file is written to a sibling temp
   path and renamed into place, so a crash mid-write cannot
   corrupt an existing queue.

Public surface
--------------

* :mod:`.models` — :class:`ScheduledJob`, :class:`JobStatus`,
  :class:`RecurrenceRule`, :class:`ScheduleBook`, :class:`Clock`,
  :class:`SystemClock`, :class:`FakeClock`.
* :mod:`.paths` — :func:`schedule_path`.
* :mod:`.storage` — :func:`load_schedule_book`,
  :func:`save_schedule_book`.
* :mod:`.runner` — :func:`run_due_jobs`, :class:`ExecutionResult`,
  :class:`JobOutcome`.
"""

from __future__ import annotations

from postcards.schedule.models import (
    MAX_RECURRING_INTERVAL_DAYS,
    Clock,
    ExecutionResult,
    FakeClock,
    JobOutcome,
    JobStatus,
    RecurrenceRule,
    ScheduledJob,
    ScheduleBook,
    ScheduleError,
    SystemClock,
    new_job_id,
)
from postcards.schedule.paths import schedule_path
from postcards.schedule.runner import BackendFactory, QuotaExhaustedError, run_due_jobs
from postcards.schedule.storage import (
    SCHEDULE_BOOK_FILENAME,
    load_schedule_book,
    save_schedule_book,
)

__all__ = [
    "BackendFactory",
    "Clock",
    "ExecutionResult",
    "FakeClock",
    "JobOutcome",
    "JobStatus",
    "MAX_RECURRING_INTERVAL_DAYS",
    "QuotaExhaustedError",
    "RecurrenceRule",
    "SCHEDULE_BOOK_FILENAME",
    "ScheduledJob",
    "ScheduleBook",
    "ScheduleError",
    "SystemClock",
    "load_schedule_book",
    "new_job_id",
    "run_due_jobs",
    "save_schedule_book",
    "schedule_path",
]