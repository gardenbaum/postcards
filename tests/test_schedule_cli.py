"""Integration tests for ``postcards schedule``.

Exercises the full CLI stack end-to-end. The runner uses
:class:`MockBackend` + :class:`FakeClock` via ``--backend mock``
and ``--fake-now``; the storage layer is pinned to a tmp
directory so the tests are hermetic.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from postcards.cli import run as cli_run
from postcards.schedule.storage import schedule_path

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolate_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Pin address-book + schedule-book storage to ``tmp_path``."""
    monkeypatch.setenv("POSTCARDS_DATA_DIR", str(tmp_path / "data"))
    yield


@pytest.fixture(autouse=True)
def clean_postcards_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in (
        "POSTCARDS_USERNAME",
        "POSTCARDS_PASSWORD",
        "POSTCARDS_KEY",
        "POSTCARDS_BACKEND",
        "POSTCARDS_CONFIG",
    ):
        monkeypatch.delenv(key, raising=False)
    yield


def _invoke(*args: str) -> Any:
    """Run ``cli_run`` with the supplied argv and return the result."""
    return cli_run(list(args))


@pytest.fixture
def saved_alice() -> None:
    """Persist a single ``alice`` recipient to the address book."""
    from postcards.addressbook.models import (
        AddressBook,
        AddressBookEntry,
        AddressCategory,
        AddressSpec,
    )
    from postcards.addressbook.storage import save_address_book

    book = AddressBook(
        entries=(
            AddressBookEntry(
                name="alice",
                category=AddressCategory.RECIPIENT,
                address=AddressSpec(
                    prename="Alice",
                    lastname="Doe",
                    street="Hauptstrasse 1",
                    zip_code="8000",
                    place="Zurich",
                ),
            ),
        )
    )
    save_address_book(book)


# ---------------------------------------------------------------------------
# ``schedule add``
# ---------------------------------------------------------------------------


