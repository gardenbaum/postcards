"""Integration tests for the M4 ``send`` flow with the address book + template book.

The constitution (M0 §1) requires at least one integration
test that drives a MOCKED Swiss Post backend end-to-end. M4
extends that requirement to the new ``--to`` /
``--sender`` / ``--message-template`` flow: this file
exercises the full CLI stack (Typer → ``do_command_send`` →
mocked ``send_free_card``) with address-book entries and a
rendered template as inputs, and asserts the postcard the mock
backend receives carries the resolved recipient and the
rendered message.

The mock approach mirrors the one used in
``tests/test_send_integration.py`` and
``tests/test_typer_cli.py``: ``Token.has_valid_credentials``,
``PostcardCreatorBase.has_free_postcard``, and
``PostcardCreatorBase.send_free_card`` are monkey-patched so
the CLI never touches the live Swiss Post endpoint. The
``recorded`` list captures the call arguments the shim saw,
and the tests assert against it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner
from typer.testing import Result as CliResult

from postcards._vendor.postcard_creator import Token
from postcards._vendor.postcard_creator.postcard_creator import PostcardCreatorBase
from postcards.addressbook.models import (
    AddressBook,
    AddressBookEntry,
    AddressCategory,
    MessageTemplate,
    TemplateBook,
)
from postcards.addressbook.storage import (
    save_address_book,
    save_template_book,
)
from postcards.backend.base import AddressSpec
from postcards.cli import run as cli_run

# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def isolate_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Pin address-book + template-book storage to ``tmp_path``."""
    monkeypatch.setenv("POSTCARDS_DATA_DIR", str(tmp_path / "data"))
    yield
    return None


@pytest.fixture(autouse=True)
def clean_postcards_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Strip ``POSTCARDS_*`` env vars so the tests are hermetic.

    The constitution (post-M2 §2) makes these env vars the
    highest-priority source of credentials. Tests that do not
    intend to exercise that path explicitly clear them so the
    config-file path is the one under test.
    """
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
    """Patch the shim's network methods so the CLI never hits the live API.

    Returns the list of recorded send calls; the test asserts
    against it. Mirrors the fixture in
    ``tests/test_typer_cli.py``.
    """
    recorded: list[dict] = []

    def mock_has_valid_credentials(self: Token, username: str | None, password: str | None) -> bool:
        self.token = "<mocked-token>"
        return bool(username and password)

    def mock_has_free_postcard(self: PostcardCreatorBase) -> bool:
        return True

    def mock_send_free_card(self: PostcardCreatorBase, *args: object, **kwargs: object) -> bool:
        # The shim's ``send_free_card`` accepts the ``Postcard``
        # as either a positional argument or the ``postcard=``
        # keyword (depending on the shim version); capture the
        # bits the test cares about so a future shim signature
        # change does not require a matching test rewrite.
        from postcards._vendor.postcard_creator.postcard_creator import (
            Postcard as _ShimPostcard,
        )

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
        recorded.append({"raw_args": [repr(a) for a in args], "raw_kwargs": repr(kwargs)})
        return True

    monkeypatch.setattr(Token, "has_valid_credentials", mock_has_valid_credentials)
    monkeypatch.setattr(PostcardCreatorBase, "has_free_postcard", mock_has_free_postcard)
    monkeypatch.setattr(PostcardCreatorBase, "send_free_card", mock_send_free_card)
    return recorded


def _recipient_to_dict(recipient: object) -> dict[str, str]:
    """Read the shim's :class:`Recipient` fields into a plain dict."""
    return {
        "prename": getattr(recipient, "prename", ""),
        "lastname": getattr(recipient, "lastname", ""),
        "street": getattr(recipient, "street", ""),
        "zipcode": getattr(recipient, "zip_code", ""),
        "place": getattr(recipient, "place", ""),
        "salutation": getattr(recipient, "salutation", ""),
    }


