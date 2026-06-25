"""Core shim classes for the vendored ``postcard_creator`` package.

This module is the in-tree replacement for the upstream
``postcard_creator.postcard_creator`` module. It exposes the same
public classes (``Token``, ``PostcardCreator``, ``Postcard``,
``Recipient``, ``Sender``, ``PostcardCreatorException``) and the same
constructor signatures, so the legacy ``postcards.postcards`` module
can ``from postcards._vendor.postcard_creator import postcard_creator``
and then do ``postcard_creator.Recipient(...)`` etc. without code
changes.

See ``postcards._vendor.postcard_creator.__init__`` for the rationale.
"""

from __future__ import annotations

import base64
import io
import logging
from typing import Any, BinaryIO

import requests
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("postcard_creator")

#: Swiss Post Postcard Creator mobile API base + image specs (from the
#: Android app the unofficial wrapper mirrors).
_HOST = "https://pccweb.api.post.ch/secure/api/mobile/v1"
_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 6.0.1; wv) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Version/4.0 Chrome/52.0.2743.98 Mobile Safari/537.36"
)
_FRONT_SIZE = (1819, 1311)  # front picture, JPEG
_TEXT_COVER_SIZE = (720, 744)  # message rendered as an image, JPEG
_TEXT_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
)


class PostcardCreatorException(Exception):
    """Raised by the vendored shim for live-API errors.

    Matches the upstream class name so callers' ``except`` clauses
    keep working. The shim never reaches the network, so this is only
    raised when the caller misuses the shim (e.g. constructing a
    ``PostcardCreator`` without a token, or invoking a method that the
    shim does not implement).
    """

    server_response: Any = None


class Sender:
    """Sender address — data class, no network.

    Matches the upstream ``postcard_creator.postcard_creator.Sender``
    constructor signature so ``postcards.postcards._create_sender``
    keeps working.
    """

    def __init__(
        self,
        prename: str,
        lastname: str,
        street: str,
        zip_code: str,
        place: str,
        company: str = "",
        country: str = "",
    ) -> None:
        self.prename = prename
        self.lastname = lastname
        self.street = street
        self.zip_code = zip_code
        self.place = place
        self.company = company
        self.country = country

    def is_valid(self) -> bool:
        return all(
            field for field in [self.prename, self.lastname, self.street, self.zip_code, self.place]
        )


class Recipient:
    """Recipient address — data class, no network.

    Matches the upstream ``postcard_creator.postcard_creator.Recipient``
    constructor signature so ``postcards.postcards._create_recipient``
    keeps working.
    """

    def __init__(
        self,
        prename: str,
        lastname: str,
        street: str,
        zip_code: str,
        place: str,
        company: str = "",
        company_addition: str = "",
        salutation: str = "",
    ) -> None:
        self.salutation = salutation
        self.prename = prename
        self.lastname = lastname
        self.street = street
        self.zip_code = zip_code
        self.place = place
        self.company = company
        self.company_addition = company_addition

    def is_valid(self) -> bool:
        return all(
            field for field in [self.prename, self.lastname, self.street, self.zip_code, self.place]
        )


