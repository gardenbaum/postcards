"""Tests for the network-free web-app service layer.

These cover the logic behind the WYSIWYG app — draft → postcard,
image processing, live preview bytes, validation, and sending via a
``MockBackend`` — without importing NiceGUI or touching the network.
The UI layer itself is smoke-tested separately in
:mod:`tests.test_web_app`.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from postcards.backend import MockBackend
from postcards.backend.base import AddressSpec, QuotaInfo
from postcards.backend.exceptions import AuthenticationError
from postcards.config import KEYRING_SERVICE, KeyringError, KeyringStore
from postcards.image import A6_LANDSCAPE_HEIGHT, A6_LANDSCAPE_WIDTH, ImageError
from postcards.models.message import MAX_MESSAGE_LENGTH
from postcards.web import service
from postcards.web.service import PostcardDraft


class _FakeKeyring:
    """Minimal in-memory keyring backend matching the keyring protocol."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.store.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.store[(service_name, username)] = password

    def delete_password(self, service_name: str, username: str) -> None:
        self.store.pop((service_name, username), None)


def _valid_address(prename: str = "Erika", lastname: str = "Musterfrau") -> AddressSpec:
    return AddressSpec(
        prename=prename,
        lastname=lastname,
        street="Hauptstrasse 42",
        zip_code="8001",
        place="Zürich",
    )


def _jpeg_bytes(width: int = 400, height: int = 300, color: str = "blue") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=color).save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def _valid_draft(*, picture: bytes | None = None, message: str = "Hello!") -> PostcardDraft:
    return PostcardDraft(
        recipient=_valid_address(),
        sender=_valid_address(prename="Fabian", lastname="Baumgartner"),
        message=message,
        picture=picture,
    )


# ---------------------------------------------------------------------------
# Draft
# ---------------------------------------------------------------------------


def test_empty_draft_has_full_message_budget() -> None:
    draft = PostcardDraft()
    assert draft.message_remaining() == MAX_MESSAGE_LENGTH
    assert draft.picture is None


def test_message_remaining_goes_negative_when_over_limit() -> None:
    draft = PostcardDraft(message="x" * (MAX_MESSAGE_LENGTH + 5))
    assert draft.message_remaining() == -5


# ---------------------------------------------------------------------------
# process_image
# ---------------------------------------------------------------------------


def test_process_image_returns_a6_jpeg() -> None:
    processed = service.process_image(_jpeg_bytes())
    with Image.open(io.BytesIO(processed)) as image:
        image.load()
        assert image.format == "JPEG"
        # The pipeline resizes to one of the A6 orientations.
        assert image.size in {
            (A6_LANDSCAPE_WIDTH, A6_LANDSCAPE_HEIGHT),
            (A6_LANDSCAPE_HEIGHT, A6_LANDSCAPE_WIDTH),
        }


def test_process_image_rejects_garbage() -> None:
    with pytest.raises(ImageError):
        service.process_image(b"definitely not an image")


# ---------------------------------------------------------------------------
# build_postcard / render_preview
# ---------------------------------------------------------------------------


def test_build_postcard_carries_message_and_picture() -> None:
    picture = service.process_image(_jpeg_bytes())
    card = service.build_postcard(_valid_draft(picture=picture, message="Hi from Bern"))
    assert card.message.text == "Hi from Bern"
    assert card.picture == picture
    assert card.is_valid()


def test_build_postcard_rejects_overlong_message() -> None:
    with pytest.raises(ValueError, match="character"):
        service.build_postcard(_valid_draft(message="x" * (MAX_MESSAGE_LENGTH + 1)))


@pytest.mark.parametrize("side", ["front", "back"])
def test_render_preview_returns_png_bytes(side: str) -> None:
    png = service.render_preview(
        _valid_draft(picture=service.process_image(_jpeg_bytes())), side=side
    )
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


def test_render_preview_works_without_picture() -> None:
    # Text-only card still renders a (placeholder) front.
    png = service.render_preview(_valid_draft(picture=None), side="front")
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


# ---------------------------------------------------------------------------
# validate_draft
# ---------------------------------------------------------------------------


def test_validate_draft_flags_empty_draft() -> None:
    problems = service.validate_draft(PostcardDraft())
    assert any("Recipient" in p for p in problems)
    assert any("Sender" in p for p in problems)
    assert any("picture or a message" in p for p in problems)


def test_validate_draft_accepts_complete_draft() -> None:
    assert service.validate_draft(_valid_draft()) == []


def test_validate_draft_flags_overlong_message() -> None:
    problems = service.validate_draft(_valid_draft(message="x" * (MAX_MESSAGE_LENGTH + 1)))
    assert any("too long" in p for p in problems)


# ---------------------------------------------------------------------------
# send_draft
# ---------------------------------------------------------------------------


def test_send_draft_dry_run_records_mock_send() -> None:
    backend = MockBackend()
    outcome = service.send_draft(_valid_draft(), backend=backend, dry_run=True)
    assert outcome.ok is True
    assert outcome.dry_run is True
    assert "NOT sent" in outcome.message
    assert len(backend.sent) == 1
    assert backend.sent[0].mock is True


