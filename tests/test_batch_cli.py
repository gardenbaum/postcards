"""Integration tests for ``postcards batch``.

Exercises the full CLI stack (Typer → ``do_command_send`` →
mocked ``send_free_card``) with all three recipient sources
(--to-many, --to-all-recipients, --manifest CSV, --manifest
YAML) and the per-recipient override semantics.

Mirrors :mod:`tests.test_send_addressbook_integration` —
the legacy shim's network methods are monkey-patched so the
CLI never touches the live Swiss Post endpoint.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from postcards._vendor.postcard_creator import Token
from postcards._vendor.postcard_creator.postcard_creator import (
    Postcard as _ShimPostcard,
)
from postcards._vendor.postcard_creator.postcard_creator import PostcardCreatorBase
from postcards.addressbook.models import (
    AddressBook,
    AddressBookEntry,
    AddressCategory,
    AddressSpec,
)
from postcards.addressbook.storage import save_address_book
from postcards.cli import run as cli_run

# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolate_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
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


@pytest.fixture
def mock_shim_send(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Patch the shim so the CLI never reaches the network."""
    recorded: list[dict] = []

    def mock_has_valid_credentials(self: Token, username: str | None, password: str | None) -> bool:
        self.token = "<mocked-token>"
        return bool(username and password)

    def mock_has_free_postcard(self: PostcardCreatorBase) -> bool:
        return True

    def mock_send_free_card(self: PostcardCreatorBase, *args: object, **kwargs: object) -> bool:
        def capture(card: object) -> bool:
            if isinstance(card, _ShimPostcard):
                recorded.append(
                    {
                        "message": getattr(card, "message", ""),
                        "recipient": _recipient_to_dict(card.recipient),
                        "sender": _sender_to_dict(card.sender),
                    }
                )
                return True
            return False

        for arg in args:
            if capture(arg):
                return True
        if "postcard" in kwargs and capture(kwargs["postcard"]):
            return True
        return True

    monkeypatch.setattr(Token, "has_valid_credentials", mock_has_valid_credentials)
    monkeypatch.setattr(PostcardCreatorBase, "has_free_postcard", mock_has_free_postcard)
    monkeypatch.setattr(PostcardCreatorBase, "send_free_card", mock_send_free_card)
    return recorded


def _recipient_to_dict(recipient: object) -> dict[str, str]:
    return {
        "prename": getattr(recipient, "prename", ""),
        "lastname": getattr(recipient, "lastname", ""),
        "street": getattr(recipient, "street", ""),
        "zipcode": getattr(recipient, "zip_code", ""),
        "city": getattr(recipient, "place", ""),
    }


def _sender_to_dict(sender: object) -> dict[str, str]:
    return {
        "prename": getattr(sender, "prename", ""),
        "lastname": getattr(sender, "lastname", ""),
        "street": getattr(sender, "street", ""),
        "zipcode": getattr(sender, "zip_code", ""),
        "city": getattr(sender, "place", ""),
    }


def _invoke(*args: str) -> Any:
    """Run ``cli_run`` with the supplied argv and return the result."""
    return cli_run(list(args))


@pytest.fixture
def three_recipients() -> AddressBook:
    """Return an address book with three recipients."""
    return AddressBook(
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
            AddressBookEntry(
                name="bob",
                category=AddressCategory.RECIPIENT,
                address=AddressSpec(
                    prename="Bob",
                    lastname="Smith",
                    street="Bahnhofstrasse 2",
                    zip_code="3011",
                    place="Bern",
                ),
            ),
            AddressBookEntry(
                name="charlie",
                category=AddressCategory.RECIPIENT,
                address=AddressSpec(
                    prename="Charlie",
                    lastname="Brown",
                    street="Rue du Lac 3",
                    zip_code="1003",
                    place="Lausanne",
                ),
            ),
        )
    )


@pytest.fixture
def saved_address_book(three_recipients: AddressBook) -> None:
    save_address_book(three_recipients)


# ---------------------------------------------------------------------------
# --to-many
# ---------------------------------------------------------------------------


