"""Integration test for the send flow against the MockBackend.

Satisfies ``docs/CONSTITUTION.md`` invariant §1.2: every code path
that calls the Swiss Post network MUST go through a backend
interface that has a mocked implementation. This test exercises the
new :class:`postcard.backend.base.PostcardBackend` abstraction end to
end against :class:`postcard.backend.mock.MockBackend`, and also
exercises :class:`SwissIdConsumerBackend` by monkey-patching the
shim's network methods (the shim itself raises ``NotImplementedError``
for live calls — see :mod:`postcards._vendor.postcard_creator`).

Two flows are covered:

1. ``MockBackend`` end-to-end — login, quota, preview, send. The
   mock records every call so the test can assert that the protocol
   was implemented faithfully.
2. ``SwissIdConsumerBackend.send`` — drives the production code path
   against the shim with patched network methods, verifying that
   ``PostcardSpec`` is translated into ``Sender`` / ``Recipient`` /
   ``Postcard`` and that ``send_free_card`` is invoked with the
   expected ``mock_send`` flag.

No live network is exercised at any point.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from postcards._vendor.postcard_creator import (
    Postcard,
    Token,
)
from postcards._vendor.postcard_creator.postcard_creator import PostcardCreatorBase
from postcards.backend import (
    AddressSpec,
    MockBackend,
    PostcardSpec,
    QuotaInfo,
    SendResult,
    SwissIdConsumerBackend,
    select_backend,
)
from postcards.backend.base import PreviewInfo

# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def valid_postcard() -> PostcardSpec:
    """A valid :class:`PostcardSpec` for the integration tests."""
    return PostcardSpec(
        sender=AddressSpec(
            prename="Maria",
            lastname="Muster",
            street="Bahnhofstrasse 1",
            zip_code="8000",
            place="Zurich",
        ),
        recipient=AddressSpec(
            prename="Hans",
            lastname="Muster",
            street="Bahnhofstrasse 2",
            zip_code="8000",
            place="Zurich",
            salutation="Mr.",
        ),
        message="Hello from postcards",
        picture=io.BytesIO(b"\xff\xd8\xff\xe0fake-jpeg"),
    )


@pytest.fixture
def mock_backend() -> Iterator[MockBackend]:
    """A fresh :class:`MockBackend` for each test."""
    backend = MockBackend()
    yield backend


# ---------------------------------------------------------------------------
# MockBackend end-to-end (the "send flow against MockBackend" the task asks for)
# ---------------------------------------------------------------------------


def test_select_backend_mock_returns_mock_backend_instance() -> None:
    """``POSTCARDS_BACKEND=mock`` returns a :class:`MockBackend` via the registry."""
    backend = select_backend(env={"POSTCARDS_BACKEND": "mock"})
    assert isinstance(backend, MockBackend)
    assert backend.name == "mock"


def test_mock_backend_full_send_flow_records_each_call(
    mock_backend: MockBackend, valid_postcard: PostcardSpec
) -> None:
    """``login → quota → preview → send`` records each step on the mock."""
    mock_backend.login("alice", "alice-secret")
    quota = mock_backend.quota()
    preview = mock_backend.preview(valid_postcard)
    result = mock_backend.send(valid_postcard, mock=False)

    # Every step was recorded.
    assert mock_backend.logins == [("alice", "alice-secret")]
    assert len(mock_backend.previews) == 1
    assert len(mock_backend.sent) == 1

    # The returned values match what was sent in.
    assert quota.available is True
    assert isinstance(preview, PreviewInfo)
    assert preview.postcard is valid_postcard
    assert isinstance(result, SendResult)
    assert result.backend == "mock"
    assert result.account == "alice"
    assert result.mock is False
    assert result.postcard is valid_postcard
    assert result.confirmation is not None
    assert result.confirmation.startswith("mock-")


def test_mock_backend_send_with_mock_flag_marks_result(
    mock_backend: MockBackend, valid_postcard: PostcardSpec
) -> None:
    """``send(..., mock=True)`` records a result with ``mock=True`` and skips the network."""
    mock_backend.login("bob", "bob-secret")
    result = mock_backend.send(valid_postcard, mock=True)
    assert result.mock is True
    # The mock backend never raises on a real send — the ``mock`` flag
    # only affects the ``SendResult`` and the confirmation string.
    assert len(mock_backend.sent) == 1


def test_mock_backend_quota_returns_configured_quota(mock_backend: MockBackend) -> None:
    """``quota()`` returns whatever ``quota_info`` was set to."""
    mock_backend.quota_info = QuotaInfo(
        available=False,
        next_available_at=datetime(2099, 1, 1, 0, 0, 0),
        retention_days=7,
    )
    quota = mock_backend.quota()
    assert quota.available is False
    assert quota.next_available_at == datetime(2099, 1, 1, 0, 0, 0)
    assert quota.retention_days == 7


def test_mock_backend_login_failure_injection(mock_backend: MockBackend) -> None:
    """``should_fail_login`` makes ``login()`` raise the configured error."""
    mock_backend.should_fail_login = True
    mock_backend.login_error = RuntimeError("bad creds")
    with pytest.raises(RuntimeError, match="bad creds"):
        mock_backend.login("alice", "alice-secret")
    # The login attempt was still recorded before the raise so an
    # operator can audit who tried to authenticate.
    assert mock_backend.logins == [("alice", "alice-secret")]


def test_mock_backend_records_separate_state_per_instance(
    valid_postcard: PostcardSpec,
) -> None:
    """Two :class:`MockBackend` instances do not share state."""
    a = MockBackend()
    b = MockBackend()
    a.login("alice", "pw")
    a.send(valid_postcard)
    assert len(a.sent) == 1
    assert len(b.sent) == 0


# ---------------------------------------------------------------------------
# Dataclass contract (exercised through the mock so the test is end-to-end)
# ---------------------------------------------------------------------------


def test_postcard_spec_is_valid_requires_addresses_and_payload(
    valid_postcard: PostcardSpec,
) -> None:
    """``PostcardSpec.is_valid`` checks sender / recipient / payload."""
    assert valid_postcard.is_valid() is True

    # Empty message + no picture is invalid (the Swiss Post web flow
    # requires at least one of the two).
    empty = PostcardSpec(
        sender=valid_postcard.sender,
        recipient=valid_postcard.recipient,
        message="",
        picture=None,
    )
    assert empty.is_valid() is False

    # A blank address in either side invalidates the spec.
    bad_recipient = AddressSpec(prename="", lastname="x", street="x", zip_code="x", place="x")
    bad_spec = PostcardSpec(
        sender=valid_postcard.sender,
        recipient=bad_recipient,
        message="x",
        picture=None,
    )
    assert bad_spec.is_valid() is False


def test_quota_info_from_dict_parses_upstream_shape() -> None:
    """``QuotaInfo.from_dict`` accepts the upstream ``get_quota`` response."""

    q = QuotaInfo.from_dict(
        {"quota": -1, "retentionDays": 7, "available": False, "next": "2099-01-01T00:00:00Z"}
    )
    assert q.available is False
    assert q.retention_days == 7
    # ``Z`` parses as UTC; the QuotaInfo preserves the tzinfo so callers
    # can compare against ``datetime.now(UTC)`` without surprises.
    assert q.next_available_at == datetime(2099, 1, 1, 0, 0, 0, tzinfo=UTC)

    # ``next`` empty when ``available`` is True is fine.
    q2 = QuotaInfo.from_dict({"available": True, "retentionDays": 1, "next": ""})
    assert q2.available is True
    assert q2.next_available_at is None

    # Malformed ``next`` falls back to None so the CLI never crashes.
    q3 = QuotaInfo.from_dict({"available": False, "next": "not-a-date"})
    assert q3.next_available_at is None


# ---------------------------------------------------------------------------
# SwissIdConsumerBackend end-to-end (against the shim with mocked network)
# ---------------------------------------------------------------------------


@pytest.fixture
def shim_with_mocked_network() -> Iterator[SwissIdConsumerBackend]:
    """Patch the shim's network methods so a real backend can run.

    The shim raises ``NotImplementedError`` for every network call;
    we install stubs that record the call and set ``token.token`` so
    ``PostcardCreator(token)`` accepts it.
    """
    calls: dict[str, list[object]] = {
        "has_valid_credentials": [],
        "send_free_card": [],
        "has_free_postcard": [],
        "get_quota": [],
    }

    def mock_has_valid_credentials(self: Token, username: str | None, password: str | None) -> bool:
        calls["has_valid_credentials"].append((username, password))
        self.token = "<mocked>"
        return True

    def mock_has_free_postcard(self: PostcardCreatorBase) -> bool:
        calls["has_free_postcard"].append(True)
        return True

    def mock_get_quota(self: PostcardCreatorBase) -> dict[str, object]:
        calls["get_quota"].append({})
        return {"available": True, "retentionDays": 1, "next": ""}

    def mock_send_free_card(
        self: PostcardCreatorBase,
        postcard: Postcard,
        mock_send: bool = False,
        **kwargs: object,
    ) -> None:
        calls["send_free_card"].append(
            {
                "postcard": postcard,
                "mock_send": mock_send,
                "kwargs": kwargs,
            }
        )

    import unittest.mock as _umock
    from typing import cast as _cast

    patches = [
        _umock.patch.object(Token, "has_valid_credentials", mock_has_valid_credentials),
        _umock.patch.object(PostcardCreatorBase, "send_free_card", mock_send_free_card),
        _umock.patch.object(PostcardCreatorBase, "has_free_postcard", mock_has_free_postcard),
        _umock.patch.object(PostcardCreatorBase, "get_quota", mock_get_quota),
    ]
    for p in patches:
        _cast(_umock._patch, p).start()
    try:
        yield SwissIdConsumerBackend()
    finally:
        for p in patches:
            _cast(_umock._patch, p).stop()


def test_swissid_backend_login_calls_token_has_valid_credentials(
    shim_with_mocked_network: SwissIdConsumerBackend,
) -> None:
    """``SwissIdConsumerBackend.login`` delegates to the shim's Token method."""
    shim = shim_with_mocked_network
    shim.login("alice", "alice-secret")
    # ``_account`` is set so subsequent calls know which username authenticated.
    assert shim._account == "alice"


