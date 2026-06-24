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

import logging
from typing import Any, BinaryIO

LOGGING_TRACE_LVL = 5
logger = logging.getLogger("postcard_creator")
logging.addLevelName(LOGGING_TRACE_LVL, "TRACE")
setattr(logger, "trace", lambda *args: logger.log(LOGGING_TRACE_LVL, *args))


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
        return all(field for field in [self.prename, self.lastname, self.street, self.zip_code, self.place])


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
        return all(field for field in [self.prename, self.lastname, self.street, self.zip_code, self.place])


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
        return bool(self.recipient and self.recipient.is_valid() and self.sender and self.sender.is_valid())

    def validate(self) -> None:
        if self.recipient is None or not self.recipient.is_valid():
            raise PostcardCreatorException("Not all required attributes in recipient set")
        if self.sender is None or not self.sender.is_valid():
            raise PostcardCreatorException("Not all required attributes in sender set")


class PostcardCreatorBase:
    """Base implementation stub.

    The shim raises ``NotImplementedError`` from every network method so
    accidental live calls are caught immediately. Integration tests
    monkey-patch these methods on a ``PostcardCreator`` instance to
    drive the send flow against a mock backend.
    """

    def has_free_postcard(self) -> bool:
        raise NotImplementedError(
            "postcards._vendor.postcard_creator is a shim; "
            "PostcardCreator.has_free_postcard must be mocked in tests."
        )

    def send_free_card(self, postcard: Postcard, mock_send: bool = False, **kwargs: Any) -> None:
        raise NotImplementedError(
            "postcards._vendor.postcard_creator is a shim; "
            "PostcardCreator.send_free_card must be mocked in tests."
        )

    def get_quota(self) -> dict[str, Any]:
        """Quota shape matches the upstream contract.

        Format (per upstream docstring):
            {'quota': -1, 'retentionDays': 1, 'available': False, 'next': '<iso8601>'}
        """
        raise NotImplementedError(
            "postcards._vendor.postcard_creator is a shim; "
            "PostcardCreator.get_quota must be mocked in tests."
        )


class PostcardCreator:
    """Client-side proxy — the upstream class forwards to a backend implementation.

    The upstream constructor sets ``self.impl`` based on the token's
    declared ``token_implementation`` ('legacy' vs 'swissid'). The
    shim sets ``self.impl`` to a single ``PostcardCreatorBase`` stub
    so attribute forwarding (the ``__getattr__`` shim below) still
    raises ``NotImplementedError`` for every network method.

    The shim still validates ``token.token is not None`` at
    construction time so ``postcards.postcards._create_pcc_wrappers``
    sees the same error if a Token has no auth — matches upstream.
    """

    def __init__(self, token: "Token | None" = None) -> None:
        if token is None or getattr(token, "token", None) is None:
            raise PostcardCreatorException("No Token given")
        self.token = token
        self.impl = PostcardCreatorBase()

    def __getattr__(self, method_name: str) -> Any:
        # __getattr__ is only called when the attribute was not found
        # through normal lookup; this is the upstream's proxy pattern.
        def method(*args: Any, **kwargs: Any) -> Any:
            logger.debug(
                "Forwarding method to shim implementation: '{}'".format(method_name),
            )
            return getattr(self.impl, method_name)(*args, **kwargs)

        return method


# ``Token`` lives in postcards._vendor.postcard_creator.token to keep
# the file layout close to upstream's (token.py is a separate module).
# Imported here at the bottom to avoid a circular import (token.py
# imports PostcardCreatorException from this module).
from postcards._vendor.postcard_creator.token import Token  # noqa: E402