class TestBatchToMany:
    def test_sends_to_each_named_recipient(
        self, saved_address_book: None, mock_shim_send: list[dict]
    ) -> None:
        result = _invoke(
            "batch",
            "--to-many",
            "alice,bob",
            "--message",
            "Hi",
            "--username",
            "user",
            "--password",
            "pass",
        )
        assert result.exit_code == 0, result.output
        assert len(mock_shim_send) == 2
        names = sorted(r["recipient"]["prename"] for r in mock_shim_send)
        assert names == ["Alice", "Bob"]

    def test_summary_prints_count(
        self, saved_address_book: None, mock_shim_send: list[dict]
    ) -> None:
        result = _invoke(
            "batch",
            "--to-many",
            "alice",
            "--message",
            "Hi",
            "--username",
            "user",
            "--password",
            "pass",
        )
        assert result.exit_code == 0, result.output
        assert "sent 1/1 postcards" in result.output

    def test_unknown_recipient_exits_with_error(
        self, saved_address_book: None, mock_shim_send: list[dict]
    ) -> None:
        result = _invoke(
            "batch",
            "--to-many",
            "alice,nobody",
            "--message",
            "Hi",
            "--username",
            "user",
            "--password",
            "pass",
        )
        assert result.exit_code != 0
        assert "nobody" in result.output
        # The legacy ``do_command_send`` aborts via ``sys.exit``
        # on the first invalid recipient, so ``alice`` is never
        # sent when the loop hits ``nobody``; the failure is
        # surfaced through the per-recipient summary. We assert
        # ``alice`` does NOT show up in the sent list to lock in
        # that behaviour.
        assert mock_shim_send == []


# ---------------------------------------------------------------------------
# --to-all-recipients
# ---------------------------------------------------------------------------


class TestBatchToAllRecipients:
    def test_sends_to_every_recipient(
        self, saved_address_book: None, mock_shim_send: list[dict]
    ) -> None:
        result = _invoke(
            "batch",
            "--to-all-recipients",
            "--message",
            "Hi all",
            "--username",
            "user",
            "--password",
            "pass",
        )
        assert result.exit_code == 0, result.output
        assert len(mock_shim_send) == 3
        names = sorted(r["recipient"]["prename"] for r in mock_shim_send)
        assert names == ["Alice", "Bob", "Charlie"]

    def test_excludes_senders_from_address_book(self, mock_shim_send: list[dict]) -> None:
        # Build an address book with one recipient and one sender.
        # ``--to-all-recipients`` should only pick the recipient.
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
                AddressBookEntry(
                    name="me",
                    category=AddressCategory.SENDER,
                    address=AddressSpec(
                        prename="M",
                        lastname="E",
                        street="X 1",
                        zip_code="8000",
                        place="Zurich",
                    ),
                ),
            )
        )
        save_address_book(book)

        result = _invoke(
            "batch",
            "--to-all-recipients",
            "--message",
            "Hi",
            "--username",
            "user",
            "--password",
            "pass",
        )
        assert result.exit_code == 0, result.output
        assert len(mock_shim_send) == 1
        assert mock_shim_send[0]["recipient"]["prename"] == "Alice"

    def test_empty_address_book_rejected(self, mock_shim_send: list[dict]) -> None:
        save_address_book(AddressBook())
        result = _invoke(
            "batch",
            "--to-all-recipients",
            "--message",
            "Hi",
            "--username",
            "user",
            "--password",
            "pass",
        )
        assert result.exit_code != 0
        assert "no recipient entries" in result.output.lower()


# ---------------------------------------------------------------------------
# --manifest (CSV)
# ---------------------------------------------------------------------------


class TestBatchManifestCSV:
    def test_parses_csv_with_to_column(
        self, saved_address_book: None, mock_shim_send: list[dict], tmp_path: Path
    ) -> None:
        manifest = tmp_path / "recipients.csv"
        manifest.write_text(
            "to,message\nalice,Hi Alice\nbob,Hi Bob\n",
            encoding="utf-8",
        )
        result = _invoke(
            "batch",
            "--manifest",
            str(manifest),
            "--username",
            "user",
            "--password",
            "pass",
        )
        assert result.exit_code == 0, result.output
        assert len(mock_shim_send) == 2
        messages = sorted(r["message"] for r in mock_shim_send)
        assert messages == ["Hi Alice", "Hi Bob"]

    def test_csv_without_to_column_rejected(
        self, saved_address_book: None, mock_shim_send: list[dict], tmp_path: Path
    ) -> None:
        manifest = tmp_path / "bad.csv"
        manifest.write_text("name,message\nalice,hi\n", encoding="utf-8")
        result = _invoke(
            "batch",
            "--manifest",
            str(manifest),
            "--message",
            "Hi",
            "--username",
            "user",
            "--password",
            "pass",
        )
        assert result.exit_code != 0
        assert "to" in result.output.lower()

    def test_per_recipient_message_overrides_shared(
        self, saved_address_book: None, mock_shim_send: list[dict], tmp_path: Path
    ) -> None:
        manifest = tmp_path / "recipients.csv"
        manifest.write_text("to,message\nalice,Custom for Alice\n", encoding="utf-8")
        result = _invoke(
            "batch",
            "--manifest",
            str(manifest),
            "--message",
            "Shared",
            "--username",
            "user",
            "--password",
            "pass",
        )
        assert result.exit_code == 0, result.output
        assert mock_shim_send[0]["message"] == "Custom for Alice"


# ---------------------------------------------------------------------------
# --manifest (YAML)
# ---------------------------------------------------------------------------


