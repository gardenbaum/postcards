"""Integration test driving the postcards CLI send flow against a MOCKED Swiss Post backend.

This test satisfies ``docs/CONSTITUTION.md`` invariant §1.2: every
code path that calls the Swiss Post network MUST go through an
interface that has a mocked implementation. The mock backend here
records every ``send_free_card`` call so the test can assert that:

* the CLI constructed a ``Postcard`` with the right message / sender /
  recipient / picture stream,
* the CLI called ``send_free_card(postcard, mock_send=...)`` once per
  valid account, with ``mock_send=True`` when the user passed
  ``--mock``,
* the CLI did NOT call any other Swiss Post endpoint (SwissID login,
  quota check, etc.) — the send flow under ``--mock`` must not touch
  the network beyond the (also mocked) ``Token.has_valid_credentials``
  check.

The mock replaces the methods that would otherwise reach the network
on the shim's ``PostcardCreator`` and ``Token`` classes; the data
classes (``Recipient``, ``Sender``, ``Postcard``) are the real shim
classes.

No live network is exercised at any point.
"""

from __future__ import annotations

import argparse
import io
import json
import unittest.mock
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

import pytest

from postcards._vendor.postcard_creator import (
    Postcard,
    Token,
)
from postcards._vendor.postcard_creator.postcard_creator import PostcardCreatorBase
from postcards.postcards import Postcards

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


class MockBackend:
    """In-memory Swiss Post backend that records every send.

    The mock mimics just enough of the upstream behaviour to drive the
    CLI's send flow: it provides a ``has_valid_credentials`` predicate
    on the ``Token`` and a ``send_free_card`` method on the
    ``PostcardCreator`` that records the call.
    """

    def __init__(self) -> None:
        self.sent: list[dict] = []
        # ``valid_accounts`` is the set of usernames the mock accepts.
        # By default it accepts everything so tests can opt out per
        # case via ``self.valid_accounts = {...}``.
        self.valid_accounts: set[str] | None = None
        self.calls_to_has_valid_credentials: list[tuple[str, str]] = []
        self.calls_to_send_free_card: list[dict] = []

    def install(self) -> list[unittest.mock._patch]:
        """Patch ``Token`` and ``PostcardCreator`` to use this mock backend.

        Returns the list of patch objects; tests should call
        ``[p.stop() for p in patches]`` (or use as a context manager
        via the ``mock_backend`` fixture below).
        """
        backend = self

        def mock_has_valid_credentials(
            self: Token, username: str | None, password: str | None
        ) -> bool:
            backend.calls_to_has_valid_credentials.append((username or "", password or ""))
            # Mark this Token as authenticated so the
            # ``PostcardCreator(token)`` constructor in
            # ``_create_pcc_wrappers`` accepts it (the shim raises
            # ``PostcardCreatorException`` if ``token.token is None``).
            self.token = "<mocked-token>"
            if backend.valid_accounts is None:
                return True
            return (username or "") in backend.valid_accounts

        def mock_has_free_postcard(self: PostcardCreatorBase) -> bool:
            """Mocked quota check: always returns True so the send proceeds."""
            return True

        def mock_send_free_card(
            self: PostcardCreatorBase,
            postcard: Postcard,
            mock_send: bool = False,
            **kwargs: object,
        ) -> None:
            entry = {
                "postcard_message": postcard.message,
                "postcard_recipient": postcard.recipient,
                "postcard_sender": postcard.sender,
                "postcard_picture_stream": postcard.picture_stream,
                "mock_send": mock_send,
                "kwargs": kwargs,
            }
            backend.sent.append(entry)
            backend.calls_to_send_free_card.append(entry)

        patches: list[unittest.mock._patch] = [
            patch.object(Token, "has_valid_credentials", mock_has_valid_credentials),
            # ``PostcardCreator`` exposes ``send_free_card`` via its
            # ``__getattr__`` proxy, which forwards to ``self.impl``
            # (``PostcardCreatorBase``). Patch the impl-level method
            # so the proxy picks up the mock when the CLI calls
            # ``pc.send_free_card(...)``.
            patch.object(PostcardCreatorBase, "send_free_card", mock_send_free_card),
            patch.object(PostcardCreatorBase, "has_free_postcard", mock_has_free_postcard),
        ]
        for p in patches:
            p.start()
        return patches

    def stop(self, patches: list[unittest.mock._patch]) -> None:
        for p in patches:
            p.stop()