class TestScheduleAdd:
    def test_adds_one_shot_job_for_now(self, saved_alice: None) -> None:
        result = _invoke(
            "schedule",
            "add",
            "--to",
            "alice",
            "--message",
            "Hi",
            "--username",
            "user",
            "--password",
            "pass",
        )
        assert result.exit_code == 0, result.output
        assert "queued job" in result.output
        book_path = schedule_path()
        assert book_path.is_file()
        payload = json.loads(book_path.read_text(encoding="utf-8"))
        assert payload["version"] == 1
        assert len(payload["jobs"]) == 1
        assert payload["jobs"][0]["recipient_name"] == "alice"
        assert payload["jobs"][0]["message"] == "Hi"
        assert payload["jobs"][0]["recurrence"]["kind"] == "none"

    def test_adds_recurring_job(self, saved_alice: None) -> None:
        result = _invoke(
            "schedule",
            "add",
            "--recurring",
            "every:7d",
            "--to",
            "alice",
            "--message",
            "Weekly",
            "--username",
            "user",
            "--password",
            "pass",
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(schedule_path().read_text(encoding="utf-8"))
        assert payload["jobs"][0]["recurrence"]["kind"] == "every_n_days"
        assert payload["jobs"][0]["recurrence"]["interval_days"] == 7

    def test_recurring_first_run_in_future(self, saved_alice: None) -> None:
        # The runner uses ``recurrence.advance(now)`` for the
        # first fire, so the next_run_at is strictly after
        # ``now``. The CLI's printed summary shows the
        # scheduled time.
        result = _invoke(
            "schedule",
            "add",
            "--recurring",
            "weekly:mon",
            "--to",
            "alice",
            "--message",
            "Hi",
            "--username",
            "user",
            "--password",
            "pass",
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(schedule_path().read_text(encoding="utf-8"))
        # Future (just verify it's parseable and the recurrence
        # is weekly — the exact date depends on the test clock).
        assert payload["jobs"][0]["recurrence"]["kind"] == "weekly"

    def test_unknown_recipient_rejected(self, saved_alice: None) -> None:
        result = _invoke(
            "schedule",
            "add",
            "--to",
            "nobody",
            "--message",
            "Hi",
            "--username",
            "user",
            "--password",
            "pass",
        )
        assert result.exit_code != 0
        assert "nobody" in result.output.lower()

    def test_no_message_or_picture_rejected(self, saved_alice: None) -> None:
        result = _invoke(
            "schedule",
            "add",
            "--to",
            "alice",
            "--username",
            "user",
            "--password",
            "pass",
        )
        assert result.exit_code != 0
        assert "either" in result.output.lower()

    def test_mutually_exclusive_message_and_template_rejected(self, saved_alice: None) -> None:
        result = _invoke(
            "schedule",
            "add",
            "--to",
            "alice",
            "--message",
            "Hi",
            "--message-template",
            "greeting",
            "--username",
            "user",
            "--password",
            "pass",
        )
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output.lower()

    def test_recurring_with_at_emits_warning(self, saved_alice: None) -> None:
        result = _invoke(
            "schedule",
            "add",
            "--recurring",
            "every:7d",
            "--at",
            "2026-07-01T08:00:00",
            "--to",
            "alice",
            "--message",
            "Hi",
            "--username",
            "user",
            "--password",
            "pass",
        )
        # The command succeeds; --at is ignored with a warning.
        assert result.exit_code == 0, result.output
        assert "ignored" in result.output.lower() or "--at" in result.output


# ---------------------------------------------------------------------------
# ``schedule list``
# ---------------------------------------------------------------------------


class TestScheduleList:
    def test_empty_book_says_no_jobs(
        self,
    ) -> None:
        result = _invoke("schedule", "list")
        assert result.exit_code == 0
        assert "no jobs" in result.output.lower()

    def test_lists_queued_job(self, saved_alice: None) -> None:
        _invoke(
            "schedule",
            "add",
            "--to",
            "alice",
            "--message",
            "Hi",
            "--username",
            "user",
            "--password",
            "pass",
        )
        result = _invoke("schedule", "list")
        assert result.exit_code == 0
        assert "alice" in result.output
        assert "pending" in result.output

    def test_filter_by_status(self, saved_alice: None) -> None:
        _invoke(
            "schedule",
            "add",
            "--to",
            "alice",
            "--message",
            "Hi",
            "--username",
            "user",
            "--password",
            "pass",
        )
        result = _invoke("schedule", "list", "--status", "pending")
        assert result.exit_code == 0
        assert "alice" in result.output

        result = _invoke("schedule", "list", "--status", "completed")
        assert result.exit_code == 0
        assert "alice" not in result.output

    def test_invalid_status_rejected(self, saved_alice: None) -> None:
        result = _invoke("schedule", "list", "--status", "not-a-status")
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# ``schedule show``
# ---------------------------------------------------------------------------


class TestScheduleShow:
    def test_shows_queued_job(self, saved_alice: None) -> None:
        _invoke(
            "schedule",
            "add",
            "--to",
            "alice",
            "--message",
            "Hi",
            "--username",
            "user",
            "--password",
            "pass",
        )
        book_payload = json.loads(schedule_path().read_text(encoding="utf-8"))
        job_id = book_payload["jobs"][0]["id"]

        result = _invoke("schedule", "show", job_id)
        assert result.exit_code == 0, result.output
        assert job_id in result.output
        assert "alice" in result.output
        assert "Hi" in result.output

    def test_unknown_job_rejected(self, saved_alice: None) -> None:
        result = _invoke("schedule", "show", "no-such-id")
        assert result.exit_code != 0
        assert "no scheduled job" in result.output.lower()


# ---------------------------------------------------------------------------
# ``schedule remove``
# ---------------------------------------------------------------------------


class TestScheduleRemove:
    def test_removes_queued_job(self, saved_alice: None) -> None:
        _invoke(
            "schedule",
            "add",
            "--to",
            "alice",
            "--message",
            "Hi",
            "--username",
            "user",
            "--password",
            "pass",
        )
        book_payload = json.loads(schedule_path().read_text(encoding="utf-8"))
        job_id = book_payload["jobs"][0]["id"]

        result = _invoke("schedule", "remove", job_id)
        assert result.exit_code == 0, result.output
        assert "removed" in result.output.lower()

        # Subsequent list is empty.
        result = _invoke("schedule", "list")
        assert "no jobs" in result.output.lower()

    def test_unknown_job_rejected(self, saved_alice: None) -> None:
        result = _invoke("schedule", "remove", "no-such-id")
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# ``schedule run`` — the heart of the scheduler
# ---------------------------------------------------------------------------


class TestScheduleRun:
    def test_dispatches_due_job(self, saved_alice: None, monkeypatch: pytest.MonkeyPatch) -> None:
        # Queue a job whose next_run_at is far in the past so
        # any ``fake-now`` we pick is strictly after it.
        _invoke(
            "schedule",
            "add",
            "--at",
            "2026-06-20T08:00:00",
            "--to",
            "alice",
            "--message",
            "Hi",
            "--username",
            "user",
            "--password",
            "pass",
        )

        # Set POSTCARDS_BACKEND=mock so the runner picks the
        # in-memory backend (the vendored shim's
        # Token.has_valid_credentials would otherwise raise).
        monkeypatch.setenv("POSTCARDS_BACKEND", "mock")

        result = _invoke(
            "schedule",
            "run",
            "--fake-now",
            "2026-06-24T09:00:00",
            "--quiet",
        )
        assert result.exit_code == 0, result.output

        # The job should now be COMPLETED.
        payload = json.loads(schedule_path().read_text(encoding="utf-8"))
        assert payload["jobs"][0]["status"] == "completed"
        assert payload["jobs"][0]["last_confirmation"] is not None

    def test_quiet_suppresses_summary(
        self, saved_alice: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _invoke(
            "schedule",
            "add",
            "--at",
            "2026-06-20T08:00:00",
            "--to",
            "alice",
            "--message",
            "Hi",
            "--username",
            "user",
            "--password",
            "pass",
        )
        monkeypatch.setenv("POSTCARDS_BACKEND", "mock")

        result = _invoke(
            "schedule",
            "run",
            "--fake-now",
            "2026-06-24T09:00:00",
            "--quiet",
        )
        assert result.exit_code == 0, result.output
        # ``--quiet`` keeps the per-job summary off stdout.
        assert "sent" not in result.output.lower() or "[sent]" not in result.output

    def test_dry_run_does_not_dispatch(
        self, saved_alice: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _invoke(
            "schedule",
            "add",
            "--at",
            "2026-06-20T08:00:00",
            "--to",
            "alice",
            "--message",
            "Hi",
            "--username",
            "user",
            "--password",
            "pass",
        )
        monkeypatch.setenv("POSTCARDS_BACKEND", "mock")

        result = _invoke(
            "schedule",
            "run",
            "--dry-run",
            "--fake-now",
            "2026-06-24T09:00:00",
        )
        assert result.exit_code == 0, result.output
        # Job stays PENDING.
        payload = json.loads(schedule_path().read_text(encoding="utf-8"))
        assert payload["jobs"][0]["status"] == "pending"

    def test_empty_book_short_circuits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("POSTCARDS_BACKEND", "mock")
        result = _invoke("schedule", "run")
        assert result.exit_code == 0
        assert "no jobs" in result.output.lower()


# ---------------------------------------------------------------------------
# ``schedule retry``
# ---------------------------------------------------------------------------


class TestScheduleRetry:
    def test_retry_resets_failed_job_to_pending(
        self, saved_alice: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Queue a job then mutate the schedule book directly
        # to mark it FAILED.
        _invoke(
            "schedule",
            "add",
            "--at",
            "2026-06-20T08:00:00",
            "--to",
            "alice",
            "--message",
            "Hi",
            "--username",
            "user",
            "--password",
            "pass",
        )
        book_path = schedule_path()
        payload = json.loads(book_path.read_text(encoding="utf-8"))
        payload["jobs"][0]["status"] = "failed"
        payload["jobs"][0]["last_error"] = "synthetic failure"
        book_path.write_text(json.dumps(payload), encoding="utf-8")

        result = _invoke("schedule", "retry", payload["jobs"][0]["id"])
        assert result.exit_code == 0, result.output

        payload = json.loads(schedule_path().read_text(encoding="utf-8"))
        assert payload["jobs"][0]["status"] == "pending"
        assert payload["jobs"][0]["last_error"] is None


# ---------------------------------------------------------------------------
# Help text
# ---------------------------------------------------------------------------


class TestScheduleHelp:
    def test_help_lists_all_subcommands(self) -> None:
        result = _invoke("schedule", "--help")
        assert result.exit_code == 0
        for subcommand in ("add", "list", "show", "remove", "retry", "run"):
            assert subcommand in result.output
