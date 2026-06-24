"""Tests for the domain models — :class:`Postcard`, :class:`Message`,
:class:`Recipient`, :class:`Sender`.

The model tests are pure-Python (no I/O, no Pillow, no network).
The image pipeline that backs :meth:`Postcard.from_image` has its own
test module (``test_image_pipeline.py``); here we only exercise the
model layer.

What we cover
-------------

* :class:`Message` enforces the 500-char cap, exposes a
  ``from_text`` builder, and treats empty / whitespace-only strings
  as empty.
* :class:`Recipient` / :class:`Sender` are structural aliases of
  :class:`AddressSpec` so an ``AddressSpec`` is accepted wherever a
  ``Recipient`` / ``Sender`` is expected (the constructor argument
  types round-trip).
* :class:`Postcard.is_valid` mirrors the upstream Swiss Post rule:
  both addresses must be valid AND at least one of message / picture
  must carry content.
* :meth:`Postcard.from_image` runs the full pipeline so a built
  postcard carries a real JPEG byte stream of the expected dimensions.
* :meth:`Postcard.open_picture` returns a fresh ``BytesIO`` over
  the picture bytes; it returns ``None`` for text-only postcards.
"""

from __future__ import annotations

import io

import pytest

from postcards.backend.base import AddressSpec
from postcards.image import A6_LANDSCAPE_HEIGHT, A6_LANDSCAPE_WIDTH, Orientation
from postcards.models import MAX_MESSAGE_LENGTH, Message, Postcard, Recipient, Sender

# ---------------------------------------------------------------------------
# Address aliases (Recipient, Sender)
# ---------------------------------------------------------------------------


def test_recipient_is_an_alias_of_address_spec() -> None:
    """``Recipient`` is structurally identical to ``AddressSpec``."""
    assert Recipient is AddressSpec


def test_sender_is_an_alias_of_address_spec() -> None:
    """``Sender`` is structurally identical to ``AddressSpec``."""
    assert Sender is AddressSpec


def test_recipient_constructor_produces_valid_address() -> None:
    """A ``Recipient`` constructed with all required fields passes ``is_valid``."""
    addr = Recipient(
        prename="Hans",
        lastname="Muster",
        street="Bahnhofstrasse 1",
        zip_code="8000",
        place="Zurich",
    )
    assert addr.is_valid()


def test_sender_constructor_produces_valid_address() -> None:
    """A ``Sender`` constructed with all required fields passes ``is_valid``."""
    addr = Sender(
        prename="Maria",
        lastname="Muster",
        street="Bahnhofstrasse 2",
        zip_code="8000",
        place="Zurich",
    )
    assert addr.is_valid()


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------


def test_message_accepts_short_text() -> None:
    """A short text is stored verbatim on the message."""
    msg = Message.from_text("Hello!")
    assert msg.text == "Hello!"


def test_message_accepts_exactly_500_characters() -> None:
    """The 500-character limit is inclusive (500 is OK)."""
    text = "x" * MAX_MESSAGE_LENGTH
    msg = Message.from_text(text)
    assert len(msg.text) == MAX_MESSAGE_LENGTH


def test_message_rejects_text_over_500_characters() -> None:
    """501 characters raises :class:`ValueError` (not silently truncated)."""
    with pytest.raises(ValueError, match="500-character limit"):
        Message.from_text("x" * (MAX_MESSAGE_LENGTH + 1))


def test_message_is_empty_when_blank() -> None:
    """``is_empty()`` is True for empty or whitespace-only messages."""
    assert Message.from_text("").is_empty()
    assert Message.from_text("   ").is_empty()
    assert Message.from_text("\n\n").is_empty()


def test_message_is_not_empty_when_has_visible_text() -> None:
    """``is_empty()`` is False when the message has any non-whitespace content."""
    assert not Message.from_text("hi").is_empty()
    assert not Message.from_text("  hi  ").is_empty()


def test_message_str_returns_text() -> None:
    """``str(message)`` returns the wrapped text."""
    assert str(Message.from_text("hello")) == "hello"


def test_message_len_returns_text_length() -> None:
    """``len(message)`` returns the wrapped text length."""
    assert len(Message.from_text("hello")) == 5


def test_message_is_frozen() -> None:
    """``Message`` is immutable — assignment raises :class:`FrozenInstanceError`."""
    from dataclasses import FrozenInstanceError

    msg = Message.from_text("hi")
    with pytest.raises(FrozenInstanceError):
        msg.text = "bye"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Postcard construction
# ---------------------------------------------------------------------------


def _address() -> AddressSpec:
    return AddressSpec(
        prename="Maria",
        lastname="Muster",
        street="Bahnhofstrasse 1",
        zip_code="8000",
        place="Zurich",
    )


def _recipient() -> AddressSpec:
    return AddressSpec(
        prename="Hans",
        lastname="Muster",
        street="Bahnhofstrasse 2",
        zip_code="8000",
        place="Zurich",
    )


