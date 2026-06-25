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

:meth:`send` translates the user-facing :class:`postcards.models.Postcard`
into the shim's ``Sender`` / ``Recipient`` / ``Postcard`` types and
calls ``PostcardCreator.send_free_card``. The picture bytes on the
``Postcard`` are wrapped in a fresh ``io.BytesIO`` because the shim
takes a file-like object.

Quota
-----

:meth:`quota` returns a :class:`QuotaInfo` built from the shim's
``get_quota`` dict via :meth:`QuotaInfo.from_dict`. When the shim
reports ``available=True``, ``next_available_at`` is ``None``.

M5: retries and quota classification
------------------------------------

:meth:`quota` and :meth:`send` are wrapped in
:func:`postcards.retry.with_retries` so a transient network blip
does not surface as a fatal error. The shim raises
``NotImplementedError`` from every network method (because the
vendored module is a stub — see ``postcards._vendor.postcard_creator``);
this is **not** a transient error and the classifier treats it as
non-retryable. When the user installs the real ``postcard-creator``
PyPI package, the shim is no longer used, and the retry classifier
will see real network errors (``requests.exceptions.ConnectionError``,
``Timeout``, ``HTTPError`` with status ``5xx``); those get
re-raised as :class:`postcards.backend.exceptions.TransientBackendError`
which the retry helper then re-attempts with exponential backoff.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from postcards.backend.base import (
    PostcardBackend,
    PreviewInfo,
    QuotaInfo,
    SendResult,
)
from postcards.backend.exceptions import (
    AuthenticationError,
    QuotaExhaustedError,
)
from postcards.retry import RetryPolicy, default_classifier, with_retries

if TYPE_CHECKING:
    # Imported only for type checking; the runtime imports live inside
    # the methods so importing this module never pulls the shim in.
    from postcards._vendor.postcard_creator.postcard_creator import Token  # noqa: F401
    from postcards.models.postcard import Postcard

#: Module-level logger. Routes through :mod:`postcards.log`'s
#: :func:`configure` so the retry helper's per-attempt lines
#: share the project's format.
_LOGGER = logging.getLogger("postcards.backend.swissid")

#: Retry policy for Swiss Post network calls. Four attempts
#: with exponential backoff (0.5s → 1s → 2s → 4s) and 8s
#: ceiling — same shape as the AWS "full jitter" recommendation,
#: tuned so the worst-case wall time is well under a minute.
_DEFAULT_RETRY_POLICY: RetryPolicy = RetryPolicy(
    attempts=4, base_delay=0.5, multiplier=2.0, max_delay=8.0
)


def _swissid_classifier(exc: BaseException) -> bool:
    """Retry classifier for the Swiss Post backend.

    * :class:`TransientBackendError` — retry, it's a network blip.
    * :class:`requests.exceptions.ConnectionError`,
      :class:`requests.exceptions.Timeout`,
      :class:`requests.exceptions.HTTPError` with status ``5xx``
      — classify as transient (lazy import: ``requests`` is a
      runtime dependency but we don't want to pull it at module
      import time when only the type is needed).
    * :class:`AuthenticationError`, :class:`QuotaExhaustedError`,
      ``NotImplementedError`` (shim stub), ``ValueError`` — do
      **not** retry; surface immediately.
    """
    if isinstance(exc, (AuthenticationError, QuotaExhaustedError)):
        return False
    if default_classifier(exc):
        return True
    try:
        import requests.exceptions as rex
    except ImportError:  # pragma: no cover - requests is a hard dep
        return False
    if isinstance(exc, (rex.ConnectionError, rex.Timeout)):
        return True
    if isinstance(exc, rex.HTTPError):
        # 5xx is transient; 4xx is permanent (bad request, auth, quota).
        response = getattr(exc, "response", None)
        if response is not None and 500 <= int(getattr(response, "status_code", 0)) < 600:
            return True
    return False


