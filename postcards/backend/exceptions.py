"""Typed exceptions for the backend layer.

The CLI distinguishes three failure modes:

* :class:`AuthenticationError` — the credentials were rejected
  by SwissID. Do **not** retry; the next attempt will fail with
  the same error.
* :class:`QuotaExhaustedError` — the user's daily quota is gone.
  Do **not** retry within the same day; surface the ``next_available_at``
  timestamp so the user (or :mod:`postcards.schedule`) can
  reschedule.
* :class:`TransientBackendError` — a network blip, TLS reset,
  5xx from the upstream, or any other recoverable error. Retry
  with backoff per :mod:`postcards.retry`.

Everything else (programming bugs, malformed addresses, etc.)
propagates as-is; the retry helper treats unrecognised exceptions
as non-retryable and lets them surface to the user.

The hierarchy
-------------

::

    BackendError(RuntimeError)
    ├── AuthenticationError
    ├── QuotaExhaustedError
    └── TransientBackendError

:class:`QuotaExhaustedError` here is **distinct** from the
runner-level :class:`postcards.schedule.runner.QuotaExhaustedError`;
the runner's exception subclasses this one so ``except QuotaExhaustedError``
in the CLI catches both, but the schedule-runner variant adds
the runner-specific ``reschedule_to`` payload the user sees in
``schedule list``.
"""

from __future__ import annotations

from datetime import datetime


class BackendError(RuntimeError):
    """Base class for every error raised by a :class:`PostcardBackend` method.

    :class:`RuntimeError` (not :class:`Exception`) keeps the
    error category aligned with the rest of the project's
    exception hierarchy — :class:`postcards.cli.errors.CLIError`
    is also a :class:`RuntimeError` subclass, so a coarse
    ``except RuntimeError`` in the CLI layer catches both
    backend failures and user-input errors uniformly.
    """


class AuthenticationError(BackendError):
    """Credentials were rejected by the upstream auth flow.

    Retrying the same credentials will not help. The CLI surfaces
    the error verbatim and exits non-zero; :mod:`postcards.schedule`
    marks the affected job as :attr:`JobStatus.FAILED` so the
    user can fix the credentials and ``postcards schedule retry``
    the job.
    """


class QuotaExhaustedError(BackendError):
    """The account's daily free-card quota is gone.

    Parameters
    ----------
    message:
        Human-readable summary. The CLI uses the message
        verbatim in ``postcards quota`` and ``schedule list``.
    next_available_at:
        UTC timestamp at which the upstream says the next
        free card will be available. ``None`` when the upstream
        did not return one (fall back to "next UTC midnight"
        on the consumer side).
    retention_days:
        Number of days the upstream retains sent cards in the
        web UI. Mirrors ``QuotaInfo.retention_days``.
    """

    def __init__(
        self,
        message: str,
        *,
        next_available_at: datetime | None = None,
        retention_days: int = 1,
    ) -> None:
        super().__init__(message)
        self.next_available_at = next_available_at
        self.retention_days = retention_days


class TransientBackendError(BackendError):
    """A recoverable backend failure — retry with backoff.

    Typical causes: network reset, TLS handshake failure, 5xx
    response from the upstream, request timeout. The CLI's
    retry helper (:func:`postcards.retry.with_retries`) classifies
    these as retryable.
    """


__all__ = [
    "AuthenticationError",
    "BackendError",
    "QuotaExhaustedError",
    "TransientBackendError",
]