class TestBatchManifestYAML:
    def test_yaml_flat_list_of_names(
        self, saved_address_book: None, mock_shim_send: list[dict], tmp_path: Path
    ) -> None:
        manifest = tmp_path / "recipients.yaml"
        manifest.write_text(
            yaml.safe_dump({"recipients": ["alice", "bob"]}),
            encoding="utf-8",
        )
        result = _invoke(
            "batch",
            "--manifest",
            str(manifest),
            "--message",
            "Hi",
            "--username",
            "user",
            "--password",
            "pass",
        )
        assert result.exit_code == 0, result.output
        assert len(mock_shim_send) == 2

    def test_yaml_top_level_list(
        self, saved_address_book: None, mock_shim_send: list[dict], tmp_path: Path
    ) -> None:
        manifest = tmp_path / "recipients.yaml"
        manifest.write_text(yaml.safe_dump(["alice", "bob"]), encoding="utf-8")
        result = _invoke(
            "batch",
            "--manifest",
            str(manifest),
            "--message",
            "Hi",
            "--username",
            "user",
            "--password",
            "pass",
        )
        assert result.exit_code == 0, result.output
        assert len(mock_shim_send) == 2

    def test_yaml_list_of_objects_with_overrides(
        self, saved_address_book: None, mock_shim_send: list[dict], tmp_path: Path
    ) -> None:
        manifest = tmp_path / "recipients.yaml"
        manifest.write_text(
            yaml.safe_dump(
                {
                    "recipients": [
                        {"to": "alice", "message": "Hi Alice"},
                        {"to": "bob", "message": "Hi Bob"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        result = _invoke(
            "batch",
            "--manifest",
            str(manifest),
            "--username",
            "user",
            "--password",
            "pass",
        )
        assert result.exit_code == 0, result.output
        assert len(mock_shim_send) == 2
        messages = sorted(r["message"] for r in mock_shim_send)
        assert messages == ["Hi Alice", "Hi Bob"]

    def test_yaml_object_missing_to_rejected(
        self, saved_address_book: None, mock_shim_send: list[dict], tmp_path: Path
    ) -> None:
        manifest = tmp_path / "bad.yaml"
        manifest.write_text(
            yaml.safe_dump({"recipients": [{"message": "oops"}]}),
            encoding="utf-8",
        )
        result = _invoke(
            "batch",
            "--manifest",
            str(manifest),
            "--message",
            "Hi",
            "--username",
            "user",
            "--password",
            "pass",
        )
        assert result.exit_code != 0
        assert "to" in result.output.lower()

    def test_yaml_malformed_rejected(
        self, saved_address_book: None, mock_shim_send: list[dict], tmp_path: Path
    ) -> None:
        manifest = tmp_path / "bad.yaml"
        manifest.write_text("recipients: [unclosed", encoding="utf-8")
        result = _invoke(
            "batch",
            "--manifest",
            str(manifest),
            "--message",
            "Hi",
            "--username",
            "user",
            "--password",
            "pass",
        )
        assert result.exit_code != 0
        assert "yaml" in result.output.lower() or "YAML" in result.output


# ---------------------------------------------------------------------------
# Mutually exclusive sources
# ---------------------------------------------------------------------------


class TestBatchSourceValidation:
    def test_no_source_rejected(self, saved_address_book: None) -> None:
        result = _invoke("batch", "--message", "Hi", "--username", "user", "--password", "pass")
        assert result.exit_code != 0
        assert "one of" in result.output.lower()

    def test_two_sources_rejected(self, saved_address_book: None) -> None:
        result = _invoke(
            "batch",
            "--to-many",
            "alice",
            "--to-all-recipients",
            "--message",
            "Hi",
            "--username",
            "user",
            "--password",
            "pass",
        )
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output.lower()

    def test_no_message_or_picture_rejected(self, saved_address_book: None) -> None:
        result = _invoke(
            "batch",
            "--to-many",
            "alice",
            "--username",
            "user",
            "--password",
            "pass",
        )
        assert result.exit_code != 0
        assert "either" in result.output.lower()


# ---------------------------------------------------------------------------
# Help text
# ---------------------------------------------------------------------------


class TestBatchHelp:
    def test_help_lists_all_sources(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Rich help rendering wraps option names when the terminal width is
        # too narrow, which can hide ``--to-many`` behind a line break on
        # some Rich versions / non-TTY environments (notably the GitHub
        # Actions runner with rich>=15). Pin a wide width so the rendered
        # help reliably contains every option name on its own line.
        monkeypatch.setenv("COLUMNS", "200")

        result = _invoke("batch", "--help")
        assert result.exit_code == 0
        assert "--to-many" in result.output
        assert "--to-all-recipients" in result.output
        assert "--manifest" in result.output