class SwissIdConsumerBackend:
    """Backend that authenticates with SwissID and sends via the shim.

    The backend is intentionally thin — it owns the :class:`Token`
    instance for the lifetime of one CLI invocation and translates
    between the user-facing :class:`postcards.models.Postcard` and
    the shim's data classes. The shim is the actual API client.

    M5: network calls are wrapped in :func:`postcards.retry.with_retries`
    with a 4-attempt exponential-backoff policy. The retry helper
    swallows nothing — non-transient errors (bad credentials, shim
    stub) propagate immediately.
    """

    name: str = "swissid"

    def __init__(
        self,
        *,
        retry_policy: RetryPolicy | None = None,
        classifier=_swissid_classifier,
    ) -> None:
        # ``self._token`` is set by :meth:`login`. The backend is
        # not usable until that has happened.
        self._token: object | None = None
        self._account: str = ""
        self._retry_policy = retry_policy or _DEFAULT_RETRY_POLICY
        self._classifier = classifier

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

        M5: login is not retried — wrong credentials will fail the
        same way on every attempt, and we want the user to fix the
        credentials, not spin.
        """
        from postcards._vendor.postcard_creator import Token

        try:
            token = Token()
            token.has_valid_credentials(username, password)
        except NotImplementedError:
            _LOGGER.error(
                "SwissID login is not supported by the vendored shim; "
                "install the upstream 'postcard-creator' PyPI package "
                "to authenticate against the live Swiss Post backend"
            )
            raise
        self._token = token
        self._account = username
        _LOGGER.info("authenticated as %s", username)

    def quota(self) -> QuotaInfo:
        """Return the quota for the authenticated account.

        Translates the shim's ``get_quota`` dict via
        :meth:`QuotaInfo.from_dict`. The shim raises
        ``NotImplementedError`` for live calls; integration tests
        patch ``PostcardCreatorBase.get_quota`` to drive this path.

        M5: wrapped in :func:`with_retries` — a transient 5xx from
        the upstream triggers the backoff loop. ``NotImplementedError``
        is **not** retryable (the classifier knows the shim raises it
        for non-network reasons).
        """
        self._require_authenticated()

        def _call() -> QuotaInfo:
            from postcards._vendor.postcard_creator import PostcardCreator

            pcc = PostcardCreator(self._token)  # type: ignore[arg-type]
            if pcc.has_free_postcard():
                return QuotaInfo(available=True, retention_days=1)
            raw = pcc.get_quota()
            return QuotaInfo.from_dict(raw)

        outcome = with_retries(
            _call,
            policy=self._retry_policy,
            classifier=self._classifier,
            logger=_LOGGER,
            description="quota fetch",
        )
        assert isinstance(outcome.result, QuotaInfo)
        return outcome.result

    def preview(self, card: Postcard) -> PreviewInfo:
        """Return a default preview.

        The upstream consumer flow has no preview endpoint — the
        dry-run happens via ``send_free_card(postcard, mock_send=True)``.
        :meth:`send` honours the ``mock`` flag, so ``preview`` is a
        no-op that records what the user intends to send.
        """
        return PreviewInfo(postcard=card)

    def send(self, card: Postcard, *, mock: bool = False) -> SendResult:
        """Send a postcard via the shim.

        Translates :class:`postcards.models.Postcard` → ``Sender`` /
        ``Recipient`` / ``Postcard`` and calls
        ``PostcardCreator.send_free_card``. ``mock`` maps directly
        to the shim's ``mock_send`` argument.

        The picture bytes on ``card`` are wrapped in a fresh
        :class:`io.BytesIO` because the shim's API takes a file-like
        object rather than raw bytes.

        M5: the shim call is wrapped in :func:`with_retries`. The
        retry classifier treats :class:`NotImplementedError` as
        permanent (it is raised by the shim, not the network) so
        tests that forget to monkey-patch ``send_free_card`` fail
        immediately rather than spinning.
        """
        self._require_authenticated()
        if not card.is_valid():
            raise ValueError("Postcard is invalid: sender or recipient missing required fields")

        def _call() -> None:
            from postcards._vendor.postcard_creator import (
                Postcard as ShimPostcard,
            )
            from postcards._vendor.postcard_creator import (
                PostcardCreator,
            )
            from postcards._vendor.postcard_creator import (
                Recipient as ShimRecipient,
            )
            from postcards._vendor.postcard_creator import (
                Sender as ShimSender,
            )

            recipient = ShimRecipient(
                prename=card.recipient.prename,
                lastname=card.recipient.lastname,
                street=card.recipient.street,
                zip_code=card.recipient.zip_code,
                place=card.recipient.place,
                company=card.recipient.company,
                company_addition=card.recipient.company_addition,
                salutation=card.recipient.salutation,
            )
            sender = ShimSender(
                prename=card.sender.prename,
                lastname=card.sender.lastname,
                street=card.sender.street,
                zip_code=card.sender.zip_code,
                place=card.sender.place,
                company=card.sender.company,
                country=card.sender.country,
            )
            pc_card = ShimPostcard(
                sender=sender,
                recipient=recipient,
                picture_stream=card.open_picture(),
                message=card.message.text,
            )

            pcc = PostcardCreator(self._token)  # type: ignore[arg-type]
            pcc.send_free_card(postcard=pc_card, mock_send=mock)

        with_retries(
            _call,
            policy=self._retry_policy,
            classifier=self._classifier,
            logger=_LOGGER,
            description="send",
        )

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


__all__ = [
    "SwissIdConsumerBackend",
]  # noqa: RUF100 - intentional
