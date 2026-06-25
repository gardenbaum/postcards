"""Typed schedule-book models and the :class:`Clock` abstraction.

This module defines the dataclasses and the time source the
schedule runner reads / writes. They are deliberately small,
immutable, and JSON-friendly so they round-trip through the
storage layer without a third-party serialisation library.

Persistence shape
-----------------

:class:`ScheduleBook` round-trips through :meth:`to_dict` /
:meth:`from_dict` (the latter being a :class:`classmethod`
constructor). The on-disk schema is::

    {
        "version": 1,
        "jobs": [...]
    }

The ``version`` field lets future migrations detect older files
without inferring the schema from the shape. Today the loader
only accepts ``version == 1``.

Job shape
---------

A :class:`ScheduledJob` carries the recipient / sender / message
inputs the CLI captured at ``schedule add`` time, plus a
:class:`RecurrenceRule`, a status, and bookkeeping timestamps.
Jobs are addressed by an opaque ``id`` (``uuid4`` hex string) so
``postcards schedule show`` / ``remove`` have a stable handle
independent of job content.

Time source
-----------

The runner reads "now" through a :class:`Clock` protocol rather
than calling :func:`datetime.datetime.now` directly. The
production runner uses :class:`SystemClock`; tests inject
:class:`FakeClock` to advance time deterministically. The
abstraction is intentionally minimal — just :meth:`Clock.now` —
because the runner only needs a single monotonic timestamp.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

#: Maximum interval (in days) allowed on a recurring rule.
#:
#: The cap is a guardrail against typos (``--recurring every:999d``
#: is almost certainly wrong) and against runaway scheduling. It is
#: well above any reasonable real-world interval (the upstream
#: Swiss Post tier allows 1 free card / day, so anything longer
#: than a week is unusual).
MAX_RECURRING_INTERVAL_DAYS = 365

#: Pattern that matches ``every:Nd`` recurrence strings. ``N`` is
#: captured so the parser can validate the upper bound against
#: :data:`MAX_RECURRING_INTERVAL_DAYS`.
_EVERY_DAYS_PATTERN = re.compile(r"^every:(\d+)d$")

#: Pattern that matches ``weekly:DAY[,DAY...]`` recurrence strings.
#: ``DAY`` is one of ``mon`` / ``tue`` / ``wed`` / ``thu`` / ``fri``
#: / ``sat`` / ``sun`` (case-insensitive). The full list of days is
#: captured so the parser can reject unknown abbreviations.
_WEEKLY_DAY_NAMES: dict[str, int] = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


class ScheduleError(ValueError):
    """Raised when a schedule-book invariant is violated.

    Examples include invalid recurrence strings, ``next_run_at``
    timestamps that cannot be parsed, and unknown on-disk job
    fields. The error is a :class:`ValueError` subclass so callers
    that don't care about the precise reason can still catch it via
    the standard ``ValueError` pathway.
    """


class JobStatus(StrEnum):
    """The lifecycle state of a :class:`ScheduledJob`.

    * ``PENDING`` — the job is in the queue and waiting to be
      dispatched (initial state and the state recurring jobs
      return to after a successful run).
    * ``RUNNING`` — the runner has picked up the job and is in
      the middle of dispatching it. The status is only visible
      briefly because the runner writes the new state at the end
      of the dispatch; it exists so a future concurrent runner
      can spot a stale lock.
    * ``COMPLETED`` — the job ran successfully and has no
      recurrence (terminal state).
    * ``FAILED`` — the job's last dispatch raised an exception
      other than :class:`QuotaExhaustedError`. The job stays in
      the queue; ``postcards schedule retry <id>`` resets it to
      ``PENDING`` once the user has fixed the underlying issue.
    * ``CANCELLED`` — the user removed the job before it ran
      (terminal state; kept on disk for audit purposes).

    The string value matches the on-disk format. Inheriting from
    :class:`enum.StrEnum` keeps the on-disk format readable and
    lets the CLI match against ``status.value`` directly when
    filtering.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class RecurrenceRule:
    """When a :class:`ScheduledJob` should run again.

    Two shapes are supported:

    * :attr:`kind == "none"` — a one-shot. ``advance`` raises
      :class:`ScheduleError` because the job has no next run.
    * :attr:`kind == "every_n_days"` — fire every :attr:`interval_days`
      days. ``advance`` returns ``current + timedelta(days=N)``.
    * :attr:`kind == "weekly"` — fire on the given ISO weekdays.
      ``advance`` returns the next date in ``weekdays`` strictly
      after ``current``.

    Both recurring kinds also carry :attr:`hour` and :attr:`minute`
    so the user can pin the local send time of day. The runner
    converts ``hour`` / ``minute`` to UTC at the call site by
    subtracting the system timezone; the model itself stores
    naive ``hour`` / ``minute`` integers so the on-disk format is
    timezone-agnostic.
    """

    kind: str
    interval_days: int = 0
    weekdays: tuple[int, ...] = ()
    hour: int = 9
    minute: int = 0

    def __post_init__(self) -> None:
        if self.kind not in {"none", "every_n_days", "weekly"}:
            raise ScheduleError(
                f"unknown recurrence kind {self.kind!r}; expected 'none', 'every_n_days', or 'weekly'"
            )
        if self.kind == "every_n_days":
            if self.interval_days < 1:
                raise ScheduleError(
                    f"every-n-days recurrence requires interval_days >= 1, got {self.interval_days}"
                )
            if self.interval_days > MAX_RECURRING_INTERVAL_DAYS:
                raise ScheduleError(
                    f"every-n-days recurrence interval {self.interval_days} exceeds the "
                    f"{MAX_RECURRING_INTERVAL_DAYS}-day cap"
                )
        if self.kind == "weekly":
            if not self.weekdays:
                raise ScheduleError("weekly recurrence requires at least one weekday")
            for day in self.weekdays:
                if day not in _WEEKLY_DAY_NAMES.values():
                    raise ScheduleError(f"weekly recurrence weekday {day} is not in 0..6")
        if not 0 <= self.hour <= 23:
            raise ScheduleError(f"recurrence hour {self.hour} is not in 0..23")
        if not 0 <= self.minute <= 59:
            raise ScheduleError(f"recurrence minute {self.minute} is not in 0..59")

    @classmethod
    def one_shot(cls) -> RecurrenceRule:
        """Return a one-shot recurrence (the default for ``schedule add --at``)."""
        return cls(kind="none")

    @classmethod
    def every_n_days(cls, days: int, *, hour: int = 9, minute: int = 0) -> RecurrenceRule:
        """Return a recurrence that fires every ``days`` days."""
        return cls(kind="every_n_days", interval_days=days, hour=hour, minute=minute)

    @classmethod
    def weekly(
        cls,
        weekdays: Iterable[int],
        *,
        hour: int = 9,
        minute: int = 0,
    ) -> RecurrenceRule:
        """Return a recurrence that fires on the given ISO weekdays."""
        return cls(
            kind="weekly",
            weekdays=tuple(sorted(set(weekdays))),
            hour=hour,
            minute=minute,
        )

    @classmethod
    def from_string(cls, value: str) -> RecurrenceRule:
        """Parse a CLI / on-disk recurrence string.

        Accepts:

        * ``"none"`` / ``""`` / ``"once"`` — one-shot.
        * ``"every:Nd"`` — every ``N`` days.
        * ``"weekly:mon,wed,fri"`` — on the named weekdays.

        Raises :class:`ScheduleError` for unrecognised strings.
        """
        normalised = value.strip().lower()
        if normalised in {"", "none", "once"}:
            return cls.one_shot()
        match = _EVERY_DAYS_PATTERN.match(normalised)
        if match is not None:
            return cls.every_n_days(int(match.group(1)))
        if normalised.startswith("weekly:"):
            raw_days = normalised[len("weekly:") :]
            if not raw_days:
                raise ScheduleError(
                    "weekly recurrence must list at least one day, e.g. 'weekly:mon'"
                )
            weekdays: list[int] = []
            for chunk in raw_days.split(","):
                chunk = chunk.strip()
                if chunk not in _WEEKLY_DAY_NAMES:
                    valid = ", ".join(sorted(_WEEKLY_DAY_NAMES))
                    raise ScheduleError(
                        f"unknown weekday {chunk!r} in recurrence {value!r}; "
                        f"valid weekdays are: {valid}"
                    )
                weekdays.append(_WEEKLY_DAY_NAMES[chunk])
            return cls.weekly(weekdays)
        raise ScheduleError(
            f"could not parse recurrence {value!r}; expected 'none', 'every:Nd', or "
            "'weekly:mon[,tue,...]'"
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-friendly representation of this rule."""
        return {
            "kind": self.kind,
            "interval_days": self.interval_days,
            "weekdays": list(self.weekdays),
            "hour": self.hour,
            "minute": self.minute,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> RecurrenceRule:
        """Build a :class:`RecurrenceRule` from its JSON-friendly dict."""
        try:
            kind = payload["kind"]
        except KeyError as exc:
            raise ScheduleError(f"recurrence rule missing required field: {exc.args[0]}") from exc
        return cls(
            kind=str(kind),
            interval_days=int(payload.get("interval_days", 0)),
            weekdays=tuple(int(d) for d in payload.get("weekdays", [])),
            hour=int(payload.get("hour", 9)),
            minute=int(payload.get("minute", 0)),
        )

    def advance(self, current: datetime) -> datetime:
        """Return the next run time strictly after ``current``.

        One-shot rules raise :class:`ScheduleError` — the caller
        marks the job :attr:`JobStatus.COMPLETED` instead of
        calling :meth:`advance`.
        """
        if self.kind == "none":
            raise ScheduleError("cannot advance a one-shot recurrence")
        if self.kind == "every_n_days":
            # Snap ``current`` to the configured hour/minute, then
            # advance by ``interval_days`` until the candidate is
            # strictly after ``current``. The ``<=`` guard handles
            # the case where the caller is computing the next run
            # immediately after a successful fire (i.e. ``current``
            # is exactly the just-fired time) — without it the
            # caller would loop forever on the same instant.
            base = current.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)
            candidate = base
            while candidate <= current:
                candidate = candidate + timedelta(days=self.interval_days)
            return candidate
        if self.kind == "weekly":
            for offset in range(1, 8):
                candidate = current + timedelta(days=offset)
                if candidate.weekday() in self.weekdays:
                    return candidate.replace(
                        hour=self.hour, minute=self.minute, second=0, microsecond=0
                    )
            # ``range(1, 8)`` always finds a match because the
            # weekdays tuple is non-empty (validated in
            # ``__post_init__``).
            raise ScheduleError(
                "unreachable: weekly recurrence has no matching weekday"
            )  # pragma: no cover
        raise ScheduleError(f"unknown recurrence kind {self.kind!r}")  # pragma: no cover

    def describe(self) -> str:
        """Return a human-readable description of the rule."""
        if self.kind == "none":
            return "once"
        if self.kind == "every_n_days":
            plural = "" if self.interval_days == 1 else "s"
            return f"every {self.interval_days} day{plural} at {self.hour:02d}:{self.minute:02d}"
        if self.kind == "weekly":
            names = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
            days = ",".join(names[d] for d in sorted(self.weekdays))
            return f"weekly on {days} at {self.hour:02d}:{self.minute:02d}"
        return self.kind  # pragma: no cover


@dataclass(frozen=True)
class ScheduledJob:
    """A deferred or recurring send, persisted in the schedule book.

    The job carries everything the runner needs to dispatch the
    send *without* re-reading the CLI flags: the recipient /
    sender / message / picture inputs, the recurrence rule, and
    the next run time. The ``id`` is a UUID4 hex string so the
    CLI can address the job with a short, stable handle.

    Inputs that the user supplied as flag values (e.g. ``--to``)
    are stored alongside their concrete resolutions (the address-
    book entry's name + the resolved :class:`AddressSpec`) so the
    runner can build a :class:`postcards.models.Postcard` directly
    without going through the legacy ``do_command_send`` plumbing.
    """

    id: str
    created_at: datetime
    next_run_at: datetime
    recurrence: RecurrenceRule
    status: JobStatus
    # The send inputs — captured at queue time. Optional fields
    # reflect the M4 ``postcards send`` surface; the runner
    # accepts whatever combination is non-empty.
    recipient_name: str
    sender_name: str | None
    picture: str | None
    message: str | None
    message_template_name: str | None
    template_variables: Mapping[str, str]
    # Account credentials live with the job because the runner is
    # a separate process (cron-driven) and may not have access to
    # the user's interactive config. ``None`` means "use the
    # default account from the env / keyring at run time".
    username: str | None
    password: str | None
    backend: str | None
    # Bookkeeping — populated by the runner after a dispatch.
    last_run_at: datetime | None = None
    last_error: str | None = None
    last_confirmation: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ScheduleError("job id must be a non-empty string")
        if not isinstance(self.status, JobStatus):
            raise ScheduleError(f"job status must be a JobStatus, got {type(self.status).__name__}")
        if not isinstance(self.recurrence, RecurrenceRule):
            raise ScheduleError(
                f"job recurrence must be a RecurrenceRule, got {type(self.recurrence).__name__}"
            )
        if not isinstance(self.recipient_name, str) or not self.recipient_name.strip():
            raise ScheduleError("recipient_name must be a non-empty string")
        # ``created_at`` and ``next_run_at`` are checked by the
        # caller (the CLI validates user input; the runner validates
        # in-memory bookkeeping). We accept both naive and aware
        # datetimes; the runner normalises to UTC before comparing.
        if not isinstance(self.created_at, datetime):
            raise ScheduleError("created_at must be a datetime")
        if not isinstance(self.next_run_at, datetime):
            raise ScheduleError("next_run_at must be a datetime")

    def with_status(
        self,
        status: JobStatus,
        *,
        next_run_at: datetime | None = None,
        last_run_at: datetime | None = None,
        last_error: str | None = None,
        last_confirmation: str | None = None,
    ) -> ScheduledJob:
        """Return a copy of this job with the given bookkeeping updated.

        The original job is left untouched — :class:`ScheduledJob`
        is a value type, consistent with the address-book and
        template-book discipline.
        """
        return ScheduledJob(
            id=self.id,
            created_at=self.created_at,
            next_run_at=next_run_at if next_run_at is not None else self.next_run_at,
            recurrence=self.recurrence,
            status=status,
            recipient_name=self.recipient_name,
            sender_name=self.sender_name,
            picture=self.picture,
            message=self.message,
            message_template_name=self.message_template_name,
            template_variables=self.template_variables,
            username=self.username,
            password=self.password,
            backend=self.backend,
            last_run_at=last_run_at if last_run_at is not None else self.last_run_at,
            last_error=last_error,
            last_confirmation=last_confirmation,
        )

    def is_due(self, now: datetime) -> bool:
        """Return ``True`` when the job is ready to be dispatched.

        A job is due when its status is :attr:`JobStatus.PENDING`
        AND its ``next_run_at`` is at or before ``now``. Other
        statuses are skipped — a :attr:`JobStatus.RUNNING` job is
        presumably being dispatched by a concurrent runner and
        must not be picked up twice.
        """
        return self.status is JobStatus.PENDING and self.next_run_at <= now

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-friendly representation of this job."""
        return {
            "id": self.id,
            "created_at": _isoformat(self.created_at),
            "next_run_at": _isoformat(self.next_run_at),
            "recurrence": self.recurrence.to_dict(),
            "status": self.status.value,
            "recipient_name": self.recipient_name,
            "sender_name": self.sender_name,
            "picture": self.picture,
            "message": self.message,
            "message_template_name": self.message_template_name,
            "template_variables": dict(self.template_variables),
            "username": self.username,
            "password": self.password,
            "backend": self.backend,
            "last_run_at": _isoformat(self.last_run_at) if self.last_run_at else None,
            "last_error": self.last_error,
            "last_confirmation": self.last_confirmation,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ScheduledJob:
        """Build a :class:`ScheduledJob` from its JSON-friendly dict."""
        try:
            job_id = payload["id"]
            created_at_raw = payload["created_at"]
            next_run_at_raw = payload["next_run_at"]
        except KeyError as exc:
            raise ScheduleError(f"job missing required field: {exc.args[0]}") from exc
        return cls(
            id=str(job_id),
            created_at=_parse_isoformat(created_at_raw, "created_at"),
            next_run_at=_parse_isoformat(next_run_at_raw, "next_run_at"),
            recurrence=RecurrenceRule.from_dict(payload.get("recurrence", {"kind": "none"})),
            status=JobStatus(str(payload.get("status", JobStatus.PENDING.value))),
            recipient_name=str(payload.get("recipient_name", "")),
            sender_name=_optional_str(payload.get("sender_name")),
            picture=_optional_str(payload.get("picture")),
            message=_optional_str(payload.get("message")),
            message_template_name=_optional_str(payload.get("message_template_name")),
            template_variables={
                str(key): str(value) for key, value in payload.get("template_variables", {}).items()
            },
            username=_optional_str(payload.get("username")),
            password=_optional_str(payload.get("password")),
            backend=_optional_str(payload.get("backend")),
            last_run_at=(
                _parse_isoformat(payload["last_run_at"], "last_run_at")
                if payload.get("last_run_at")
                else None
            ),
            last_error=_optional_str(payload.get("last_error")),
            last_confirmation=_optional_str(payload.get("last_confirmation")),
        )


@dataclass(frozen=True)
class ScheduleBook:
    """An ordered collection of :class:`ScheduledJob` records.

    Same value-type discipline as :class:`postcards.addressbook.
    AddressBook`: :meth:`add`, :meth:`update`, :meth:`remove`
    return *new* books rather than mutating ``self``. The book
    keeps insertion order so ``schedule list`` output is stable
    across calls.
    """

    jobs: tuple[ScheduledJob, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for job in self.jobs:
            if job.id in seen:
                raise ScheduleError(f"duplicate job id {job.id!r} in schedule book")
            seen.add(job.id)

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def get(self, job_id: str) -> ScheduledJob:
        """Return the job with ``job_id``.

        Raises :class:`ScheduleError` when no such job exists;
        callers that want a ``None`` return should use
        :meth:`find` instead.
        """
        for job in self.jobs:
            if job.id == job_id:
                return job
        raise ScheduleError(f"no scheduled job with id {job_id!r}")

    def find(self, job_id: str) -> ScheduledJob | None:
        """Return the job with ``job_id`` or ``None`` if absent."""
        for job in self.jobs:
            if job.id == job_id:
                return job
        return None

    def filter(
        self,
        *,
        status: JobStatus | None = None,
    ) -> ScheduleBook:
        """Return a new book containing only jobs matching ``status``.

        ``status=None`` returns a copy of the whole book. The
        copy preserves insertion order.
        """
        if status is None:
            return ScheduleBook(jobs=self.jobs)
        return ScheduleBook(jobs=tuple(j for j in self.jobs if j.status is status))

    def is_empty(self) -> bool:
        return not self.jobs

    def __len__(self) -> int:
        return len(self.jobs)

    def __iter__(self) -> Iterator[ScheduledJob]:
        return iter(self.jobs)

    # ------------------------------------------------------------------
    # Mutations (return new books)
    # ------------------------------------------------------------------

    def add(self, job: ScheduledJob) -> ScheduleBook:
        """Return a new book with ``job`` appended.

        Raises :class:`ScheduleError` if a job with the same id
        already exists.
        """
        if any(j.id == job.id for j in self.jobs):
            raise ScheduleError(f"scheduled job {job.id!r} already exists")
        return ScheduleBook(jobs=(*self.jobs, job))

    def update(self, job: ScheduledJob) -> ScheduleBook:
        """Return a new book with ``job`` replacing the existing one.

        Raises :class:`ScheduleError` if no job with that id
        exists.
        """
        for index, existing in enumerate(self.jobs):
            if existing.id == job.id:
                new_jobs = list(self.jobs)
                new_jobs[index] = job
                return ScheduleBook(jobs=tuple(new_jobs))
        raise ScheduleError(f"cannot update unknown scheduled job {job.id!r}")

    def remove(self, job_id: str) -> ScheduleBook:
        """Return a new book without the job with ``job_id``.

        Raises :class:`ScheduleError` if no such job exists.
        """
        new_jobs = tuple(j for j in self.jobs if j.id != job_id)
        if len(new_jobs) == len(self.jobs):
            raise ScheduleError(f"cannot remove unknown scheduled job {job_id!r}")
        return ScheduleBook(jobs=new_jobs)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "jobs": [job.to_dict() for job in self.jobs],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ScheduleBook:
        try:
            version = payload["version"]
        except KeyError as exc:
            raise ScheduleError("schedule-book file missing 'version' field") from exc
        if version != 1:
            raise ScheduleError(
                f"unsupported schedule-book version {version!r}; this build only reads version 1"
            )
        raw_jobs = payload.get("jobs", [])
        if not isinstance(raw_jobs, list):
            raise ScheduleError("schedule-book 'jobs' must be a list")
        jobs = tuple(ScheduledJob.from_dict(item) for item in raw_jobs)
        return cls(jobs=jobs)


# ---------------------------------------------------------------------------
# Clock abstraction
# ---------------------------------------------------------------------------


@runtime_checkable
class Clock(Protocol):
    """A source of "now".

    The runner reads ``clock.now()`` to decide which jobs are
    due and to stamp the ``last_run_at`` / ``next_run_at``
    bookkeeping fields. Production code uses :class:`SystemClock`;
    tests inject :class:`FakeClock` to advance time without
    sleeping.

    The protocol is intentionally minimal — only :meth:`now` —
    because the runner only needs a single monotonic timestamp
    per dispatch.
    """

    def now(self) -> datetime:
        """Return the current UTC time."""
        ...


@dataclass(frozen=True)
class SystemClock:
    """Production :class:`Clock` — delegates to :func:`datetime.now`."""

    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass
class FakeClock:
    """Test :class:`Clock` — the time is whatever the test sets.

    ``advance(seconds=...)`` and :meth:`advance_to` move the
    clock forward by the given amount, letting tests assert
    what the runner does at a specific wall-clock time without
    sleeping. The class is intentionally a mutable dataclass
    (no ``frozen=True``) because tests need to mutate it; the
    production path uses the immutable :class:`SystemClock`.
    """

    current: datetime

    def __init__(self, current: datetime | None = None) -> None:
        self.current = current if current is not None else datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(
        self, *, seconds: float = 0, minutes: float = 0, hours: float = 0, days: float = 0
    ) -> None:
        """Move the clock forward by the given delta.

        ``seconds`` / ``minutes`` / ``hours`` / ``days`` are
        additive. The total is converted to seconds via
        :class:`datetime.timedelta` so the test does not have to
        pre-compute a :class:`datetime` value.
        """
        delta = timedelta(
            seconds=seconds,
            minutes=minutes,
            hours=hours,
            days=days,
        )
        self.current = self.current + delta

    def advance_to(self, target: datetime) -> None:
        """Snap the clock to ``target``.

        Going backwards raises :class:`ScheduleError` because
        most callers intend forward time travel; if a test
        really needs to go backwards, it can construct a fresh
        :class:`FakeClock` instead.
        """
        if target < self.current:
            raise ScheduleError(
                f"cannot advance FakeClock backwards from {self.current.isoformat()} "
                f"to {target.isoformat()}; construct a new FakeClock instead"
            )
        self.current = target


# ---------------------------------------------------------------------------
# Outcome types for the runner
# ---------------------------------------------------------------------------


class JobOutcome(StrEnum):
    """What the runner did with a single :class:`ScheduledJob`.

    Returned as part of :class:`ExecutionResult` so the CLI can
    print a per-job summary and so tests can assert on the
    dispatch path without inspecting the on-disk book.
    """

    SENT = "sent"
    SKIPPED_NOT_DUE = "skipped_not_due"
    SKIPPED_QUOTA = "skipped_quota"
    SKIPPED_BAD_STATUS = "skipped_bad_status"
    RESCHEDULED_RECURRING = "rescheduled_recurring"
    FAILED = "failed"


@dataclass(frozen=True)
class ExecutionResult:
    """A per-job outcome the runner returns.

    ``outcome`` is the high-level disposition (:class:`JobOutcome`).
    ``message`` is a human-readable explanation; the CLI echoes it
    to the user. ``job_id`` is the originating job's id (or ``""``
    for an empty book).
    """

    job_id: str
    outcome: JobOutcome
    message: str
    confirmation: str | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _isoformat(value: datetime) -> str:
    """Return the ISO-8601 string for ``value``.

    Aware datetimes are emitted with their UTC offset; naive
    datetimes are emitted without one so the round-trip is
    lossless.
    """
    return value.isoformat()


def _parse_isoformat(value: object, field_name: str) -> datetime:
    """Parse an ISO-8601 string into a :class:`datetime`.

    Accepts the ``+00:00`` and trailing ``Z`` forms; normalises
    a naive datetime to UTC because the runner treats every
    timestamp as UTC. Raises :class:`ScheduleError` when the
    value cannot be parsed.
    """
    if not isinstance(value, str):
        raise ScheduleError(f"{field_name} must be a string, got {type(value).__name__}")
    try:
        normalised = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalised)
    except ValueError as exc:
        raise ScheduleError(
            f"{field_name} {value!r} is not a valid ISO-8601 timestamp: {exc}"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _optional_str(value: Any) -> str | None:
    """Return ``value`` if it is a non-empty string, else ``None``.

    Used by the JSON loader to normalise ``null`` / missing
    optional fields back to ``None`` rather than empty strings.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    return str(value)


def new_job_id() -> str:
    """Return a fresh UUID4 hex string suitable as a job id."""
    return uuid.uuid4().hex


__all__ = [
    "MAX_RECURRING_INTERVAL_DAYS",
    "Clock",
    "ExecutionResult",
    "FakeClock",
    "JobOutcome",
    "JobStatus",
    "RecurrenceRule",
    "ScheduleBook",
    "ScheduleError",
    "ScheduledJob",
    "SystemClock",
    "new_job_id",
]