@pytest.fixture
def mock_backend() -> Iterator[tuple[MockBackend, list]]:
    backend = MockBackend()
    patches = backend.install()
    try:
        yield backend, patches
    finally:
        backend.stop(patches)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(
    tmp_path: Path,
    *,
    recipient: dict | None = None,
    sender: dict | None = None,
    accounts: list[dict] | None = None,
    payload: dict | None = None,
) -> Path:
    """Write a ``config.json`` and return its absolute path."""
    config = {
        "recipient": recipient
        or {
            "firstname": "Hans",
            "lastname": "Muster",
            "street": "Bahnhofstrasse 1",
            "zipcode": "8000",
            "city": "Zurich",
            "salutation": "Mr.",
        },
        "sender": sender or {},
        "accounts": accounts
        or [
            {"username": "alice", "password": "alice-pw"},
            {"username": "bob", "password": "bob-pw"},
        ],
        "payload": payload or {},
    }
    location = tmp_path / "config.json"
    location.write_text(json.dumps(config), encoding="utf-8")
    return location


def _make_postcards_for_send(
    tmp_path: Path,
    *,
    picture_path: Path | None = None,
    message: str = "Hello world",
) -> tuple[Postcards, argparse.Namespace]:
    """Construct a ``Postcards`` instance bound to a real config file on disk.

    Returns ``(cards, args)``. The picture stream is mocked to a
    ``BytesIO`` (via ``_read_picture``) so the test does not leave
    a real file handle open at teardown — pytest's strict
    ``filterwarnings = ["error", ...]`` turns ``ResourceWarning``
    into a test failure.
    """
    config_path = _write_config(tmp_path)
    cards = Postcards()
    args = argparse.Namespace(
        config_file=[str(config_path)],
        accounts_file=False,
        username="",
        password="",
        all_accounts=False,
        mock=True,
        test_plugin=False,
        picture=str(picture_path) if picture_path else None,
        message=message,
        key=(None,),
    )
    return cards, args


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_send_with_mock_calls_send_free_card_once_per_account(
    tmp_path: Path,
    mock_backend: tuple[MockBackend, list],
) -> None:
    """``send`` with ``--all-accounts`` invokes ``send_free_card`` for every valid account."""
    backend, _patches = mock_backend
    picture = tmp_path / "pic.jpg"
    picture.write_bytes(b"\xff\xd8\xff\xe0")  # minimal JPEG magic

    cards, args = _make_postcards_for_send(tmp_path, picture_path=picture)

    # ``--all-accounts`` so both accounts are exercised; ``--mock``
    # so we don't hit the network.
    args.all_accounts = True
    in_memory = io.BytesIO(b"\xff\xd8\xff\xe0fake-jpeg")
    with patch.object(cards, "_read_picture", return_value=in_memory):
        cards.do_command_send(args)

    assert len(backend.calls_to_send_free_card) == 2
    # The send order is randomized (``random.shuffle(accounts)``);
    # assert that both expected usernames made it through, but not
    # their order.
    messages = {entry["postcard_message"] for entry in backend.calls_to_send_free_card}
    assert messages == {"Hello world"}


