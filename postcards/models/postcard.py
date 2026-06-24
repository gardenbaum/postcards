"""The :class:`Postcard` domain model — the user-facing postcard.

:class:`Postcard` is the highest-level model the CLI builds before
handing a card to :class:`PostcardBackend.send`. It bundles:

* :class:`Sender` (an :class:`AddressSpec`)
* :class:`Recipient` (an :class:`AddressSpec`)
* :class:`Message` (typed greeting, ≤500 chars)
* an optional ``picture`` — processed JPEG bytes ready to ship

The class is a ``frozen`` dataclass so a postcard is immutable from
the moment it is built; that matches the protocol's
:class:`PostcardSpec` semantics and means a postcard passed to
``backend.send(...)`` cannot be silently mutated mid-send.

Construction
------------

Direct construction takes already-processed JPEG ``bytes``::

    postcard = Postcard(
        sender=Sender(...),
        recipient=Recipient(...),
        message=Message.from_text("Hi from Zurich"),
        picture=jpeg_bytes,
    )

The convenience classmethod :meth:`from_image` runs the full image
pipeline (see :mod:`postcards.image`) so callers do not have to::

    postcard = Postcard.from_image(
        sender=Sender(...),
        recipient=Recipient(...),
        message=Message.from_text("Hi"),
        image_source="photo.jpg",  # path, bytes, or file-like
    )

Picture
-------

``picture`` is stored as ``bytes`` (not ``BinaryIO``) so a postcard
can be hashed, compared with ``==``, and passed across function
boundaries without worrying about file-handle lifecycle. The
backend wraps the bytes in ``io.BytesIO`` on send.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import BinaryIO

from postcards.image.dimensions import DEFAULT_JPEG_QUALITY, Orientation
from postcards.image.pipeline import ImageSource, prepare_postcard_image
from postcards.models.address import Recipient, Sender
from postcards.models.message import Message


@dataclass(frozen=True)
class Postcard:
    """A fully-built postcard ready to hand to a :class:`PostcardBackend`.

    ``picture`` is the *processed* JPEG byte stream emitted by the
    image pipeline. It is optional because the Swiss Postcard Creator
    accepts either an image or a (short) text message — see
    :meth:`is_valid` for the rule.
    """

    sender: Sender
    recipient: Recipient
    message: Message
    picture: bytes | None = None

    def is_valid(self) -> bool:
        """Return ``True`` when the postcard is sendable.

        A postcard is valid when both addresses carry the required
        fields AND at least one of ``message`` / ``picture`` has
        content. Mirrors the check that
        :meth:`postcards.backend.swissid.SwissIdConsumerBackend.send`
        performs before invoking the shim.
        """
        return (
            self.recipient.is_valid()
            and self.sender.is_valid()
            and (not self.message.is_empty() or self.picture is not None)
        )

    @classmethod
    def from_image(
        cls,
        *,
        sender: Sender,
        recipient: Recipient,
        message: Message,
        image_source: ImageSource | None = None,
        orientation: Orientation = Orientation.AUTO,
        quality: int = DEFAULT_JPEG_QUALITY,
    ) -> Postcard:
        """Build a :class:`Postcard` from a raw image source.

        Runs the full image pipeline (load → orient → validate →
        crop → resize → JPEG encode) via
        :func:`postcards.image.prepare_postcard_image` and stores
        the resulting bytes in :attr:`picture`. ``image_source`` of
        ``None`` is allowed and produces a text-only postcard.

        Raises
        ------
        postcards.image.ImageError
            When the source cannot be loaded, its format is not
            supported, or the resize/encode step fails.
        ValueError
            When ``message`` exceeds
            :data:`postcards.models.message.MAX_MESSAGE_LENGTH`.
        """
        picture: bytes | None = None
        if image_source is not None:
            picture = prepare_postcard_image(image_source, orientation=orientation, quality=quality)
        return cls(
            sender=sender,
            recipient=recipient,
            message=message,
            picture=picture,
        )

    def open_picture(self) -> BinaryIO | None:
        """Return a fresh :class:`io.BytesIO` over :attr:`picture`.

        Returns ``None`` when the postcard carries no image. The
        returned stream is rewound to position 0 so callers can
        read from the start without seeking.
        """
        if self.picture is None:
            return None
        return io.BytesIO(self.picture)


__all__ = ["Postcard"]
