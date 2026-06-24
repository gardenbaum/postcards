"""``PostcardBackend`` protocol + typed payloads exchanged with backends.

This module is the contract every Swiss Post network call MUST go
through. See ``docs/CONSTITUTION.md`` §1.1:

    All Swiss Post network calls live behind a ``Backend`` interface.
    Every code path that calls the network MUST go through that
    interface, and the interface MUST have a mocked implementation
    that the integration test suite uses.

The protocol is declared as a :class:`typing.Protocol` and decorated
with :func:`typing.runtime_checkable` so that test fixtures can assert
``isinstance(backend, PostcardBackend)`` against third-party mocks
without an explicit ``register`` call.

Dataclasses
-----------

The payloads exchanged with a backend are simple frozen dataclasses so
they can be hashed, compared with ``==``, and printed in tracebacks
without surprises. They are intentionally **not** the
``postcard_creator.{Sender,Recipient,Postcard}`` data classes — those
live in the vendored shim and are tied to its specific call signature.
The backend protocol speaks its own language; the SwissID wrapper
translates to/from the shim's classes inside :meth:`SwissIdConsumerBackend.send`.

Quota shape
-----------

The upstream ``postcard_creator`` quota response is::

    {"quota": -1, "retentionDays": 1, "available": False, "next": "<iso8601>"}

:func:`QuotaInfo.from_dict` accepts that shape so the shim's
``PostcardCreator.get_quota()`` response can be forwarded without a
mapping layer; new backends that return a different shape provide
their own construction path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, BinaryIO, Protocol, runtime_checkable


@dataclass(frozen=True)
class AddressSpec:
    """A postal address — sender or recipient.

    The field names match what the Swiss Post consumer API accepts.
    ``company`` / ``country`` / ``salutation`` / ``company_addition``
    are optional; ``prename``, ``lastname``, ``street``, ``zip_code``
    and ``place`` are required for :meth:`is_valid` to return ``True``.
    """

    prename: str
    lastname: str
    street: str
    zip_code: str
    place: str
    company: str = ""
    country: str = ""
    salutation: str = ""
    company_addition: str = ""

    def is_valid(self) -> bool:
        """Return True iff all required address fields are non-empty."""
        return all(
            field for field in (self.prename, self.lastname, self.street, self.zip_code, self.place)
        )


@dataclass(frozen=True)
class PostcardSpec:
    """A postcard payload ready to be handed to a backend.

    ``picture`` is a binary file-like (``open(..., 'rb')``, ``BytesIO``,
    ``http.client.HTTPResponse``). The backend reads from it once and
    does not close it; the caller owns the lifecycle.

    A postcard is valid when both addresses are valid AND at least one
    of ``message`` / ``picture`` carries content. The Swiss Post web
    flow allows either text or image; the CLI historically always
    sends an image.
    """

    sender: AddressSpec
    recipient: AddressSpec
    message: str
    picture: BinaryIO | None = None

    def is_valid(self) -> bool:
        return (
            self.recipient.is_valid()
            and self.sender.is_valid()
            and bool(self.message or self.picture)
        )


@dataclass(frozen=True)
class QuotaInfo:
    """Quota state for the authenticated account.

    ``next_available_at`` is the timestamp at which the next free card
    becomes available, or ``None`` when the quota is currently free.
    ``retention_days`` mirrors the upstream ``retentionDays`` field
    (the number of days a sent card is retained in the web UI).
    """

    available: bool
    next_available_at: datetime | None = None
    retention_days: int = 1

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> QuotaInfo:
        """Build a :class:`QuotaInfo` from the upstream ``get_quota`` response.

        The upstream response shape is::

            {"quota": -1, "retentionDays": 1, "available": False, "next": "<iso8601>"}

        ``next`` may be an empty string when ``available`` is True; in
        that case :attr:`next_available_at` is ``None``.
        """
        available = bool(payload.get("available", False))
        next_str = payload.get("next") or ""
        next_available_at: datetime | None = None
        if next_str:
            try:
                # Python's ``fromisoformat`` accepts ``+00:00`` but not
                # the trailing ``Z``; normalize so both work.
                normalized = next_str.replace("Z", "+00:00")
                next_available_at = datetime.fromisoformat(normalized)
            except ValueError:
                # Bad timestamp from upstream — fall back to ``None`` so
                # the CLI can still display "quota available" sensibly.
                next_available_at = None
        retention_days = int(payload.get("retentionDays", 1))
        return cls(
            available=available,
            next_available_at=next_available_at,
            retention_days=retention_days,
        )


@dataclass(frozen=True)
class PreviewInfo:
    """What would happen if :attr:`postcard` were sent.

    ``estimated_send_at`` is backend-supplied (e.g. the next quota
    window). ``warnings`` is a tuple of human-readable strings the
    backend wants to surface to the user before sending.
    """

    postcard: PostcardSpec
    estimated_send_at: datetime | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SendResult:
    """The result of a successful :meth:`PostcardBackend.send` call.

    ``mock`` is True when the send was a dry-run (no network call).
    ``confirmation`` is an opaque backend-supplied tracking string
    (``None`` for mocks).
    """

    backend: str
    account: str
    sent_at: datetime
    mock: bool
    postcard: PostcardSpec
    confirmation: str | None = None

    @classmethod
    def now(
        cls,
        *,
        backend: str,
        account: str,
        mock: bool,
        postcard: PostcardSpec,
        confirmation: str | None = None,
    ) -> SendResult:
        """Construct a :class:`SendResult` stamped with the current UTC time."""
        return cls(
            backend=backend,
            account=account,
            sent_at=datetime.now(UTC),
            mock=mock,
            postcard=postcard,
            confirmation=confirmation,
        )


@runtime_checkable
class PostcardBackend(Protocol):
    """Pluggable Swiss Post backend interface.

    The runtime protocol is intentionally minimal — the four operations
    the Swiss Post consumer flow actually exposes (authenticate, query
    quota, preview a card, send a card). Backends may add methods but
    MUST implement these four.

    Implementations are not required to be thread-safe; the CLI is
    single-threaded today and the backend holds per-account state.
    """

    name: str

    def login(self, username: str, password: str) -> None:
        """Authenticate with the SwissID-style credentials.

        Implementations raise on invalid credentials. The SwissID
        wrapper raises ``NotImplementedError`` because the vendored
        shim does not implement a real SwissID login — that path
        requires the user's real credentials and the live anomaly-
        detection-protected web flow, which CI never exercises.
        """
        ...

    def quota(self) -> QuotaInfo:
        """Return quota information for the authenticated account.

        Raises :class:`RuntimeError` when called before :meth:`login`.
        """
        ...

    def preview(self, card: PostcardSpec) -> PreviewInfo:
        """Return what would happen if ``card`` were sent.

        Implementations should NOT perform any side effects; this is
        a dry-run that lets the CLI render "you are about to send X
        to Y" screens without consuming the daily quota.
        """
        ...

    def send(self, card: PostcardSpec, *, mock: bool = False) -> SendResult:
        """Send a postcard.

        ``mock=True`` requests a dry-run; implementations MUST NOT
        perform any side effects in that mode. ``mock=False`` is a
        real send — implementations that cannot reach the network
        (the vendored shim, in CI) raise ``NotImplementedError``.
        """
        ...