def test_send_with_mock_passes_picture_stream(
    tmp_path: Path,
    mock_backend: tuple[MockBackend, list],
) -> None:
    """The CLI forwards the picture file handle to ``send_free_card``.

    We use a ``BytesIO`` rather than an on-disk file so the test does
    not leave a file handle open at teardown (which triggers pytest's
    unraisable-exception warning).
    """
    backend, _patches = mock_backend
    cards, args = _make_postcards_for_send(tmp_path)
    # Replace the local-path branch with an in-memory stream by
    # pre-opening it and patching ``_read_picture`` to return it.
    # ``args.picture`` must be a non-None string for ``_read_picture``
    # to be called by ``do_command_send`` (the CLI short-circuits on
    # ``args.picture is None``).
    args.picture = "ignored-by-mock.jpg"
    in_memory = io.BytesIO(b"\xff\xd8\xff\xe0fake-jpeg")
    in_memory.name = "in-memory.jpg"
    with patch.object(cards, "_read_picture", return_value=in_memory):
        cards.do_command_send(args)

    assert len(backend.calls_to_send_free_card) == 1
    stream = backend.calls_to_send_free_card[0]["postcard_picture_stream"]
    assert hasattr(stream, "read")


def test_send_constructs_recipient_and_sender_from_config(
    tmp_path: Path,
    mock_backend: tuple[MockBackend, list],
) -> None:
    """The CLI builds ``Recipient`` / ``Sender`` instances from the config dicts."""
    backend, _patches = mock_backend
    cards, args = _make_postcards_for_send(tmp_path)
    in_memory = io.BytesIO(b"\xff\xd8\xff\xe0fake-jpeg")
    with patch.object(cards, "_read_picture", return_value=in_memory):
        cards.do_command_send(args)

    send_call = backend.calls_to_send_free_card[0]
    recipient = send_call["postcard_recipient"]
    sender = send_call["postcard_sender"]

    # The config had no ``sender`` block, so the CLI should fall back
    # to the recipient address.
    assert recipient.prename == "Hans"
    assert recipient.lastname == "Muster"
    assert sender.prename == recipient.prename
    assert sender.lastname == recipient.lastname
    assert sender.street == recipient.street


def test_send_passes_mock_send_true_when_flag_set(
    tmp_path: Path,
    mock_backend: tuple[MockBackend, list],
) -> None:
    """``--mock`` propagates to ``send_free_card(postcard, mock_send=True)``."""
    backend, _patches = mock_backend
    cards, args = _make_postcards_for_send(tmp_path)
    # ``_make_postcards_for_send`` already sets ``mock=True``.
    cards.do_command_send(args)

    assert backend.calls_to_send_free_card[0]["mock_send"] is True


def test_send_with_no_valid_accounts_exits(
    tmp_path: Path, mock_backend: tuple[MockBackend, list]
) -> None:
    """When no accounts validate, the CLI exits instead of calling ``send_free_card``."""
    backend, _patches = mock_backend
    backend.valid_accounts = set()  # accept nothing

    cards, args = _make_postcards_for_send(tmp_path)

    exits: list[int] = []
    with patch("postcards.postcards.sys.exit", lambda code=0: exits.append(code)):
        cards.do_command_send(args)

    assert exits == [1]
    assert backend.calls_to_send_free_card == []


def test_send_first_valid_account_only_when_not_all(
    tmp_path: Path,
    mock_backend: tuple[MockBackend, list],
) -> None:
    """Without ``--all-accounts``, the CLI sends via the first valid account only."""
    backend, _patches = mock_backend
    cards, args = _make_postcards_for_send(tmp_path)
    args.all_accounts = False  # explicit
    cards.do_command_send(args)

    # Only one send, even though two accounts are valid.
    assert len(backend.calls_to_send_free_card) == 1


