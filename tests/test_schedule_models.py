"""Unit tests for :mod:`postcards.schedule.models`.

Covers:

* :class:`RecurrenceRule` parsing (every-N-days / weekly /
  one-shot), validation, advance semantics.
* :class:`ScheduledJob` construction + ``is_due`` semantics.
* :class:`ScheduleBook` value-type discipline (add / update /
  remove / filter).
* :class:`Clock` implementations (:class:`SystemClock`,
  :class:`FakeClock`).
* JSON round-trip via :meth:`to_dict` / :meth:`from_dict`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from postcards.schedule.models import (
    Clock,
    ExecutionResult,
    FakeClock,
    JobOutcome,
    JobStatus,
    MAX_RECURRING_INTERVAL_DAYS,
    RecurrenceRule,
    ScheduledJob,
    ScheduleBook,
    ScheduleError,
    SystemClock,
    new_job_id,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts(year: int, month: int, day: int, hour: int = 9, minute: int = 0) -> datetime:
    """Build an aware UTC datetime for tests."""
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _job(
    *,
    id: str = "job1",
    next_run_at: datetime | None = None,
    recurrence: RecurrenceRule | None = None,
    status: JobStatus = JobStatus.PENDING,
    sender_name: str | None = None,
    message: str | None = "hello",
    last_run_at: datetime | None = None,
    last_error: str | None = None,
    last_confirmation: str | None = None,
    template_variables: dict[str, str] | None = None,
    username: str | None = None,
    password: str | None = None,
    backend: str | None = None,
    picture: str | None = None,
    message_template_name: str | None = None,
    recipient_name: str = "alice",
) -> ScheduledJob:
    """Build a :class:`ScheduledJob` with sensible defaults."""
    return ScheduledJob(
        id=id,
        created_at=_ts(2026, 6, 24),
        next_run_at=next_run_at if next_run_at is not None else _ts(2026, 6, 24, 9),
        recurrence=recurrence if recurrence is not None else RecurrenceRule.one_shot(),
        status=status,
        recipient_name=recipient_name,
        sender_name=sender_name,
        picture=picture,
        message=message,
        message_template_name=message_template_name,
        template_variables=template_variables if template_variables is not None else {},
        username=username,
        password=password,
        backend=backend,
        last_run_at=last_run_at,
        last_error=last_error,
        last_confirmation=last_confirmation,
    )


# ---------------------------------------------------------------------------
# RecurrenceRule
# ---------------------------------------------------------------------------


class TestRecurrenceRuleConstruction:
    def test_one_shot_is_kind_none(self) -> None:
        rule = RecurrenceRule.one_shot()
        assert rule.kind == "none"

    def test_every_n_days_validates_lower_bound(self) -> None:
        with pytest.raises(ScheduleError, match="interval_days"):
            RecurrenceRule(kind="every_n_days", interval_days=0)

    def test_every_n_days_validates_upper_bound(self) -> None:
        with pytest.raises(ScheduleError, match="cap"):
            RecurrenceRule(
                kind="every_n_days",
                interval_days=MAX_RECURRING_INTERVAL_DAYS + 1,
            )

    def test_weekly_requires_at_least_one_weekday(self) -> None:
        with pytest.raises(ScheduleError, match="at least one weekday"):
            RecurrenceRule(kind="weekly", weekdays=())

    def test_weekly_rejects_unknown_weekday(self) -> None:
        with pytest.raises(ScheduleError, match="weekday"):
            RecurrenceRule(kind="weekly", weekdays=(7,))

    def test_hour_and_minute_bounds(self) -> None:
        with pytest.raises(ScheduleError, match="hour"):
            RecurrenceRule(kind="every_n_days", interval_days=1, hour=24)
        with pytest.raises(ScheduleError, match="minute"):
            RecurrenceRule(kind="every_n_days", interval_days=1, minute=60)

    def test_unknown_kind_rejected(self) -> None:
        with pytest.raises(ScheduleError, match="unknown recurrence kind"):
            RecurrenceRule(kind="monthly")  # type: ignore[arg-type]


class TestRecurrenceRuleFromString:
    def test_one_shot_aliases(self) -> None:
        for value in ("", "none", "once", "  None "):
            rule = RecurrenceRule.from_string(value)
            assert rule.kind == "none"

    def test_every_n_days(self) -> None:
        rule = RecurrenceRule.from_string("every:7d")
        assert rule.kind == "every_n_days"
        assert rule.interval_days == 7
        assert rule.hour == 9
        assert rule.minute == 0

    def test_every_n_days_with_cap_violation(self) -> None:
        with pytest.raises(ScheduleError, match="cap"):
            RecurrenceRule.from_string(f"every:{MAX_RECURRING_INTERVAL_DAYS + 1}d")

    def test_every_n_days_with_zero(self) -> None:
        with pytest.raises(ScheduleError, match="interval_days"):
            RecurrenceRule.from_string("every:0d")

    def test_weekly(self) -> None:
        rule = RecurrenceRule.from_string("weekly:mon,wed,fri")
        assert rule.kind == "weekly"
        assert rule.weekdays == (0, 2, 4)  # Mon, Wed, Fri

    def test_weekly_case_insensitive(self) -> None:
        rule = RecurrenceRule.from_string("weekly:MON,Tue")
        assert rule.weekdays == (0, 1)

    def test_weekly_empty_days_rejected(self) -> None:
        with pytest.raises(ScheduleError, match="weekly recurrence"):
            RecurrenceRule.from_string("weekly:")

    def test_weekly_unknown_day_rejected(self) -> None:
        with pytest.raises(ScheduleError, match="unknown weekday"):
            RecurrenceRule.from_string("weekly:funday")

    def test_unknown_string_rejected(self) -> None:
        with pytest.raises(ScheduleError, match="could not parse"):
            RecurrenceRule.from_string("monthly:first")


class TestRecurrenceRuleAdvance:
    def test_one_shot_advance_raises(self) -> None:
        with pytest.raises(ScheduleError, match="one-shot"):
            RecurrenceRule.one_shot().advance(_ts(2026, 6, 24, 9))

    def test_every_n_days_advances_by_interval(self) -> None:
        rule = RecurrenceRule.every_n_days(7)
        next_run = rule.advance(_ts(2026, 6, 24, 9))
        assert next_run == _ts(2026, 7, 1, 9)

    def test_every_n_days_from_just_fired(self) -> None:
        rule = RecurrenceRule.every_n_days(7)
        next_run = rule.advance(_ts(2026, 7, 1, 9))  # exactly the just-fired time
        assert next_run == _ts(2026, 7, 8, 9)

    def test_every_n_days_snaps_to_hour_minute(self) -> None:
        rule = RecurrenceRule.every_n_days(7, hour=8, minute=30)
        # Caller is at 12:00 — the rule should still pin to 08:30
        # on the next 7-day slot.
        next_run = rule.advance(_ts(2026, 6, 24, 12))
        assert next_run.hour == 8
        assert next_run.minute == 30
        assert next_run.date() == _ts(2026, 7, 1).date()

    def test_weekly_finds_next_matching_day(self) -> None:
        rule = RecurrenceRule.weekly((0, 2, 4))  # Mon/Wed/Fri
        # 2026-06-24 is a Wednesday.
        next_run = rule.advance(_ts(2026, 6, 24, 9))
        # Friday is two days later.
        assert next_run == _ts(2026, 6, 26, 9)
        assert next_run.weekday() == 4

    def test_weekly_wraps_around_week(self) -> None:
        rule = RecurrenceRule.weekly((0,))  # Monday only
        # 2026-06-27 is a Saturday.
        next_run = rule.advance(_ts(2026, 6, 27, 9))
        # Next Monday is 2026-06-29.
        assert next_run == _ts(2026, 6, 29, 9)


class TestRecurrenceRuleDescribe:
    def test_one_shot_describe(self) -> None:
        assert RecurrenceRule.one_shot().describe() == "once"

    def test_every_n_days_describe(self) -> None:
        rule = RecurrenceRule.every_n_days(1)
        assert rule.describe() == "every 1 day at 09:00"
        rule = RecurrenceRule.every_n_days(7)
        assert rule.describe() == "every 7 days at 09:00"

    def test_weekly_describe(self) -> None:
        rule = RecurrenceRule.weekly((0, 2, 4))
        assert rule.describe() == "weekly on Mon,Wed,Fri at 09:00"


class TestRecurrenceRuleSerialization:
    def test_round_trip(self) -> None:
        rule = RecurrenceRule(kind="every_n_days", interval_days=14, hour=8, minute=30)
        data = rule.to_dict()
        restored = RecurrenceRule.from_dict(data)
        assert restored == rule

    def test_round_trip_weekly(self) -> None:
        rule = RecurrenceRule(kind="weekly", weekdays=(0, 6), hour=20, minute=15)
        data = rule.to_dict()
        restored = RecurrenceRule.from_dict(data)
        assert restored == rule

    def test_from_dict_missing_kind_rejected(self) -> None:
        with pytest.raises(ScheduleError, match="missing required field"):
            RecurrenceRule.from_dict({"interval_days": 1})


# ---------------------------------------------------------------------------
# ScheduledJob
# ---------------------------------------------------------------------------


class TestScheduledJobConstruction:
    def test_recipient_name_required(self) -> None:
        with pytest.raises(ScheduleError, match="recipient_name"):
            _job(recipient_name="")

    def test_id_required(self) -> None:
        with pytest.raises(ScheduleError, match="id"):
            _job(id="")

    def test_status_must_be_enum(self) -> None:
        with pytest.raises(ScheduleError, match="status"):
            _job(status="not-an-enum")  # type: ignore[arg-type]

    def test_recurrence_must_be_recurrence_rule(self) -> None:
        with pytest.raises(ScheduleError, match="recurrence"):
            ScheduledJob(
                id="abc",
                created_at=_ts(2026, 6, 24),
                next_run_at=_ts(2026, 6, 24, 9),
                recurrence=None,  # type: ignore[arg-type]
                status=JobStatus.PENDING,
                recipient_name="alice",
                sender_name=None,
                picture=None,
                message="hello",
                message_template_name=None,
                template_variables={},
                username=None,
                password=None,
                backend=None,
            )


class TestScheduledJobIsDue:
    def test_pending_and_past_is_due(self) -> None:
        job = _job(next_run_at=_ts(2026, 6, 24, 8))
        assert job.is_due(_ts(2026, 6, 24, 9)) is True

    def test_pending_and_future_is_not_due(self) -> None:
        job = _job(next_run_at=_ts(2026, 6, 24, 10))
        assert job.is_due(_ts(2026, 6, 24, 9)) is False

    def test_completed_is_never_due(self) -> None:
        job = _job(next_run_at=_ts(2026, 6, 23, 9), status=JobStatus.COMPLETED)
        assert job.is_due(_ts(2026, 6, 24, 9)) is False

    def test_failed_is_not_due(self) -> None:
        job = _job(next_run_at=_ts(2026, 6, 23, 9), status=JobStatus.FAILED)
        assert job.is_due(_ts(2026, 6, 24, 9)) is False


class TestScheduledJobWithStatus:
    def test_with_status_returns_new_instance(self) -> None:
        job = _job()
        new_job = job.with_status(JobStatus.COMPLETED)
        assert new_job is not job
        assert new_job.status is JobStatus.COMPLETED
        assert job.status is JobStatus.PENDING  # original unchanged

    def test_with_status_preserves_unchanged_fields(self) -> None:
        job = _job(next_run_at=_ts(2026, 6, 24, 9))
        new_job = job.with_status(
            JobStatus.COMPLETED,
            last_run_at=_ts(2026, 6, 24, 10),
            last_confirmation="abc",
        )
        assert new_job.next_run_at == job.next_run_at
        assert new_job.recipient_name == job.recipient_name
        assert new_job.last_run_at == _ts(2026, 6, 24, 10)
        assert new_job.last_confirmation == "abc"

    def test_with_status_can_advance_next_run(self) -> None:
        job = _job(next_run_at=_ts(2026, 6, 24, 9))
        new_job = job.with_status(
            JobStatus.PENDING,
            next_run_at=_ts(2026, 7, 1, 9),
        )
        assert new_job.next_run_at == _ts(2026, 7, 1, 9)


class TestScheduledJobSerialization:
    def test_round_trip(self) -> None:
        job = _job(
            id="abc",
            recurrence=RecurrenceRule.every_n_days(7),
            template_variables={"name": "Alice"},
        )
        data = job.to_dict()
        restored = ScheduledJob.from_dict(data)
        assert restored == job

    def test_round_trip_with_optional_fields(self) -> None:
        job = _job(
            sender_name="me",
            picture="/tmp/pic.jpg",
            message=None,
            message_template_name="greeting",
            template_variables={"name": "Alice"},
            username="user",
            password="pass",
            backend="mock",
            last_run_at=_ts(2026, 6, 24, 9, 30),
            last_error=None,
            last_confirmation="xyz",
        )
        data = job.to_dict()
        restored = ScheduledJob.from_dict(data)
        assert restored == job

    def test_from_dict_handles_naive_datetime(self) -> None:
        data = {
            "id": "abc",
            "created_at": "2026-06-24T09:00:00",
            "next_run_at": "2026-06-24T09:00:00",
            "recurrence": {"kind": "none"},
            "status": "pending",
            "recipient_name": "alice",
            "template_variables": {},
        }
        job = ScheduledJob.from_dict(data)
        assert job.created_at.tzinfo is not None
        assert job.next_run_at.tzinfo is not None

    def test_from_dict_normalises_z_suffix(self) -> None:
        data = {
            "id": "abc",
            "created_at": "2026-06-24T09:00:00Z",
            "next_run_at": "2026-06-24T09:00:00Z",
            "recurrence": {"kind": "none"},
            "status": "pending",
            "recipient_name": "alice",
            "template_variables": {},
        }
        job = ScheduledJob.from_dict(data)
        assert job.created_at.tzinfo is not None

    def test_from_dict_rejects_bad_timestamp(self) -> None:
        data = {
            "id": "abc",
            "created_at": "not-a-timestamp",
            "next_run_at": "2026-06-24T09:00:00",
            "recurrence": {"kind": "none"},
            "status": "pending",
            "recipient_name": "alice",
            "template_variables": {},
        }
        with pytest.raises(ScheduleError, match="not a valid ISO-8601"):
            ScheduledJob.from_dict(data)

    def test_from_dict_rejects_missing_field(self) -> None:
        data = {"id": "abc", "created_at": "2026-06-24T09:00:00Z"}
        with pytest.raises(ScheduleError, match="missing required field"):
            ScheduledJob.from_dict(data)


# ---------------------------------------------------------------------------
# ScheduleBook
# ---------------------------------------------------------------------------


class TestScheduleBook:
    def test_empty_book(self) -> None:
        book = ScheduleBook()
        assert book.is_empty()
        assert len(book) == 0

    def test_add_returns_new_book(self) -> None:
        book = ScheduleBook()
        new_book = book.add(_job())
        assert new_book is not book
        assert len(new_book) == 1
        assert book.is_empty()  # original unchanged

    def test_add_rejects_duplicate_id(self) -> None:
        book = ScheduleBook(jobs=(_job(id="dup"),))
        with pytest.raises(ScheduleError, match="already exists"):
            book.add(_job(id="dup"))

    def test_constructor_rejects_duplicate_id(self) -> None:
        with pytest.raises(ScheduleError, match="duplicate job id"):
            ScheduleBook(jobs=(_job(id="dup"), _job(id="dup")))

    def test_update_replaces_in_place(self) -> None:
        original = _job(id="a", recipient_name="alice")
        updated = original.with_status(JobStatus.COMPLETED)
        book = ScheduleBook(jobs=(original,))
        new_book = book.update(updated)
        assert len(new_book) == 1
        assert new_book.jobs[0].status is JobStatus.COMPLETED

    def test_update_unknown_id_raises(self) -> None:
        book = ScheduleBook(jobs=(_job(id="a"),))
        with pytest.raises(ScheduleError, match="cannot update unknown"):
            book.update(_job(id="b"))

    def test_remove_returns_new_book_without_job(self) -> None:
        a = _job(id="a")
        b = _job(id="b")
        book = ScheduleBook(jobs=(a, b))
        new_book = book.remove("a")
        assert [j.id for j in new_book.jobs] == ["b"]
        assert book is not new_book  # value-type discipline

    def test_remove_unknown_id_raises(self) -> None:
        book = ScheduleBook(jobs=(_job(id="a"),))
        with pytest.raises(ScheduleError, match="cannot remove unknown"):
            book.remove("missing")

    def test_get_returns_job(self) -> None:
        job = _job(id="a")
        book = ScheduleBook(jobs=(job,))
        assert book.get("a") == job

    def test_get_unknown_raises(self) -> None:
        book = ScheduleBook()
        with pytest.raises(ScheduleError, match="no scheduled job"):
            book.get("missing")

    def test_find_returns_none_for_unknown(self) -> None:
        book = ScheduleBook()
        assert book.find("missing") is None

    def test_filter_by_status(self) -> None:
        book = ScheduleBook(
            jobs=(
                _job(id="a", status=JobStatus.PENDING),
                _job(id="b", status=JobStatus.COMPLETED),
                _job(id="c", status=JobStatus.PENDING),
            )
        )
        pending = book.filter(status=JobStatus.PENDING)
        assert [j.id for j in pending.jobs] == ["a", "c"]

    def test_filter_none_returns_copy(self) -> None:
        book = ScheduleBook(jobs=(_job(id="a"), _job(id="b")))
        copy = book.filter()
        assert [j.id for j in copy.jobs] == ["a", "b"]
        assert copy is not book

    def test_iteration_order(self) -> None:
        a = _job(id="a")
        b = _job(id="b")
        book = ScheduleBook(jobs=(a, b))
        assert [j.id for j in book] == ["a", "b"]

    def test_round_trip(self) -> None:
        book = ScheduleBook(
            jobs=(
                _job(id="a", recurrence=RecurrenceRule.every_n_days(7)),
                _job(id="b", status=JobStatus.COMPLETED),
            )
        )
        data = book.to_dict()
        restored = ScheduleBook.from_dict(data)
        assert restored == book

    def test_from_dict_rejects_wrong_version(self) -> None:
        with pytest.raises(ScheduleError, match="unsupported schedule-book version"):
            ScheduleBook.from_dict({"version": 2, "jobs": []})

    def test_from_dict_rejects_non_list_jobs(self) -> None:
        with pytest.raises(ScheduleError, match="must be a list"):
            ScheduleBook.from_dict({"version": 1, "jobs": "not-a-list"})

    def test_from_dict_rejects_missing_version(self) -> None:
        with pytest.raises(ScheduleError, match="missing 'version'"):
            ScheduleBook.from_dict({"jobs": []})


# ---------------------------------------------------------------------------
# Clock implementations
# ---------------------------------------------------------------------------


class TestSystemClock:
    def test_now_returns_aware_datetime(self) -> None:
        result = SystemClock().now()
        assert isinstance(result, datetime)
        assert result.tzinfo is not None


class TestFakeClock:
    def test_default_starts_at_2026_01_01(self) -> None:
        clock = FakeClock()
        assert clock.now() == _ts(2026, 1, 1, hour=0, minute=0)

    def test_advance_moves_clock(self) -> None:
        clock = FakeClock()
        clock.advance(days=2, hours=3)
        assert clock.now() == _ts(2026, 1, 3, 3)

    def test_advance_combines_units(self) -> None:
        clock = FakeClock()
        clock.advance(minutes=30)
        assert clock.now().minute == 30
        clock.advance(seconds=45)
        assert clock.now().second == 45

    def test_advance_to_snaps_to_target(self) -> None:
        clock = FakeClock()
        clock.advance_to(_ts(2026, 6, 24, 9))
        assert clock.now() == _ts(2026, 6, 24, 9)

    def test_advance_to_rejects_backwards(self) -> None:
        clock = FakeClock(_ts(2026, 6, 24))
        with pytest.raises(ScheduleError, match="backwards"):
            clock.advance_to(_ts(2026, 6, 23))

    def test_satisfies_protocol(self) -> None:
        clock: Clock = FakeClock()
        # runtime-checkable
        assert isinstance(clock, Clock)


# ---------------------------------------------------------------------------
# Outcome types
# ---------------------------------------------------------------------------


class TestExecutionResult:
    def test_construct_with_all_fields(self) -> None:
        result = ExecutionResult(
            job_id="abc",
            outcome=JobOutcome.SENT,
            message="ok",
            confirmation="xyz",
        )
        assert result.job_id == "abc"
        assert result.outcome is JobOutcome.SENT
        assert result.message == "ok"
        assert result.confirmation == "xyz"

    def test_confirmation_optional(self) -> None:
        result = ExecutionResult(
            job_id="abc",
            outcome=JobOutcome.SKIPPED_NOT_DUE,
            message="not due yet",
        )
        assert result.confirmation is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_new_job_id_is_unique_hex() -> None:
    a = new_job_id()
    b = new_job_id()
    assert a != b
    assert len(a) == 32  # UUID4 hex is 32 chars
    int(a, 16)  # must be valid hex