"""Unit tests for the JSON-on-disk persistence layer.

The :mod:`postcards.addressbook.storage` module owns loading and
saving the address-book and template-book files. The tests cover
the full round trip (write → read), the missing-file default
(empty book), the atomic-write guarantee, and the error
surfaces the CLI relies on (corrupt JSON, schema mismatch).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from postcards.addressbook.models import (
    AddressBook,
    AddressBookEntry,
    AddressCategory,
    MessageTemplate,
    TemplateBook,
    TemplateError,
)
from postcards.addressbook.storage import (
    load_address_book,
    load_template_book,
    save_address_book,
    save_template_book,
)
from postcards.backend.base import AddressSpec


@pytest.fixture(autouse=True)
def isolate_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Point the storage layer at ``tmp_path`` so tests are hermetic.

    ``POSTCARDS_DATA_DIR`` is honoured by
    :func:`postcards.addressbook.paths.data_dir`, which is what
    both loaders and savers fall back to when no explicit
    ``path=`` is supplied.
    """
    monkeypatch.setenv("POSTCARDS_DATA_DIR", str(tmp_path))
    yield


def _sample_address_book() -> AddressBook:
    alice = AddressBookEntry(
        name="alice",
        category=AddressCategory.RECIPIENT,
        address=AddressSpec(
            prename="Alice",
            lastname="Zuercher",
            street="Bahnhofstrasse 1",
            zip_code="8000",
            place="Zurich",
        ),
        notes="friend",
    )
    home = AddressBookEntry(
        name="home",
        category=AddressCategory.SENDER,
        address=AddressSpec(
            prename="Andrin",
            lastname="Bertschi",
            street="Lagerstrasse 1",
            zip_code="8000",
            place="Zurich",
            country="CH",
        ),
    )
    return AddressBook(entries=(alice, home))


def _sample_template_book() -> TemplateBook:
    return TemplateBook(
        templates=(
            MessageTemplate(
                name="greeting",
                body="Hi $name, greetings from Zurich",
                description="default greeting",
            ),
            MessageTemplate(name="birthday", body="Happy birthday, $name!"),
        )
    )


class TestLoadAddressBook:
    def test_missing_file_returns_empty_book(self, tmp_path: Path) -> None:
        target = tmp_path / "missing.json"
        book = load_address_book(target)
        assert book.is_empty()

    def test_round_trip(self, tmp_path: Path) -> None:
        target = tmp_path / "addressbook.json"
        original = _sample_address_book()
        save_address_book(original, target)
        loaded = load_address_book(target)
        assert loaded == original

    def test_persists_with_default_path(self) -> None:
        original = _sample_address_book()
        path = save_address_book(original)
        assert path.is_file()
        # Reload through the default path (which honours the
        # ``POSTCARDS_DATA_DIR`` env var our fixture sets).
        loaded = load_address_book()
        assert loaded == original

    def test_rejects_corrupt_json(self, tmp_path: Path) -> None:
        target = tmp_path / "corrupt.json"
        target.write_text("{not json", encoding="utf-8")
        with pytest.raises(TemplateError, match="failed to parse"):
            load_address_book(target)

    def test_rejects_non_object_top_level(self, tmp_path: Path) -> None:
        target = tmp_path / "not-object.json"
        target.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(TemplateError, match="must contain a JSON object"):
            load_address_book(target)


class TestSaveAddressBook:
    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "deeper" / "addressbook.json"
        save_address_book(_sample_address_book(), target)
        assert target.is_file()

    def test_writes_pretty_json(self, tmp_path: Path) -> None:
        target = tmp_path / "addressbook.json"
        save_address_book(_sample_address_book(), target)
        text = target.read_text(encoding="utf-8")
        # ``indent=2`` produces a multi-line file with no
        # trailing junk.
        assert "\n" in text
        # ``sort_keys=True`` keeps the output deterministic —
        # the on-disk schema is independent of dict insertion
        # order.
        payload = json.loads(text)
        assert payload["version"] == 1
        assert {entry["name"] for entry in payload["entries"]} == {"alice", "home"}

    def test_atomic_write_leaves_no_temp_files_on_success(self, tmp_path: Path) -> None:
        target = tmp_path / "addressbook.json"
        save_address_book(_sample_address_book(), target)
        leftovers = [entry for entry in os.listdir(target.parent) if entry.startswith(".tmp-")]
        assert leftovers == []

    def test_atomic_write_does_not_leave_temp_on_failure(self, tmp_path: Path) -> None:
        # Inject a failure into the ``json.dump`` call by
        # monkey-patching ``json.dump`` to raise. The save must
        # propagate the exception and clean up the temp file.
        target = tmp_path / "addressbook.json"

        def boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("simulated write failure")

        import postcards.addressbook.storage as storage_mod

        original_dump = storage_mod.json.dump
        storage_mod.json.dump = boom
        try:
            with pytest.raises(RuntimeError, match="simulated write failure"):
                save_address_book(_sample_address_book(), target)
        finally:
            storage_mod.json.dump = original_dump

        # The destination file does not exist (the rename
        # never happened) and the temp file is cleaned up.
        assert not target.exists()
        leftovers = [entry for entry in os.listdir(target.parent) if entry.startswith(".tmp-")]
        assert leftovers == []


class TestLoadTemplateBook:
    def test_missing_file_returns_empty_book(self, tmp_path: Path) -> None:
        target = tmp_path / "missing.json"
        book = load_template_book(target)
        assert book.is_empty()

    def test_round_trip(self, tmp_path: Path) -> None:
        target = tmp_path / "templates.json"
        original = _sample_template_book()
        save_template_book(original, target)
        loaded = load_template_book(target)
        assert loaded == original

    def test_rejects_unsupported_version(self, tmp_path: Path) -> None:
        target = tmp_path / "bad-version.json"
        target.write_text(
            json.dumps({"version": 99, "templates": []}),
            encoding="utf-8",
        )
        with pytest.raises(TemplateError, match="unsupported template-book version"):
            load_template_book(target)


class TestSaveTemplateBook:
    def test_round_trip_through_default_path(self) -> None:
        original = _sample_template_book()
        path = save_template_book(original)
        assert path.is_file()
        loaded = load_template_book()
        assert loaded == original

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        target = tmp_path / "templates.json"
        first = TemplateBook(templates=(MessageTemplate(name="greeting", body="Hi $name"),))
        second = TemplateBook(
            templates=(
                MessageTemplate(name="greeting", body="Hi $name!"),
                MessageTemplate(name="birthday", body="Happy birthday $name"),
            )
        )
        save_template_book(first, target)
        save_template_book(second, target)
        assert load_template_book(target) == second