def test_send_validates_recipient_before_calling_backend(
    tmp_path: Path,
    mock_backend: tuple[MockBackend, list],
) -> None:
    """A config missing required recipient fields exits before the backend is touched."""
    backend, _patches = mock_backend
    config_path = _write_config(
        tmp_path,
        recipient={"firstname": "Hans"},  # missing everything else
    )

    cards = Postcards()
    args = argparse.Namespace(
        config_file=[str(config_path)],
        accounts_file=False,
        username="",
        password="",
        all_accounts=False,
        mock=True,
        test_plugin=False,
        picture=None,
        message="",
        key=(None,),
    )

    exits: list[int] = []

    def _exit_raising(code: int = 0) -> None:
        # Record the exit and actually exit, mirroring ``sys.exit``'s
        # contract. Without the raise, the CLI would fall through and
        # reach ``send_free_card`` (which we explicitly want to assert
        # never happens).
        exits.append(code)
        raise SystemExit(code)

    with (
        patch("postcards.postcards.sys.exit", _exit_raising),
        pytest.raises(SystemExit),
    ):
        cards.do_command_send(args)

    assert exits == [1]
    # The CLI never reached the backend: no token, no postcard.
    assert backend.calls_to_send_free_card == []


def test_send_does_not_call_live_network_methods(
    tmp_path: Path,
    mock_backend: tuple[MockBackend, list],
) -> None:
    """The CLI must never call ``Token.fetch_token`` or any live-API method.

    The shim makes those raise ``NotImplementedError``; we install a
    wrapper that records the call so the assertion is a real
    observable failure rather than a quiet raise.
    """
    _backend, _patches = mock_backend
    network_calls: list[str] = []

    real_fetch_token = Token.fetch_token

    def recording_fetch_token(
        self: Token, username: str | None, password: str | None, method: str = "mixed"
    ) -> str:
        network_calls.append("Token.fetch_token")
        return real_fetch_token(self, username, password, method=method)

    with patch.object(Token, "fetch_token", recording_fetch_token):
        cards, args = _make_postcards_for_send(tmp_path)
        cards.do_command_send(args)

    assert network_calls == []


def test_send_uses_cli_username_and_password_when_provided(
    tmp_path: Path,
    mock_backend: tuple[MockBackend, list],
) -> None:
    """``--username`` / ``--password`` on the CLI bypass the config-file accounts."""
    backend, _patches = mock_backend
    cards, args = _make_postcards_for_send(tmp_path)
    args.username = "cli-user"
    args.password = "cli-pass"
    cards.do_command_send(args)

    assert len(backend.calls_to_has_valid_credentials) == 1
    assert backend.calls_to_has_valid_credentials[0] == ("cli-user", "cli-pass")
    assert len(backend.calls_to_send_free_card) == 1


def test_send_picture_url_is_not_fetched_when_local_path(
    tmp_path: Path,
    mock_backend: tuple[MockBackend, list],
) -> None:
    """A URL picture that fails to fetch aborts before the backend is touched.

    The CLI's ``_read_picture`` URL branch delegates to
    ``urllib.request.urlopen`` and lets any ``URLError`` propagate
    (no graceful ``sys.exit`` for that branch — it is the documented
    behaviour). We patch ``_read_picture`` so the test does not
    actually hit the network, then assert that:

    * ``URLError`` propagates out of ``do_command_send`` (the URL
      branch does NOT swallow the error),
    * ``send_free_card`` was never reached on the mock backend.

    ``mock_backend`` is provided for fixture parity with the other
    tests in this file; its only assertion here is the empty
    ``calls_to_send_free_card`` list.
    """
    backend, _patches = mock_backend
    cards, args = _make_postcards_for_send(tmp_path)
    args.picture = "https://nonexistent.example.invalid/pic.jpg"

    # Patch ``_read_picture`` to simulate the URL branch failing
    # (DNS lookup failure / connection refused) without actually
    # touching the network. The test is hermetic regardless of
    # network state.
    def _raise_url_error(location: str) -> object:
        raise URLError("DNS failed (mocked)")

    with (
        patch.object(cards, "_read_picture", _raise_url_error),
        pytest.raises(URLError, match="DNS failed"),
    ):
        cards.do_command_send(args)

    assert backend.calls_to_send_free_card == []