def _sender_to_dict(sender: object) -> dict[str, str]:
    """Read the shim's :class:`Sender` fields into a plain dict."""
    return {
        "prename": getattr(sender, "prename", ""),
        "lastname": getattr(sender, "lastname", ""),
        "street": getattr(sender, "street", ""),
        "zipcode": getattr(sender, "zip_code", ""),
        "place": getattr(sender, "place", ""),
        "country": getattr(sender, "country", ""),
    }


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    """Write a default config file and return its path."""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "recipient": {
                    "firstname": "Inline",
                    "lastname": "Recipient",
                    "street": "Inline-Strasse 1",
                    "zipcode": "8000",
                    "city": "Zurich",
                },
                "sender": {
                    "firstname": "Inline",
                    "lastname": "Sender",
                    "street": "Sender-Strasse 1",
                    "zipcode": "8000",
                    "city": "Zurich",
                },
                "accounts": [{"username": "alice", "password": "alice-pw"}],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def alice_recipient_entry() -> AddressBookEntry:
    return AddressBookEntry(
        name="alice",
        category=AddressCategory.RECIPIENT,
        address=AddressSpec(
            prename="Alice",
            lastname="Zuercher",
            street="Bahnhofstrasse 1",
            zip_code="8000",
            place="Zurich",
            salutation="Ms.",
        ),
    )


@pytest.fixture
def home_sender_entry() -> AddressBookEntry:
    return AddressBookEntry(
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


@pytest.fixture
def greeting_template() -> MessageTemplate:
    return MessageTemplate(
        name="greeting",
        body="Hi $name, greetings from Zurich",
        description="default greeting",
    )


@pytest.fixture
def picture_file(tmp_path: Path) -> Path:
    """A minimal JPEG-ish file the legacy ``_read_picture`` accepts.

    The shim treats the file as bytes without decoding it in
    ``--dry-run`` mode, so a small JPEG-like byte sequence is
    enough. ``_read_picture`` reads via ``open(..., 'rb')`` and
    returns a :class:`io.BytesIO`, so the file must simply
    exist on disk.
    """
    path = tmp_path / "pic.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-for-mock-send")
    return path


def _invoke(*args: str) -> CliResult:
    return cli_run(list(args))


# ---------------------------------------------------------------------------
# --to (recipient from address book)
# ---------------------------------------------------------------------------


def test_send_uses_address_book_recipient(
    config_file: Path,
    alice_recipient_entry: AddressBookEntry,
    mock_shim_send: list[dict],
    picture_file: Path,
) -> None:
    """``--to NAME`` resolves to the named recipient in the address book."""
    save_address_book(AddressBook(entries=(alice_recipient_entry,)))
    result = _invoke(
        "send",
        "-c",
        str(config_file),
        "-p",
        str(picture_file),
        "-m",
        "inline message",  # gets overridden by --message-template below
        "--to",
        "alice",
        "--username",
        "alice",
        "--password",
        "alice-pw",
        "--dry-run",
    )
    assert result.exit_code == 0, result.output
    assert mock_shim_send, f"send_free_card was not called; output:\n{result.output}"
    sent = mock_shim_send[0]
    assert sent["recipient"]["prename"] == "Alice"
    assert sent["recipient"]["lastname"] == "Zuercher"
    assert sent["recipient"]["street"] == "Bahnhofstrasse 1"
    assert sent["recipient"]["zipcode"] == "8000"
    assert sent["recipient"]["place"] == "Zurich"
    assert sent["recipient"]["salutation"] == "Ms."


def test_send_without_to_uses_inline_recipient(
    config_file: Path,
    mock_shim_send: list[dict],
    picture_file: Path,
) -> None:
    """When ``--to`` is omitted, the recipient comes from the config file."""
    result = _invoke(
        "send",
        "-c",
        str(config_file),
        "-p",
        str(picture_file),
        "-m",
        "Hi",
        "--username",
        "alice",
        "--password",
        "alice-pw",
        "--dry-run",
    )
    assert result.exit_code == 0, result.output
    assert mock_shim_send
    sent = mock_shim_send[0]
    assert sent["recipient"]["prename"] == "Inline"
    assert sent["recipient"]["lastname"] == "Recipient"


