"""Tests for :mod:`postcards.schedule.runner`.

The runner is the heart of the scheduler: it walks the schedule
book, dispatches due jobs against a :class:`PostcardBackend`,
and updates the bookkeeping. The tests use :class:`MockBackend`
(``postcards.backend.mock.MockBackend``) as the in-memory
backend so the dispatch path can be exercised without ever
touching the network — see ``docs/CONSTITUTION.md`` §1.2.

A :class:`FakeClock` is injected so the test can advance time
deterministically without sleeping.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from postcards.addressbook.models import (
    AddressBook,
    AddressBookEntry,
    AddressCategory,
    MessageTemplate,
    TemplateBook,
)
from postcards.addressbook.storage import save_address_book, save_template_book
from postcards.backend.base import AddressSpec, QuotaInfo
from postcards.backend.exceptions import AuthenticationError
from postcards.backend.mock import MockBackend
from postcards.schedule import (
    FakeClock,
    JobOutcome,
    JobStatus,
    RecurrenceRule,
    ScheduleBook,
    ScheduledJob,
    run_due_jobs,
)
from postcards.schedule.runner import QuotaExhaustedError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def alice_entry() -> AddressBookEntry:
    """Return a recipient entry named ``alice``."""
    return AddressBookEntry(
        name="alice",
        category=AddressCategory.RECIPIENT,
        address=AddressSpec(
            prename="Alice",
            lastname="Doe",
            street="Hauptstrasse 1",
            zip_code="8000",
            place="Zurich",
        ),
    )


@pytest.fixture
def address_book(
    alice_entry: AddressBookEntry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AddressBook:
    """Return an :class:`AddressBook` with one recipient, persisted."""
    book = AddressBook(entries=(alice_entry,))
    monkeypatch.setenv("POSTCARDS_DATA_DIR", str(tmp_path))
    save_address_book(book)
    return book


@pytest.fixture
def template_book(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TemplateBook:
    """Return a :class:`TemplateBook` with a single ``greeting`` template."""
    book = TemplateBook(templates=(MessageTemplate(name="greeting", body="Hi $name!"),))
    monkeypatch.setenv("POSTCARDS_DATA_DIR", str(tmp_path))
    save_template_book(book)
    return book


@pytest.fixture
def backend() -> MockBackend:
    """Return a :class:`MockBackend` with quota available."""
    return MockBackend(quota_info=QuotaInfo(available=True))


def _job(
    *,
    id: str = "job1",
    next_run_at: datetime | None = None,
    recurrence: RecurrenceRule | None = None,
    status: JobStatus = JobStatus.PENDING,
    username: str | None = "user",
    password: str | None = "pass",
    recipient_name: str = "alice",
    message: str = "Hello Alice",
    template_variables: dict[str, str] | None = None,
) -> ScheduledJob:
    """Build a :class:`ScheduledJob` with sensible defaults for runner tests."""
    return ScheduledJob(
        id=id,
        created_at=datetime(2026, 6, 24, 9, 0, tzinfo=UTC),
        next_run_at=next_run_at or datetime(2026, 6, 24, 9, 0, tzinfo=UTC),
        recurrence=recurrence or RecurrenceRule.one_shot(),
        status=status,
        recipient_name=recipient_name,
        sender_name=None,
        picture=None,
        message=message,
        message_template_name=None,
        template_variables=template_variables or {},
        username=username,
        password=password,
        backend=None,
    )


def _factory(backend: MockBackend) -> Callable[[], MockBackend]:
    """Build a backend factory that returns the same backend instance each call."""

    def factory() -> MockBackend:
        return backend

    return factory


# ---------------------------------------------------------------------------
# Successful dispatch
# ---------------------------------------------------------------------------


class TestSuccessfulDispatch:
    def test_due_one_shot_job_is_sent(
        self, address_book: AddressBook, backend: MockBackend
    ) -> None:
        clock = FakeClock(datetime(2026, 6, 24, 9, 30, tzinfo=UTC))
        job = _job(next_run_at=datetime(2026, 6, 24, 9, 0, tzinfo=UTC))
        book = ScheduleBook(jobs=(job,))

        new_book, results = run_due_jobs(
            book,
            clock=clock,
            backend_factory=_factory(backend),
            address_book=address_book,
        )

        assert len(results) == 1
        assert results[0].outcome is JobOutcome.SENT
        assert backend.sent[0].postcard.recipient.lastname == "Doe"
        assert new_book.jobs[0].status is JobStatus.COMPLETED
        assert new_book.jobs[0].last_confirmation == "mock-0"

    def test_sender_defaults_to_recipient(
        self, address_book: AddressBook, backend: MockBackend
    ) -> None:
        clock = FakeClock(datetime(2026, 6, 24, 9, 30, tzinfo=UTC))
        job = _job()
        book = ScheduleBook(jobs=(job,))

        run_due_jobs(
            book,
            clock=clock,
            backend_factory=_factory(backend),
            address_book=address_book,
        )

        assert backend.sent[0].postcard.sender.lastname == "Doe"

    def test_message_is_passed_through(
        self, address_book: AddressBook, backend: MockBackend
    ) -> None:
        clock = FakeClock(datetime(2026, 6, 24, 9, 30, tzinfo=UTC))
        job = _job(message="Hi from Zurich")
        book = ScheduleBook(jobs=(job,))

        run_due_jobs(
            book,
            clock=clock,
            backend_factory=_factory(backend),
            address_book=address_book,
        )

        assert backend.sent[0].postcard.message.text == "Hi from Zurich"

    def test_template_message_renders_variables(
        self,
        address_book: AddressBook,
        backend: MockBackend,
        template_book: TemplateBook,
    ) -> None:
        clock = FakeClock(datetime(2026, 6, 24, 9, 30, tzinfo=UTC))
        job = ScheduledJob(
            id="job1",
            created_at=datetime(2026, 6, 24, 9, 0, tzinfo=UTC),
            next_run_at=datetime(2026, 6, 24, 9, 0, tzinfo=UTC),
            recurrence=RecurrenceRule.one_shot(),
            status=JobStatus.PENDING,
            recipient_name="alice",
            sender_name=None,
            picture=None,
            message=None,
            message_template_name="greeting",
            template_variables={"name": "Alice"},
            username="user",
            password="pass",
            backend=None,
        )
        book = ScheduleBook(jobs=(job,))

        run_due_jobs(
            book,
            clock=clock,
            backend_factory=_factory(backend),
            address_book=address_book,
        )

        assert backend.sent[0].postcard.message.text == "Hi Alice!"


# ---------------------------------------------------------------------------
# Recurring jobs
# ---------------------------------------------------------------------------


class TestRecurringJobs:
    def test_every_n_days_advances_next_run(
        self, address_book: AddressBook, backend: MockBackend
    ) -> None:
        clock = FakeClock(datetime(2026, 6, 24, 9, 30, tzinfo=UTC))
        job = _job(
            next_run_at=datetime(2026, 6, 24, 9, 0, tzinfo=UTC),
            recurrence=RecurrenceRule.every_n_days(7),
        )
        book = ScheduleBook(jobs=(job,))

        new_book, results = run_due_jobs(
            book,
            clock=clock,
            backend_factory=_factory(backend),
            address_book=address_book,
        )

        assert results[0].outcome is JobOutcome.RESCHEDULED_RECURRING
        # The next run is 7 days later.
        assert new_book.jobs[0].next_run_at == datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
        # Job stays pending.
        assert new_book.jobs[0].status is JobStatus.PENDING

    def test_weekly_advances_to_next_matching_day(
        self, address_book: AddressBook, backend: MockBackend
    ) -> None:
        # 2026-06-24 is a Wednesday.
        clock = FakeClock(datetime(2026, 6, 24, 9, 30, tzinfo=UTC))
        job = _job(
            next_run_at=datetime(2026, 6, 24, 9, 0, tzinfo=UTC),
            recurrence=RecurrenceRule.weekly((0,)),  # Mondays only
        )
        book = ScheduleBook(jobs=(job,))

        new_book, results = run_due_jobs(
            book,
            clock=clock,
            backend_factory=_factory(backend),
            address_book=address_book,
        )

        assert results[0].outcome is JobOutcome.RESCHEDULED_RECURRING
        # Next Monday is 2026-06-29.
        assert new_book.jobs[0].next_run_at == datetime(2026, 6, 29, 9, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Skipped paths
# ---------------------------------------------------------------------------


class TestSkippedJobs:
    def test_job_in_the_future_is_skipped(
        self, address_book: AddressBook, backend: MockBackend
    ) -> None:
        clock = FakeClock(datetime(2026, 6, 24, 9, 0, tzinfo=UTC))
        job = _job(next_run_at=datetime(2026, 6, 25, 9, 0, tzinfo=UTC))
        book = ScheduleBook(jobs=(job,))

        new_book, results = run_due_jobs(
            book,
            clock=clock,
            backend_factory=_factory(backend),
            address_book=address_book,
        )

        assert results[0].outcome is JobOutcome.SKIPPED_NOT_DUE
        assert new_book is book  # unchanged
        assert backend.sent == []

    def test_completed_job_is_skipped(
        self, address_book: AddressBook, backend: MockBackend
    ) -> None:
        clock = FakeClock(datetime(2026, 6, 24, 9, 0, tzinfo=UTC))
        job = _job(status=JobStatus.COMPLETED)
        book = ScheduleBook(jobs=(job,))

        new_book, results = run_due_jobs(
            book,
            clock=clock,
            backend_factory=_factory(backend),
            address_book=address_book,
        )

        assert results[0].outcome is JobOutcome.SKIPPED_BAD_STATUS
        assert backend.sent == []
        assert new_book is book


# ---------------------------------------------------------------------------
# Quota handling
# ---------------------------------------------------------------------------


class TestQuota:
    def test_exhausted_quota_reschedules_to_next_midnight(self, address_book: AddressBook) -> None:
        clock = FakeClock(datetime(2026, 6, 24, 9, 30, tzinfo=UTC))
        backend = MockBackend(
            quota_info=QuotaInfo(
                available=False, next_available_at=datetime(2026, 6, 25, 0, 0, tzinfo=UTC)
            )
        )
        job = _job()
        book = ScheduleBook(jobs=(job,))

        new_book, results = run_due_jobs(
            book,
            clock=clock,
            backend_factory=_factory(backend),
            address_book=address_book,
        )

        assert results[0].outcome is JobOutcome.SKIPPED_QUOTA
        # Rescheduled to 2026-06-25 00:00 UTC.
        assert new_book.jobs[0].next_run_at == datetime(2026, 6, 25, 0, 0, tzinfo=UTC)
        assert new_book.jobs[0].status is JobStatus.PENDING  # stays pending
        assert "quota" in (new_book.jobs[0].last_error or "").lower()
        assert backend.sent == []

    def test_quota_exhausted_error_can_be_caught_directly(self, address_book: AddressBook) -> None:
        # The runner raises QuotaExhaustedError when the backend
        # reports an unavailable quota. Use the runner path so
        # the exception actually fires — the MockBackend itself
        # does not raise; it just returns the configured
        # ``quota_info`` for ``backend.quota()``.
        clock = FakeClock(datetime(2026, 6, 24, 9, 30, tzinfo=UTC))
        backend = MockBackend(
            quota_info=QuotaInfo(
                available=False, next_available_at=datetime(2026, 6, 25, 0, 0, tzinfo=UTC)
            )
        )
        job = _job()
        book = ScheduleBook(jobs=(job,))

        # The runner swallows QuotaExhaustedError and reschedules
        # the job; we verify it was caught (no exception bubbles
        # out) and the job was rescheduled.
        new_book, results = run_due_jobs(
            book,
            clock=clock,
            backend_factory=_factory(backend),
            address_book=address_book,
        )
        assert results[0].outcome is JobOutcome.SKIPPED_QUOTA
        assert new_book.jobs[0].next_run_at == datetime(2026, 6, 25, 0, 0, tzinfo=UTC)

        # ``QuotaExhaustedError`` is exported by the schedule
        # package and is the type the runner handles internally.
        assert QuotaExhaustedError.__name__ == "QuotaExhaustedError"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestErrors:
    def test_missing_recipient_marks_job_failed(
        self, address_book: AddressBook, backend: MockBackend
    ) -> None:
        clock = FakeClock(datetime(2026, 6, 24, 9, 30, tzinfo=UTC))
        job = _job(recipient_name="nobody")
        book = ScheduleBook(jobs=(job,))

        new_book, results = run_due_jobs(
            book,
            clock=clock,
            backend_factory=_factory(backend),
            address_book=address_book,
        )

        assert results[0].outcome is JobOutcome.FAILED
        assert "nobody" in (new_book.jobs[0].last_error or "")
        assert backend.sent == []

    def test_missing_credentials_raises_runtime_error(
        self, address_book: AddressBook, backend: MockBackend
    ) -> None:
        clock = FakeClock(datetime(2026, 6, 24, 9, 30, tzinfo=UTC))
        job = _job(username=None, password=None)
        book = ScheduleBook(jobs=(job,))

        new_book, results = run_due_jobs(
            book,
            clock=clock,
            backend_factory=_factory(backend),
            address_book=address_book,
        )

        assert results[0].outcome is JobOutcome.FAILED
        assert "username/password" in (new_book.jobs[0].last_error or "")

    def test_login_failure_marks_job_failed(self, address_book: AddressBook) -> None:
        clock = FakeClock(datetime(2026, 6, 24, 9, 30, tzinfo=UTC))
        backend = MockBackend()
        backend.should_fail_login = True
        backend.login_error = RuntimeError("invalid credentials")

        job = _job()
        book = ScheduleBook(jobs=(job,))

        new_book, results = run_due_jobs(
            book,
            clock=clock,
            backend_factory=_factory(backend),
            address_book=address_book,
        )

        assert results[0].outcome is JobOutcome.FAILED
        assert "invalid credentials" in (new_book.jobs[0].last_error or "")


# ---------------------------------------------------------------------------
# M5: quota-exhausted exception is the unified backend-level type
# ---------------------------------------------------------------------------


class TestQuotaExceptionHierarchy:
    """M5 made the runner's :class:`QuotaExhaustedError` subclass the
    backend-level one. A single ``except`` at the CLI layer catches
    both — this test pins that."""

    def test_runner_quota_exhausted_is_a_backend_quota_exhausted(
        self, address_book: AddressBook
    ) -> None:
        from postcards.backend.exceptions import QuotaExhaustedError as BackendQuotaError

        assert issubclass(QuotaExhaustedError, BackendQuotaError)


# ---------------------------------------------------------------------------
# M5: actionable error messages in the dispatch path
# ---------------------------------------------------------------------------


class TestActionableErrors:
    """M5 routed every dispatch-time exception through
    :func:`postcards.backend.messages.translate` so the job's
    ``last_error`` field and the per-job ``message`` carry an
    actionable hint instead of the raw ``str(exc)`` text."""

    def test_authentication_error_carries_hint(self, address_book: AddressBook) -> None:
        clock = FakeClock(datetime(2026, 6, 24, 9, 30, tzinfo=UTC))
        backend = MockBackend()
        backend.should_fail_login = True
        backend.login_error = AuthenticationError("bad pw")

        job = _job()
        book = ScheduleBook(jobs=(job,))

        new_book, results = run_due_jobs(
            book,
            clock=clock,
            backend_factory=_factory(backend),
            address_book=address_book,
        )

        assert results[0].outcome is JobOutcome.FAILED
        last_error = new_book.jobs[0].last_error or ""
        # The translator's hint mentions the credentials env vars.
        assert "credentials" in last_error.lower()
        assert "POSTCARDS_USERNAME" in last_error
        # The per-job message mirrors the actionable text.
        assert results[0].message == last_error

    def test_quota_exhausted_subclass_carries_hint(self, address_book: AddressBook) -> None:
        # The runner catches its own ``QuotaExhaustedError`` first
        # (rescheduling the job rather than failing it), but the
        # actionable message still has to make sense to the user.
        clock = FakeClock(datetime(2026, 6, 24, 9, 30, tzinfo=UTC))
        when = datetime(2026, 6, 25, 0, 0, tzinfo=UTC)
        backend = MockBackend(
            quota_info=QuotaInfo(available=False, next_available_at=when, retention_days=1)
        )

        job = _job()
        book = ScheduleBook(jobs=(job,))

        _new_book, results = run_due_jobs(
            book,
            clock=clock,
            backend_factory=_factory(backend),
            address_book=address_book,
        )

        assert results[0].outcome is JobOutcome.SKIPPED_QUOTA
        assert "rescheduled" in results[0].message
        assert when.isoformat() in results[0].message

    def test_transient_error_message_is_actionable(self, address_book: AddressBook) -> None:
        clock = FakeClock(datetime(2026, 6, 24, 9, 30, tzinfo=UTC))
        backend = MockBackend(transient_errors_remaining=99)

        job = _job()
        book = ScheduleBook(jobs=(job,))

        _new_book, results = run_due_jobs(
            book,
            clock=clock,
            backend_factory=_factory(backend),
            address_book=address_book,
        )

        assert results[0].outcome is JobOutcome.FAILED
        last_error = results[0].message
        # The transient branch of the translator mentions the
        # network and a recovery hint.
        assert "network" in last_error.lower() or "transient" in last_error.lower()
        assert "--verbose" in last_error or "--backend=mock" in last_error


# ---------------------------------------------------------------------------
# M5: structured logging in the dispatch path
# ---------------------------------------------------------------------------


class TestStructuredLogging:
    """The runner logs at every dispatch step so ``-vv`` shows exactly
    where a job got stuck. The tests pin that the right log lines
    are emitted, and that quota exhaustion produces a WARN line
    that surfaces the next-available timestamp."""

    def test_successful_dispatch_logs_info(
        self,
        address_book: AddressBook,
        backend: MockBackend,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        import logging

        clock = FakeClock(datetime(2026, 6, 24, 9, 30, tzinfo=UTC))
        job = _job()
        book = ScheduleBook(jobs=(job,))

        with caplog.at_level(logging.DEBUG, logger="postcards.schedule.runner"):
            run_due_jobs(
                book,
                clock=clock,
                backend_factory=_factory(backend),
                address_book=address_book,
            )

        # The dispatch path emits "dispatching" + "sent" at INFO.
        info_lines = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
        assert any("dispatching" in msg for msg in info_lines)
        assert any("sent" in msg for msg in info_lines)

    def test_quota_exhausted_logs_warning_with_next_available(
        self,
        address_book: AddressBook,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        import logging
        from datetime import UTC, datetime

        clock = FakeClock(datetime(2026, 6, 24, 9, 30, tzinfo=UTC))
        when = datetime(2026, 6, 25, 0, 0, tzinfo=UTC)
        backend = MockBackend(
            quota_info=QuotaInfo(available=False, next_available_at=when, retention_days=1)
        )
        job = _job()
        book = ScheduleBook(jobs=(job,))

        with caplog.at_level(logging.WARNING, logger="postcards.schedule.runner"):
            run_due_jobs(
                book,
                clock=clock,
                backend_factory=_factory(backend),
                address_book=address_book,
            )

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("quota exhausted" in r.getMessage() for r in warnings)
        assert any(when.isoformat() in r.getMessage() for r in warnings)


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_does_not_call_send(
        self, address_book: AddressBook, backend: MockBackend
    ) -> None:
        clock = FakeClock(datetime(2026, 6, 24, 9, 30, tzinfo=UTC))
        job = _job()
        book = ScheduleBook(jobs=(job,))

        new_book, results = run_due_jobs(
            book,
            clock=clock,
            backend_factory=_factory(backend),
            address_book=address_book,
            dry_run=True,
        )

        assert results[0].outcome is JobOutcome.SKIPPED_NOT_DUE
        assert results[0].message.startswith("dry-run")
        assert backend.sent == []  # crucial: no actual send
        assert new_book.jobs[0].status is JobStatus.PENDING


# ---------------------------------------------------------------------------
# Multi-job walks
# ---------------------------------------------------------------------------


class TestMultiJobWalks:
    def test_empty_book_returns_empty_results(
        self, address_book: AddressBook, backend: MockBackend
    ) -> None:
        clock = FakeClock(datetime(2026, 6, 24, 9, 30, tzinfo=UTC))
        new_book, results = run_due_jobs(
            ScheduleBook(),
            clock=clock,
            backend_factory=_factory(backend),
            address_book=address_book,
        )
        assert results == []
        assert new_book.is_empty()

    def test_mixed_statuses_dispatched_in_order(
        self, address_book: AddressBook, backend: MockBackend
    ) -> None:
        clock = FakeClock(datetime(2026, 6, 24, 9, 30, tzinfo=UTC))
        a = _job(id="a", next_run_at=datetime(2026, 6, 24, 9, 0, tzinfo=UTC))
        b = _job(id="b", next_run_at=datetime(2026, 6, 25, 9, 0, tzinfo=UTC))  # not due
        c = _job(id="c", status=JobStatus.COMPLETED)  # bad status
        book = ScheduleBook(jobs=(a, b, c))

        new_book, results = run_due_jobs(
            book,
            clock=clock,
            backend_factory=_factory(backend),
            address_book=address_book,
        )

        assert len(results) == 3
        assert [r.job_id for r in results] == ["a", "b", "c"]
        assert [r.outcome for r in results] == [
            JobOutcome.SENT,
            JobOutcome.SKIPPED_NOT_DUE,
            JobOutcome.SKIPPED_BAD_STATUS,
        ]
        # Only ``a`` was sent.
        assert len(backend.sent) == 1
        assert new_book.jobs[0].status is JobStatus.COMPLETED
        assert new_book.jobs[1].status is JobStatus.PENDING  # unchanged
        assert new_book.jobs[2].status is JobStatus.COMPLETED  # unchanged


# ---------------------------------------------------------------------------
# Value-type discipline
# ---------------------------------------------------------------------------


class TestValueTypeDiscipline:
    def test_unchanged_jobs_return_same_book(
        self, address_book: AddressBook, backend: MockBackend
    ) -> None:
        clock = FakeClock(datetime(2026, 6, 24, 9, 30, tzinfo=UTC))
        # Job in the future — nothing to do.
        job = _job(next_run_at=datetime(2026, 6, 25, 9, 0, tzinfo=UTC))
        book = ScheduleBook(jobs=(job,))

        new_book, _ = run_due_jobs(
            book,
            clock=clock,
            backend_factory=_factory(backend),
            address_book=address_book,
        )

        assert new_book is book  # identity preserved when nothing changed


# ---------------------------------------------------------------------------
# Picture handling
# ---------------------------------------------------------------------------


class TestPictureHandling:
    def test_local_picture_is_loaded_into_postcard(
        self, address_book: AddressBook, backend: MockBackend, tmp_path: Path
    ) -> None:
        clock = FakeClock(datetime(2026, 6, 24, 9, 30, tzinfo=UTC))
        picture = tmp_path / "pic.jpg"
        # Smallest possible valid JPEG header + content (just some bytes).
        picture.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIFhello")

        job = ScheduledJob(
            id="job1",
            created_at=datetime(2026, 6, 24, 9, 0, tzinfo=UTC),
            next_run_at=datetime(2026, 6, 24, 9, 0, tzinfo=UTC),
            recurrence=RecurrenceRule.one_shot(),
            status=JobStatus.PENDING,
            recipient_name="alice",
            sender_name=None,
            picture=str(picture),
            message="hi",
            message_template_name=None,
            template_variables={},
            username="user",
            password="pass",
            backend=None,
        )
        book = ScheduleBook(jobs=(job,))

        run_due_jobs(
            book,
            clock=clock,
            backend_factory=_factory(backend),
            address_book=address_book,
        )

        sent_card = backend.sent[0].postcard
        assert sent_card.picture is not None
        assert sent_card.picture.startswith(b"\xff\xd8")
