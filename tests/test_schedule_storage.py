"""Unit tests for :mod:`postcards.schedule.storage`.

Covers the atomic-write path, missing-file semantics, and the
JSON envelope's schema-version handling.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from postcards.schedule import (
    JobStatus,
    RecurrenceRule,
    ScheduleBook,
    ScheduledJob,
    load_schedule_book,
    save_schedule_book,
)
from postcards.schedule.models import ScheduleError
from postcards.schedule.storage import SCHEDULE_BOOK_FILENAME


def _make_job(job_id: str = "abc", **overrides: object) -> ScheduledJob:
    """Build a minimal :class:`ScheduledJob` for storage tests."""
    defaults: dict[str, object] = {
        "id": job_id,
        "created_at": datetime(2026, 6, 24, 9, 0, tzinfo=UTC),
        "next_run_at": datetime(2026, 6, 25, 9, 0, tzinfo=UTC),
        "recurrence": RecurrenceRule.one_shot(),
        "status": JobStatus.PENDING,
        "recipient_name": "alice",
        "sender_name": None,
        "picture": None,
        "message": "hello",
        "message_template_name": None,
        "template_variables": {},
        "username": None,
        "password": None,
        "backend": None,
    }
    defaults.update(overrides)
    return ScheduledJob(**defaults)  # type: ignore[arg-type]


class TestScheduleBookRoundTrip:
    def test_empty_book_round_trips(self, tmp_path: Path) -> None:
        path = tmp_path / SCHEDULE_BOOK_FILENAME
        save_schedule_book(ScheduleBook(), path=path)
        restored = load_schedule_book(path=path)
        assert restored.is_empty()

    def test_single_job_round_trips(self, tmp_path: Path) -> None:
        path = tmp_path / SCHEDULE_BOOK_FILENAME
        book = ScheduleBook(jobs=(_make_job("a"),))
        save_schedule_book(book, path=path)
        restored = load_schedule_book(path=path)
        assert restored == book

    def test_multi_job_round_trips_preserving_order(self, tmp_path: Path) -> None:
        path = tmp_path / SCHEDULE_BOOK_FILENAME
        book = ScheduleBook(jobs=(_make_job("a"), _make_job("b"), _make_job("c")))
        save_schedule_book(book, path=path)
        restored = load_schedule_book(path=path)
        assert [j.id for j in restored.jobs] == ["a", "b", "c"]

    def test_recurring_job_round_trips(self, tmp_path: Path) -> None:
        path = tmp_path / SCHEDULE_BOOK_FILENAME
        book = ScheduleBook(jobs=(_make_job("a", recurrence=RecurrenceRule.every_n_days(7)),))
        save_schedule_book(book, path=path)
        restored = load_schedule_book(path=path)
        assert restored.jobs[0].recurrence == RecurrenceRule.every_n_days(7)


class TestScheduleBookLoadMissing:
    def test_missing_file_returns_empty_book(self, tmp_path: Path) -> None:
        path = tmp_path / SCHEDULE_BOOK_FILENAME
        # File does not exist — load should not raise.
        book = load_schedule_book(path=path)
        assert book.is_empty()


class TestScheduleBookLoadCorrupt:
    def test_invalid_json_raises_schedule_error(self, tmp_path: Path) -> None:
        path = tmp_path / SCHEDULE_BOOK_FILENAME
        path.write_text("not valid json", encoding="utf-8")
        with pytest.raises(ScheduleError, match="failed to parse"):
            load_schedule_book(path=path)

    def test_top_level_array_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / SCHEDULE_BOOK_FILENAME
        path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        with pytest.raises(ScheduleError, match="must contain a JSON object"):
            load_schedule_book(path=path)

    def test_unsupported_version_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / SCHEDULE_BOOK_FILENAME
        path.write_text(json.dumps({"version": 99, "jobs": []}), encoding="utf-8")
        with pytest.raises(ScheduleError, match="unsupported schedule-book version"):
            load_schedule_book(path=path)

    def test_missing_version_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / SCHEDULE_BOOK_FILENAME
        path.write_text(json.dumps({"jobs": []}), encoding="utf-8")
        with pytest.raises(ScheduleError, match="missing 'version'"):
            load_schedule_book(path=path)

    def test_non_list_jobs_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / SCHEDULE_BOOK_FILENAME
        path.write_text(json.dumps({"version": 1, "jobs": "oops"}), encoding="utf-8")
        with pytest.raises(ScheduleError, match="must be a list"):
            load_schedule_book(path=path)


class TestScheduleBookAtomicWrite:
    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "dir" / SCHEDULE_BOOK_FILENAME
        save_schedule_book(ScheduleBook(jobs=(_make_job(),)), path=path)
        assert path.is_file()

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        path = tmp_path / SCHEDULE_BOOK_FILENAME
        save_schedule_book(ScheduleBook(jobs=(_make_job("a"),)), path=path)
        save_schedule_book(ScheduleBook(jobs=(_make_job("b"),)), path=path)
        restored = load_schedule_book(path=path)
        assert [j.id for j in restored.jobs] == ["b"]

    def test_no_tmp_files_left_behind(self, tmp_path: Path) -> None:
        path = tmp_path / SCHEDULE_BOOK_FILENAME
        save_schedule_book(ScheduleBook(jobs=(_make_job(),)), path=path)
        # ``mkstemp`` uses ``.tmp-`` prefix; check no leftovers.
        leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".tmp-")]
        assert leftovers == []


class TestScheduleBookOnDiskSchema:
    def test_disk_envelope_has_version_and_jobs(self, tmp_path: Path) -> None:
        path = tmp_path / SCHEDULE_BOOK_FILENAME
        save_schedule_book(ScheduleBook(jobs=(_make_job("a"),)), path=path)
        envelope = json.loads(path.read_text(encoding="utf-8"))
        assert envelope["version"] == 1
        assert isinstance(envelope["jobs"], list)
        assert envelope["jobs"][0]["id"] == "a"

    def test_disk_envelope_is_pretty_printed(self, tmp_path: Path) -> None:
        path = tmp_path / SCHEDULE_BOOK_FILENAME
        save_schedule_book(ScheduleBook(jobs=(_make_job(),)), path=path)
        text = path.read_text(encoding="utf-8")
        # ``json.dump(..., indent=2, sort_keys=True)`` produces
        # lines that start with two spaces for nested fields.
        assert "\n  " in text
