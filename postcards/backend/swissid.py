"""Production :class:`SwissIdConsumerBackend` implementation.

Wraps the vendored ``postcard_creator`` shim (the in-tree replacement
for the upstream ``postcard-creator==2.2`` PyPI package) and exposes
its operations through the :class:`PostcardBackend` protocol.

Authentication
--------------

The shim's ``Token.has_valid_credentials`` raises
``NotImplementedError`` because the upstream SwissID login requires
the user's real credentials and the live anomaly-detection-protected
web flow with 2FA — see ``docs/CONSTITUTION.md`` §1. This backend
propagates that error, so any test that exercises ``login()`` MUST
monkey-patch the shim's ``has_valid_credentials`` first.

Send
----

:meth:`send` translates the protocol-level :class:`PostcardSpec` into
the shim's ``Sender`` / ``Recipient`` / ``Postcard`` types and calls
``PostcardCreator.send_free_card``. The vendored shim's impl raises
``NotImplementedError`` for non-mock sends; tests that exercise the
send flow patch ``PostcardCreatorBase.send_free_card``.

Quota
-----

:meth:`quota` returns a :class:`QuotaInfo` built from the shim's
``get_quota`` dict via :meth:`QuotaInfo.from_dict`. When the shim
reports ``available=True``, ``next_available_at`` is ``None``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from postcards.backend.base import (
    PostcardBackend,
    PostcardSpec,
    PreviewInfo,
    QuotaInfo,
    SendResult,
)

if TYPE_CHECKING:
    # Imported only for type checking; the runtime imports live inside
    # the methods so importing this module never pulls the shim in.
    from postcards._vendor.postcard_creator.postcard_creator import Token  # noqa: F401


class SwissIdConsumerBackend:
    """Backend that authenticates with SwissID and sends via the shim.

    The backend is intentionally thin — it owns the :class:`Token`
    instance for the lifetime of one CLI invocation and translates
    between the protocol's dataclasses and the shim's data classes.
    The shim is the actual API client.
    """

    name: str = "swissid"

    def __init__(self) -> None:
        # ``self._token`` is set by :meth:`login`. The backend is
        # not usable until that has happened.
        self._token: object | None = None
        self._account: str = ""

    # ------------------------------------------------------------------
    # PostcardBackend protocol implementation
    # ------------------------------------------------------------------

    def login(self, username: str, password: str) -> None:
        """Authenticate via SwissID.

        The vendored shim raises ``NotImplementedError`` from
        ``Token.has_valid_credentials``; we let that propagate so a
        test that fails to mock the shim fails loudly instead of
        silently going to the network. Production callers (the user's
        interactive CLI) MUST run against a real ``postcard_creator``
        install, not the shim, so this branch never fires there.
        """
        from postcards._vendor.postcard_creator import Token

        token = Token()
        token.has_valid_credentials(username, password)
        self._token = token
        self._account = username

    def quota(self) -> QuotaInfo:
        """Return the quota for the authenticated account.

        Translates the shim's ``get_quota`` dict via
        :meth:`QuotaInfo.from_dict`. The shim raises
        ``NotImplementedError`` for live calls; integration tests
        patch ``PostcardCreatorBase.get_quota`` to drive this path.
        """
        self._require_authenticated()
        from postcards._vendor.postcard_creator import PostcardCreator

        pcc = PostcardCreator(self._token)  # type: ignore[arg-type]
        if pcc.has_free_postcard():
            return QuotaInfo(available=True, retention_days=1)
        return QuotaInfo.from_dict(pcc.get_quota())

    def preview(self, card: PostcardSpec) -> PreviewInfo:
        """Return a default preview.

        The upstream consumer flow has no preview endpoint — the
        dry-run happens via ``send_free_card(postcard, mock_send=True)``.
        :meth:`send` honours the ``mock`` flag, so ``preview`` is a
        no-op that records what the user intends to send.
        """
        return PreviewInfo(postcard=card)

    def send(self, card: PostcardSpec, *, mock: bool = False) -> SendResult:
        """Send a postcard via the shim.

        Translates :class:`PostcardSpec` → ``Sender`` / ``Recipient``
        / ``Postcard`` and calls ``PostcardCreator.send_free_card``.
        ``mock`` maps directly to the shim's ``mock_send`` argument.
        """
        self._require_authenticated()
        if not card.is_valid():
            raise ValueError("PostcardSpec is invalid: sender or recipient missing required fields")

        from postcards._vendor.postcard_creator import (
            Postcard,
            PostcardCreator,
            Recipient,
            Sender,
        )

        recipient = Recipient(
            prename=card.recipient.prename,
            lastname=card.recipient.lastname,
            street=card.recipient.street,
            zip_code=card.recipient.zip_code,
            place=card.recipient.place,
            company=card.recipient.company,
            company_addition=card.recipient.company_addition,
            salutation=card.recipient.salutation,
        )
        sender = Sender(
            prename=card.sender.prename,
            lastname=card.sender.lastname,
            street=card.sender.street,
            zip_code=card.sender.zip_code,
            place=card.sender.place,
            company=card.sender.company,
            country=card.sender.country,
        )
        pc_card = Postcard(
            sender=sender,
            recipient=recipient,
            picture_stream=card.picture,
            message=card.message,
        )

        pcc = PostcardCreator(self._token)  # type: ignore[arg-type]
        pcc.send_free_card(postcard=pc_card, mock_send=mock)

        from datetime import UTC, datetime

        return SendResult(
            backend=self.name,
            account=self._account,
            sent_at=datetime.now(UTC),
            mock=mock,
            postcard=card,
            confirmation=None,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _require_authenticated(self) -> None:
        """Raise ``RuntimeError`` when :meth:`login` has not succeeded."""
        if self._token is None:
            raise RuntimeError("SwissIdConsumerBackend is not authenticated; call login() first")


# Static type-checker assertion: ``SwissIdConsumerBackend`` satisfies
# the ``PostcardBackend`` protocol. Discarded assignment.
_: PostcardBackend = SwissIdConsumerBackend()


__all__ = ["SwissIdConsumerBackend"]