def test_postcard_minimal_construction_with_message_only() -> None:
    """A text-only postcard can be built without a picture."""
    card = Postcard(
        sender=_address(),
        recipient=_recipient(),
        message=Message.from_text("hello"),
    )
    assert card.picture is None
    assert card.message.text == "hello"


def test_postcard_minimal_construction_with_picture_only() -> None:
    """An image-only postcard can be built without a message."""
    card = Postcard(
        sender=_address(),
        recipient=_recipient(),
        message=Message.from_text(""),
        picture=b"\xff\xd8\xff\xe0fake-jpeg",
    )
    assert card.picture == b"\xff\xd8\xff\xe0fake-jpeg"


def test_postcard_picture_can_be_bytes() -> None:
    """``Postcard.picture`` accepts raw bytes (post-pipeline output)."""
    card = Postcard(
        sender=_address(),
        recipient=_recipient(),
        message=Message.from_text("hi"),
        picture=b"raw bytes",
    )
    assert card.picture == b"raw bytes"


def test_postcard_is_frozen() -> None:
    """``Postcard`` is immutable — the dataclass is ``frozen=True``."""
    from dataclasses import FrozenInstanceError

    card = Postcard(
        sender=_address(),
        recipient=_recipient(),
        message=Message.from_text("hi"),
    )
    with pytest.raises(FrozenInstanceError):
        card.message = Message.from_text("bye")  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Postcard.is_valid
# ---------------------------------------------------------------------------


def test_postcard_is_valid_with_message_and_addresses() -> None:
    """A text-only postcard with valid addresses is sendable."""
    card = Postcard(
        sender=_address(),
        recipient=_recipient(),
        message=Message.from_text("hi"),
    )
    assert card.is_valid()


def test_postcard_is_valid_with_picture_and_addresses() -> None:
    """An image-only postcard with valid addresses is sendable."""
    card = Postcard(
        sender=_address(),
        recipient=_recipient(),
        message=Message.from_text(""),
        picture=b"some-jpeg-bytes",
    )
    assert card.is_valid()


def test_postcard_is_valid_with_both_message_and_picture() -> None:
    """Both message and picture is fine."""
    card = Postcard(
        sender=_address(),
        recipient=_recipient(),
        message=Message.from_text("hi"),
        picture=b"some-jpeg-bytes",
    )
    assert card.is_valid()


def test_postcard_is_invalid_without_message_and_picture() -> None:
    """Neither message nor picture is invalid (Swiss Post requires one of the two)."""
    card = Postcard(
        sender=_address(),
        recipient=_recipient(),
        message=Message.from_text(""),
        picture=None,
    )
    assert not card.is_valid()


def test_postcard_is_invalid_with_blank_recipient() -> None:
    """A blank recipient invalidates the postcard."""
    card = Postcard(
        sender=_address(),
        recipient=AddressSpec(prename="", lastname="x", street="x", zip_code="x", place="x"),
        message=Message.from_text("hi"),
    )
    assert not card.is_valid()


def test_postcard_is_invalid_with_blank_sender() -> None:
    """A blank sender invalidates the postcard."""
    card = Postcard(
        sender=AddressSpec(prename="", lastname="x", street="x", zip_code="x", place="x"),
        recipient=_recipient(),
        message=Message.from_text("hi"),
    )
    assert not card.is_valid()


# ---------------------------------------------------------------------------
# Postcard.from_image (integration with the image pipeline)
# ---------------------------------------------------------------------------


def test_from_image_with_jpeg_bytes_runs_pipeline() -> None:
    """``Postcard.from_image(bytes)`` runs the full pipeline and stores the result."""
    # Generate a small JPEG in memory. Aspect 2000/1000 = 2.0, well
    # above the A6 aspect ratio of ~1.414, so the pipeline picks
    # LANDSCAPE and produces the landscape A6 dimensions.
    from PIL import Image

    source = Image.new("RGB", (2000, 1000), color="blue")
    buffer = io.BytesIO()
    source.save(buffer, format="JPEG", quality=80)
    raw = buffer.getvalue()

    card = Postcard.from_image(
        sender=_address(),
        recipient=_recipient(),
        message=Message.from_text("hi"),
        image_source=raw,
    )

    assert card.picture is not None
    assert len(card.picture) > 0
    # The picture is a real JPEG.
    assert card.picture[:3] == b"\xff\xd8\xff"
    # And it decodes back to the expected A6 dimensions.
    reloaded = Image.open(io.BytesIO(card.picture))
    assert reloaded.size == (A6_LANDSCAPE_WIDTH, A6_LANDSCAPE_HEIGHT)