def test_send_to_rejects_unknown_entry(
    config_file: Path,
    mock_shim_send: list[dict],
    picture_file: Path,
) -> None:
    result = _invoke(
        "send",
        "-c",
        str(config_file),
        "-p",
        str(picture_file),
        "-m",
        "Hi",
        "--to",
        "ghost",
        "--username",
        "alice",
        "--password",
        "alice-pw",
        "--dry-run",
    )
    assert result.exit_code == 2
    assert "no address-book entry named 'ghost'" in result.output
    assert not mock_shim_send


def test_send_to_rejects_wrong_category(
    config_file: Path,
    home_sender_entry: AddressBookEntry,
    mock_shim_send: list[dict],
    picture_file: Path,
) -> None:
    """``--to`` requires a recipient entry; pointing it at a sender fails."""
    save_address_book(AddressBook(entries=(home_sender_entry,)))
    result = _invoke(
        "send",
        "-c",
        str(config_file),
        "-p",
        str(picture_file),
        "-m",
        "Hi",
        "--to",
        "home",
        "--username",
        "alice",
        "--password",
        "alice-pw",
        "--dry-run",
    )
    assert result.exit_code == 2
    assert "is a sender, not a recipient" in result.output


# ---------------------------------------------------------------------------
# --sender (sender from address book)
# ---------------------------------------------------------------------------


def test_send_uses_address_book_sender(
    config_file: Path,
    home_sender_entry: AddressBookEntry,
    mock_shim_send: list[dict],
    picture_file: Path,
) -> None:
    save_address_book(AddressBook(entries=(home_sender_entry,)))
    result = _invoke(
        "send",
        "-c",
        str(config_file),
        "-p",
        str(picture_file),
        "-m",
        "Hi",
        "--sender",
        "home",
        "--username",
        "alice",
        "--password",
        "alice-pw",
        "--dry-run",
    )
    assert result.exit_code == 0, result.output
    sent = mock_shim_send[0]
    assert sent["sender"]["prename"] == "Andrin"
    assert sent["sender"]["lastname"] == "Bertschi"
    assert sent["sender"]["street"] == "Lagerstrasse 1"
    assert sent["sender"]["country"] == "CH"


def test_send_sender_rejects_wrong_category(
    config_file: Path,
    alice_recipient_entry: AddressBookEntry,
    mock_shim_send: list[dict],
    picture_file: Path,
) -> None:
    save_address_book(AddressBook(entries=(alice_recipient_entry,)))
    result = _invoke(
        "send",
        "-c",
        str(config_file),
        "-p",
        str(picture_file),
        "-m",
        "Hi",
        "--sender",
        "alice",
        "--username",
        "alice",
        "--password",
        "alice-pw",
        "--dry-run",
    )
    assert result.exit_code == 2
    assert "is a recipient, not a sender" in result.output


# ---------------------------------------------------------------------------
# --message-template
# ---------------------------------------------------------------------------


def test_send_renders_template(
    config_file: Path,
    greeting_template: MessageTemplate,
    mock_shim_send: list[dict],
    picture_file: Path,
) -> None:
    """``--message-template NAME`` substitutes ``--var`` into the body."""
    save_template_book(TemplateBook(templates=(greeting_template,)))
    result = _invoke(
        "send",
        "-c",
        str(config_file),
        "-p",
        str(picture_file),
        "--message-template",
        "greeting",
        "--var",
        "name=Alice",
        "--username",
        "alice",
        "--password",
        "alice-pw",
        "--dry-run",
    )
    assert result.exit_code == 0, result.output
    sent = mock_shim_send[0]
    assert sent["message"] == "Hi Alice, greetings from Zurich"


