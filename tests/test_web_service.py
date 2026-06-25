"""Tests for the network-free web-app service layer.

These cover the logic behind the WYSIWYG app — draft → postcard,
image processing, live preview bytes, validation, and sending via a
``MockBackend`` — without importing NiceGUI or touching the network.
The UI layer itself is smoke-tested separately in
:mod:`tests.test_web_app`.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from postcards.backend import MockBackend
from postcards.backend.base import AddressSpec
from postcards.backend.exceptions import AuthenticationError
from postcards.image import A6_LANDSCAPE_HEIGHT, A6_LANDSCAPE_WIDTH, ImageError
from postcards.models.message import MAX_MESSAGE_LENGTH
from postcards.web import service
from postcards.web.service import PostcardDraft


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