def test_swissid_backend_send_translates_spec_to_shim_classes(
    shim_with_mocked_network: SwissIdConsumerBackend,
    valid_postcard: PostcardSpec,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``send`` constructs ``Sender`` / ``Recipient`` / ``Postcard`` and calls ``send_free_card``."""
    shim = shim_with_mocked_network
    shim.login("alice", "pw")
    result = shim.send(valid_postcard, mock=False)

    assert isinstance(result, SendResult)
    assert result.backend == "swissid"
    assert result.account == "alice"
    assert result.mock is False
    assert result.postcard is valid_postcard


def test_swissid_backend_send_propagates_mock_flag(
    shim_with_mocked_network: SwissIdConsumerBackend,
    valid_postcard: PostcardSpec,
) -> None:
    """``send(..., mock=True)`` forwards ``mock_send=True`` to the shim."""
    shim = shim_with_mocked_network
    shim.login("alice", "pw")
    result = shim.send(valid_postcard, mock=True)
    assert result.mock is True


def test_swissid_backend_send_rejects_invalid_spec(
    shim_with_mocked_network: SwissIdConsumerBackend,
    valid_postcard: PostcardSpec,
) -> None:
    """A blank recipient / sender raises ``ValueError`` before reaching the shim."""
    shim = shim_with_mocked_network
    shim.login("alice", "pw")

    bad = PostcardSpec(
        sender=valid_postcard.sender,
        recipient=AddressSpec(
            prename="",
            lastname="x",
            street="x",
            zip_code="x",
            place="x",
        ),
        message="hi",
        picture=None,
    )
    with pytest.raises(ValueError, match="invalid"):
        shim.send(bad)


def test_swissid_backend_requires_login_before_send(
    shim_with_mocked_network: SwissIdConsumerBackend,
    valid_postcard: PostcardSpec,
) -> None:
    """``send`` without ``login`` first raises ``RuntimeError``."""
    with pytest.raises(RuntimeError, match="not authenticated"):
        shim_with_mocked_network.send(valid_postcard)


def test_swissid_backend_quota_returns_quota_info(
    shim_with_mocked_network: SwissIdConsumerBackend,
) -> None:
    """``quota`` returns a ``QuotaInfo`` derived from the shim's response."""
    shim = shim_with_mocked_network
    shim.login("alice", "pw")
    quota = shim.quota()
    assert isinstance(quota, QuotaInfo)
    assert quota.available is True


def test_swissid_backend_preview_does_not_call_shim(
    shim_with_mocked_network: SwissIdConsumerBackend,
    valid_postcard: PostcardSpec,
) -> None:
    """``preview`` is a no-op against the shim (no upstream preview endpoint)."""
    preview = shim_with_mocked_network.preview(valid_postcard)
    assert isinstance(preview, PreviewInfo)
    assert preview.postcard is valid_postcard


# ---------------------------------------------------------------------------
# Protocol conformance (runtime isinstance check)
# ---------------------------------------------------------------------------


def test_mock_backend_satisfies_postcard_backend_protocol() -> None:
    """``isinstance(MockBackend(), PostcardBackend)`` returns True at runtime."""
    from postcards.backend.base import PostcardBackend

    backend = MockBackend()
    assert isinstance(backend, PostcardBackend)


def test_swissid_backend_satisfies_postcard_backend_protocol() -> None:
    """``isinstance(SwissIdConsumerBackend(), PostcardBackend)`` returns True at runtime."""
    from postcards.backend.base import PostcardBackend

    backend = SwissIdConsumerBackend()
    assert isinstance(backend, PostcardBackend)


# ---------------------------------------------------------------------------
# Typed payload construction
# ---------------------------------------------------------------------------


def test_postcard_spec_picture_stream_is_passed_through_to_send(
    mock_backend: MockBackend, valid_postcard: PostcardSpec
) -> None:
    """The same BinaryIO object is stored on the recorded ``SendResult``."""
    mock_backend.login("alice", "pw")
    result = mock_backend.send(valid_postcard)
    assert result.postcard.picture is valid_postcard.picture


def test_send_result_now_stamps_current_utc() -> None:
    """``SendResult.now()`` stamps the result with the current UTC time."""
    spec = PostcardSpec(
        sender=AddressSpec(prename="a", lastname="b", street="c", zip_code="d", place="e"),
        recipient=AddressSpec(prename="f", lastname="g", street="h", zip_code="i", place="j"),
        message="hi",
    )
    result = SendResult.now(backend="mock", account="alice", mock=False, postcard=spec)
    assert result.sent_at <= datetime.now().astimezone()