def test_send_message_and_template_are_mutually_exclusive(
    config_file: Path,
    greeting_template: MessageTemplate,
    mock_shim_send: list[dict],
    picture_file: Path,
) -> None:
    save_template_book(TemplateBook(templates=(greeting_template,)))
    result = _invoke(
        "send",
        "-c",
        str(config_file),
        "-p",
        str(picture_file),
        "-m",
        "Inline",
        "--message-template",
        "greeting",
        "--username",
        "alice",
        "--password",
        "alice-pw",
        "--dry-run",
    )
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_send_template_undefined_variable_fails(
    config_file: Path,
    greeting_template: MessageTemplate,
    mock_shim_send: list[dict],
    picture_file: Path,
) -> None:
    save_template_book(TemplateBook(templates=(greeting_template,)))
    result = _invoke(
        "send",
        "-c",
        str(config_file),
        "-p",
        str(picture_file),
        "--message-template",
        "greeting",
        "--username",
        "alice",
        "--password",
        "alice-pw",
        "--dry-run",
    )
    assert result.exit_code == 2
    assert "undefined variable" in result.output
    assert not mock_shim_send


def test_send_var_without_template_is_rejected(
    config_file: Path,
    mock_shim_send: list[dict],
    picture_file: Path,
) -> None:
    result = _invoke(
        "send",
        "-c",
        str(config_file),
        "-p",
        str(picture_file),
        "-m",
        "Hi",
        "--var",
        "name=Alice",
        "--username",
        "alice",
        "--password",
        "alice-pw",
        "--dry-run",
    )
    assert result.exit_code == 2
    assert "without --message-template" in result.output


def test_send_template_unknown_name_is_rejected(
    config_file: Path,
    mock_shim_send: list[dict],
    picture_file: Path,
) -> None:
    result = _invoke(
        "send",
        "-c",
        str(config_file),
        "-p",
        str(picture_file),
        "--message-template",
        "ghost",
        "--username",
        "alice",
        "--password",
        "alice-pw",
        "--dry-run",
    )
    assert result.exit_code == 2
    assert "no template named 'ghost'" in result.output


# ---------------------------------------------------------------------------
# Combined --to / --sender / --message-template
# ---------------------------------------------------------------------------


def test_send_full_address_book_plus_template_flow(
    config_file: Path,
    alice_recipient_entry: AddressBookEntry,
    home_sender_entry: AddressBookEntry,
    greeting_template: MessageTemplate,
    mock_shim_send: list[dict],
    picture_file: Path,
) -> None:
    """End-to-end: --to + --sender + --message-template + --var all together.

    This is the headline M4 integration scenario — a single
    CLI invocation resolves the recipient and sender from the
    address book, renders a template with substituted
    variables, and hands the resolved ``Postcard`` to the
    mocked backend.
    """
    save_address_book(AddressBook(entries=(alice_recipient_entry, home_sender_entry)))
    save_template_book(TemplateBook(templates=(greeting_template,)))
    result = _invoke(
        "send",
        "-c",
        str(config_file),
        "-p",
        str(picture_file),
        "--to",
        "alice",
        "--sender",
        "home",
        "--message-template",
        "greeting",
        "--var",
        "name=Alice",
        "--username",
        "alice",
        "--password",
        "alice-pw",
        "--dry-run",
    )
    assert result.exit_code == 0, result.output
    assert mock_shim_send, f"send_free_card was not called; output:\n{result.output}"
    sent = mock_shim_send[0]
    assert sent["recipient"]["prename"] == "Alice"
    assert sent["recipient"]["lastname"] == "Zuercher"
    assert sent["sender"]["prename"] == "Andrin"
    assert sent["sender"]["country"] == "CH"
    assert sent["message"] == "Hi Alice, greetings from Zurich"


# ---------------------------------------------------------------------------
# Backward compatibility — existing inline options still work
# ---------------------------------------------------------------------------


def test_send_inline_message_still_works(
    config_file: Path,
    mock_shim_send: list[dict],
    picture_file: Path,
) -> None:
    """Pure inline flow (``-m``, no address-book or template) is unchanged."""
    result = _invoke(
        "send",
        "-c",
        str(config_file),
        "-p",
        str(picture_file),
        "-m",
        "Plain inline message",
        "--username",
        "alice",
        "--password",
        "alice-pw",
        "--dry-run",
    )
    assert result.exit_code == 0, result.output
    sent = mock_shim_send[0]
    assert sent["message"] == "Plain inline message"