class Postcard:
    """A postcard payload — data class, no network.

    Matches the upstream ``postcard_creator.postcard_creator.Postcard``
    signature. ``picture_stream`` is anything file-like (``open()``
    result, ``BytesIO``, an ``http.client.HTTPResponse`` from
    ``urllib.request.urlopen``).
    """

    def __init__(
        self,
        sender: Sender,
        recipient: Recipient,
        picture_stream: BinaryIO | None,
        message: str = "",
    ) -> None:
        self.recipient = recipient
        self.message = message
        self.picture_stream = picture_stream
        self.sender = sender

    def is_valid(self) -> bool:
        return bool(
            self.recipient and self.recipient.is_valid() and self.sender and self.sender.is_valid()
        )

    def validate(self) -> None:
        if self.recipient is None or not self.recipient.is_valid():
            raise PostcardCreatorException("Not all required attributes in recipient set")
        if self.sender is None or not self.sender.is_valid():
            raise PostcardCreatorException("Not all required attributes in sender set")


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Best-effort TrueType font for the text cover; never raises."""
    for candidate in _TEXT_FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _scale_front_jpeg(stream: BinaryIO | None, size: tuple[int, int] = _FRONT_SIZE) -> bytes:
    """Return the front picture as a JPEG at the PCC-required pixel size.

    ``stream`` is the processed JPEG (file-like). ``None`` (a text-only
    card) yields a plain white front so the API still accepts the card.
    """
    if stream is None:
        image = Image.new("RGB", size, (255, 255, 255))
    else:
        image = Image.open(stream)
        image.load()
        if image.mode != "RGB":
            image = image.convert("RGB")
        if image.size != size:
            image = image.resize(size, Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90, optimize=True)
    return buffer.getvalue()


def _text_cover_jpeg(message: str, size: tuple[int, int] = _TEXT_COVER_SIZE) -> bytes:
    """Render ``message`` as a JPEG text cover at the PCC-required size."""
    image = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(image)
    font = _load_font(34)
    margin, line_h = 40, 44
    max_chars = max((size[0] - 2 * margin) // 18, 1)
    y = margin
    text = message.replace("\r\n", "\n").replace("\r", "\n")
    for paragraph in text.split("\n"):
        words, line = paragraph.split(" "), ""
        for word in words:
            candidate = word if not line else f"{line} {word}"
            if len(candidate) > max_chars and line:
                draw.text((margin, y), line, fill=(24, 24, 30), font=font)
                y += line_h
                line = word
            else:
                line = candidate
        draw.text((margin, y), line, fill=(24, 24, 30), font=font)
        y += line_h
        if y > size[1] - line_h:
            break
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90, optimize=True)
    return buffer.getvalue()


def _format_sender(sender: Sender) -> dict[str, str]:
    return {
        "city": sender.place,
        "company": sender.company,
        "firstname": sender.prename,
        "lastname": sender.lastname,
        "street": sender.street,
        "zip": sender.zip_code,
    }


def _format_recipient(recipient: Recipient) -> dict[str, str]:
    return {
        "city": recipient.place,
        "company": recipient.company,
        "companyAddon": recipient.company_addition,
        "country": "SWITZERLAND",
        "firstname": recipient.prename,
        "lastname": recipient.lastname,
        "street": recipient.street,
        "title": recipient.salutation,
        "zip": recipient.zip_code,
    }


class PostcardCreatorBase:
    """Real Swiss Post Postcard Creator mobile-API client.

    Talks to ``pccweb.api.post.ch/secure/api/mobile/v1`` with the bearer
    token from :class:`Token`. A ``session`` can be injected (tests pass a
    stand-in so no live call is made). ``mock_send=True`` validates and
    builds the payload but performs **no** network request.
    """

    def __init__(self, token: Token, *, session: Any = None) -> None:
        if token is None or getattr(token, "token", None) is None:
            raise PostcardCreatorException("No Token given")
        self.token = token
        self.host = _HOST
        self._session = session if session is not None else requests.Session()

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": _USER_AGENT, "Authorization": f"Bearer {self.token.token}"}

    def _do_op(self, method: str, endpoint: str, **kwargs: Any) -> Any:
        url = self.host + endpoint
        kwargs.setdefault("headers", self._headers())
        logger.debug("%s: %s", method, url)
        response = self._session.request(method, url, **kwargs)
        if response.status_code not in (200, 201, 204):
            exc = PostcardCreatorException(
                f"error in request {method} {url}. status_code: {response.status_code}, "
                f"text: {response.text or ''}"
            )
            exc.server_response = response.text
            raise exc
        return response

    @staticmethod
    def _validate_model(endpoint: str, payload: dict[str, Any]) -> None:
        if payload.get("errors"):
            raise PostcardCreatorException(f"cannot fetch {endpoint}: {payload['errors']}")

    def get_quota(self) -> dict[str, Any]:
        """Return the quota model: ``{'quota', 'retentionDays', 'available', 'next'}``."""
        endpoint = "/user/quota"
        payload = self._do_op("get", endpoint).json()
        self._validate_model(endpoint, payload)
        model: dict[str, Any] = payload["model"]
        return model

    def has_free_postcard(self) -> bool:
        return bool(self.get_quota()["available"])

    def get_user_info(self) -> dict[str, Any]:
        endpoint = "/user/current"
        payload = self._do_op("get", endpoint).json()
        self._validate_model(endpoint, payload)
        model: dict[str, Any] = payload["model"]
        return model

    def send_free_card(
        self, postcard: Postcard, mock_send: bool = False, **kwargs: Any
    ) -> dict[str, Any] | bool:
        """Send ``postcard`` via ``POST /card/upload``.

        ``mock_send=True`` builds and validates the payload but makes no
        network call (returns ``False``) — used for dry-runs.
        """
        if not postcard:
            raise PostcardCreatorException("Postcard must be set")
        postcard.validate()

        endpoint = "/card/upload"
        payload = {
            "lang": "en",
            "paid": False,
            "recipient": _format_recipient(postcard.recipient),
            "sender": _format_sender(postcard.sender),
            "text": "",
            "textImage": base64.b64encode(_text_cover_jpeg(postcard.message)).decode("ascii"),
            "image": base64.b64encode(_scale_front_jpeg(postcard.picture_stream)).decode("ascii"),
            "stamp": None,
        }

        if mock_send:
            logger.info("mock_send=True, endpoint %s, recipient/sender validated", endpoint)
            return False

        if not self.has_free_postcard():
            raise PostcardCreatorException(
                "Limit of free postcards exceeded. Try again at "
                + str(self.get_quota().get("next"))
            )

        result = self._do_op("post", endpoint, json=payload).json()
        self._validate_model(endpoint, result)
        model: dict[str, Any] = result["model"]
        logger.info("postcard submitted, orderId %s", model.get("orderId"))
        return model


class PostcardCreator:
    """Client proxy that forwards to the mobile-API implementation.

    Validates ``token.token is not None`` at construction (matching the
    upstream contract ``postcards.postcards`` relies on), then forwards
    method calls to :class:`PostcardCreatorBase`.
    """

    def __init__(self, token: Token | None = None, *, session: Any = None) -> None:
        if token is None or getattr(token, "token", None) is None:
            raise PostcardCreatorException("No Token given")
        self.token = token
        self.impl = PostcardCreatorBase(token, session=session)

    def __getattr__(self, method_name: str) -> Any:
        # Only called when the attribute is not found normally (proxy pattern).
        def method(*args: Any, **kwargs: Any) -> Any:
            logger.debug("forwarding to mobile-API impl: %s", method_name)
            return getattr(self.impl, method_name)(*args, **kwargs)

        return method


# ``Token`` lives in postcards._vendor.postcard_creator.token to keep
# the file layout close to upstream's (token.py is a separate module).
# Imported at the bottom to avoid a circular import (token.py imports
# PostcardCreatorException from this module).
from postcards._vendor.postcard_creator.token import Token  # noqa: E402