def test_from_image_with_path_runs_pipeline(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """``Postcard.from_image(path)`` reads the file and runs the pipeline."""
    from PIL import Image

    # Aspect 1500/1000 = 1.5, slightly wider than A6 (~1.414).
    source_path = tmp_path / "source.jpg"
    Image.new("RGB", (1500, 1000), color="green").save(source_path, format="JPEG")

    card = Postcard.from_image(
        sender=_address(),
        recipient=_recipient(),
        message=Message.from_text("hi"),
        image_source=source_path,
    )

    assert card.picture is not None
    reloaded = Image.open(io.BytesIO(card.picture))
    assert reloaded.size == (A6_LANDSCAPE_WIDTH, A6_LANDSCAPE_HEIGHT)


def test_from_image_with_binaryio_runs_pipeline() -> None:
    """``Postcard.from_image(BinaryIO)`` reads from the stream."""
    from PIL import Image

    source = Image.new("RGB", (1800, 1200), color="red")
    buffer = io.BytesIO()
    source.save(buffer, format="JPEG", quality=80)

    card = Postcard.from_image(
        sender=_address(),
        recipient=_recipient(),
        message=Message.from_text("hi"),
        image_source=buffer,
    )

    assert card.picture is not None
    assert card.picture[:3] == b"\xff\xd8\xff"


def test_from_image_without_image_source_produces_text_only_postcard() -> None:
    """``image_source=None`` skips the pipeline; the postcard is text-only."""
    card = Postcard.from_image(
        sender=_address(),
        recipient=_recipient(),
        message=Message.from_text("hi"),
        image_source=None,
    )
    assert card.picture is None
    assert card.is_valid()


def test_from_image_propagates_invalid_format_error() -> None:
    """An unsupported format raises :class:`ImageError` from the pipeline."""
    from PIL import Image

    from postcards.image import ImageError

    gif_buffer = io.BytesIO()
    Image.new("RGB", (100, 100)).save(gif_buffer, format="GIF")

    with pytest.raises(ImageError):
        Postcard.from_image(
            sender=_address(),
            recipient=_recipient(),
            message=Message.from_text("hi"),
            image_source=gif_buffer.getvalue(),
        )


def test_from_image_respects_orientation_parameter() -> None:
    """Forced ``Orientation.PORTRAIT`` produces the portrait A6 dimensions."""
    from PIL import Image

    # A landscape source forced to portrait should still be portrait.
    source = Image.new("RGB", (2000, 1000), color="blue")
    raw = io.BytesIO()
    source.save(raw, format="JPEG", quality=80)

    card = Postcard.from_image(
        sender=_address(),
        recipient=_recipient(),
        message=Message.from_text("hi"),
        image_source=raw.getvalue(),
        orientation=Orientation.PORTRAIT,
    )

    assert card.picture is not None
    from postcards.image import A6_PORTRAIT_HEIGHT, A6_PORTRAIT_WIDTH

    reloaded = Image.open(io.BytesIO(card.picture))
    assert reloaded.size == (A6_PORTRAIT_WIDTH, A6_PORTRAIT_HEIGHT)


# ---------------------------------------------------------------------------
# Postcard.open_picture
# ---------------------------------------------------------------------------


def test_open_picture_returns_fresh_bytesio() -> None:
    """``open_picture`` returns a fresh ``BytesIO`` over the picture bytes."""
    raw = b"some-jpeg-bytes"
    card = Postcard(
        sender=_address(),
        recipient=_recipient(),
        message=Message.from_text("hi"),
        picture=raw,
    )
    stream = card.open_picture()
    assert stream is not None
    assert isinstance(stream, io.BytesIO)
    assert stream.read() == raw


def test_open_picture_returns_none_when_no_picture() -> None:
    """``open_picture`` returns ``None`` for text-only postcards."""
    card = Postcard(
        sender=_address(),
        recipient=_recipient(),
        message=Message.from_text("hi"),
    )
    assert card.open_picture() is None


def test_open_picture_each_call_returns_fresh_stream() -> None:
    """Each call returns a fresh stream (the first call doesn't consume the bytes)."""
    raw = b"some-jpeg-bytes"
    card = Postcard(
        sender=_address(),
        recipient=_recipient(),
        message=Message.from_text("hi"),
        picture=raw,
    )
    first = card.open_picture()
    second = card.open_picture()
    assert first is not None and second is not None
    assert first.read() == raw  # First stream is fresh and reads the full content.
    assert second.read() == raw  # Second stream is independent.


# ---------------------------------------------------------------------------
# Hashability / equality
# ---------------------------------------------------------------------------


def test_postcard_is_hashable() -> None:
    """A frozen ``Postcard`` is hashable (so it works as a dict key / set member)."""
    card_a = Postcard(
        sender=_address(),
        recipient=_recipient(),
        message=Message.from_text("hi"),
    )
    card_b = Postcard(
        sender=_address(),
        recipient=_recipient(),
        message=Message.from_text("hi"),
    )
    assert hash(card_a) == hash(card_b)
    assert card_a == card_b
    assert {card_a, card_b} == {card_a}