def test_send_draft_real_send_sets_mock_false() -> None:
    backend = MockBackend()
    outcome = service.send_draft(_valid_draft(), backend=backend, dry_run=False)
    assert outcome.ok is True
    assert outcome.message == "Postcard sent."
    assert backend.sent[0].mock is False


def test_send_draft_invalid_draft_does_not_send() -> None:
    backend = MockBackend()
    outcome = service.send_draft(PostcardDraft(), backend=backend, dry_run=True)
    assert outcome.ok is False
    assert backend.sent == []
    assert "incomplete" in outcome.message


def test_send_draft_logs_in_when_credentials_given() -> None:
    backend = MockBackend()
    service.send_draft(
        _valid_draft(), backend=backend, username="me@example.com", password="pw", dry_run=True
    )
    assert backend.logins == [("me@example.com", "pw")]


def test_send_draft_surfaces_backend_error() -> None:
    backend = MockBackend()
    backend.send_exception = AuthenticationError("login failed")
    outcome = service.send_draft(_valid_draft(), backend=backend, dry_run=False)
    assert outcome.ok is False
    assert "login failed" in outcome.message


# ---------------------------------------------------------------------------
# field helpers
# ---------------------------------------------------------------------------


def test_with_recipient_field_replaces_single_field() -> None:
    draft = service.with_recipient_field(PostcardDraft(), "place", "Genève")
    assert draft.recipient.place == "Genève"
    assert draft.recipient.prename == ""


def test_with_sender_field_replaces_single_field() -> None:
    draft = service.with_sender_field(PostcardDraft(), "lastname", "Tell")
    assert draft.sender.lastname == "Tell"


# ---------------------------------------------------------------------------
# auth: resolve_auth / save_to_keyring / check_login
# ---------------------------------------------------------------------------


def test_resolve_auth_reads_config_accounts(tmp_path: Path) -> None:
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"accounts": [{"username": "a@x.ch", "password": "secret"}]}))
    auth = service.resolve_auth(config_path=cfg, env={}, keyring_backend=_FakeKeyring())
    assert auth.error == ""
    assert auth.usernames() == ["a@x.ch"]
    acct = auth.find("a@x.ch")
    assert acct is not None
    assert acct.password == "secret"
    assert acct.source == "config_file"
    assert auth.config_path == str(cfg)


def test_resolve_auth_empty_when_no_accounts(tmp_path: Path) -> None:
    auth = service.resolve_auth(
        config_path=tmp_path / "missing.json", env={}, keyring_backend=_FakeKeyring()
    )
    assert auth.accounts == ()
    # "no accounts found" is an expected empty state, not a surfaced error.
    assert auth.error == ""


def test_save_to_keyring_stores_password() -> None:
    fake = _FakeKeyring()
    msg = service.save_to_keyring("u@x.ch", "pw", store=KeyringStore(fake))
    assert "Saved" in msg
    assert fake.store[(KEYRING_SERVICE, "u@x.ch")] == "pw"


def test_save_to_keyring_requires_both_fields() -> None:
    with pytest.raises(KeyringError):
        service.save_to_keyring("", "pw", store=KeyringStore(_FakeKeyring()))


def test_check_login_mock_succeeds() -> None:
    result = service.check_login(MockBackend(), "u@x.ch", "pw")
    assert result.ok is True
    assert result.quota_available is True


def test_check_login_requires_credentials() -> None:
    result = service.check_login(MockBackend(), "", "")
    assert result.ok is False
    assert "Enter" in result.detail


def test_check_login_reports_quota_used() -> None:
    backend = MockBackend(quota_info=QuotaInfo(available=False))
    result = service.check_login(backend, "u@x.ch", "pw")
    assert result.ok is True
    assert result.quota_available is False
    assert "quota already used" in result.detail


def test_check_login_surfaces_auth_failure() -> None:
    backend = MockBackend(should_fail_login=True, login_error=AuthenticationError("bad creds"))
    result = service.check_login(backend, "u@x.ch", "pw")
    assert result.ok is False
    assert "bad creds" in result.detail


# ---------------------------------------------------------------------------
# browser-assisted login (SwissID 2FA)
# ---------------------------------------------------------------------------


class _TokenExchangeResponse:
    status_code = 200

    def json(self) -> dict[str, object]:
        return {"access_token": "ACCESS", "token_type": "Bearer", "expires_in": 3600}


class _TokenExchangeSession:
    """Fake session whose token endpoint returns a valid access token."""

    def post(self, url: str, **kwargs: object) -> _TokenExchangeResponse:
        return _TokenExchangeResponse()


def test_begin_browser_login_returns_url_and_verifier() -> None:
    url, verifier = service.begin_browser_login()
    assert url.startswith("https://pccweb.api.post.ch/OAuth/authorization?")
    assert "code_challenge=" in url
    assert verifier


def test_complete_browser_login_returns_authenticated_backend() -> None:
    backend = service.complete_browser_login(
        "ch.post.pcc://auth/x?code=ABC123", "verifier", session=_TokenExchangeSession()
    )
    # The returned backend is authenticated and can dry-run a send offline.
    outcome = service.send_draft(_valid_draft(), backend=backend, dry_run=True)
    assert outcome.ok is True
    assert outcome.backend == "swissid"
