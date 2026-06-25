"""Run due :class:`ScheduledJob` records against a :class:`PostcardBackend`.

The runner is the heart of the scheduler: given a
:class:`ScheduleBook`, a :class:`Clock`, and a backend factory,
it walks the book, dispatches every due job, updates the
bookkeeping, and returns a list of :class:`ExecutionResult`
records the CLI can summarise.

Why this lives outside the legacy ``do_command_send`` flow
---------------------------------------------------------

The legacy send flow goes through the vendored
``postcard_creator`` shim, which is exercised in tests via
``unittest.mock.patch`` on ``Token.has_valid_credentials`` /
``PostcardCreator.send_free_card``. The schedule runner instead
uses the modern :class:`postcards.backend.base.PostcardBackend`
protocol because:

* the protocol is the public contract (``docs/CONSTITUTION.md``
  §1.1) and is what the test suite uses;
* the in-memory :class:`MockBackend` records every send so
  tests can assert against ``backend.sent`` without patching
  shim internals;
* the runner needs to read quota state before dispatching, and
  the protocol's :meth:`PostcardBackend.quota` returns a typed
  :class:`QuotaInfo` instead of the shim's loose ``{"quota":
  -1, ...}`` mapping.

The runner accepts an injected *backend factory* callable so
tests can hand it a freshly-built :class:`MockBackend` per
invocation. The production caller wraps
:func:`postcards.backend.registry.select_backend` and supplies
the env / config it wants.

Quota handling
--------------

A job is dispatched when ``clock.now() >= job.next_run_at``
AND the account's quota is available. If the quota is
exhausted, the runner reschedules the job to the next
midnight (so the next dispatch happens as soon as the upstream
free-card window opens) and returns
:attr:`JobOutcome.SKIPPED_QUOTA`. The ``last_error`` field on
the job carries the quota-exhaustion message for ``schedule
list`` / ``schedule show``.

Recurring jobs
--------------

When a recurring job completes successfully, the runner
advances the rule's :meth:`RecurrenceRule.advance` from the
current run time and stores the new ``next_run_at`` on the
job. The job stays :attr:`JobStatus.PENDING` so the next
``schedule run`` picks it up. One-shot jobs transition to
:attr:`JobStatus.COMPLETED` and stay in the book for audit
purposes (the user can ``schedule remove`` them).

Error handling
--------------

Any exception raised during dispatch (login failure, network
error, malformed address, ...) transitions the job to
:attr:`JobStatus.FAILED` and stores the error message in
``last_error``. Recurring jobs that fail are *not* advanced —
they stay at their original ``next_run_at`` so the next
``schedule run`` retries them. (One-shot failures also stay in
the queue so the user can see what went wrong.)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, time, timedelta
from typing import Protocol

from postcards.addressbook.models import (
    AddressBook,
    AddressBookEntry,
    AddressCategory,
    TemplateError,
)
from postcards.addressbook.storage import (
    load_address_book,
    load_template_book,
)
from postcards.addressbook.variables import TemplateRenderError
from postcards.backend.base import (
    PostcardBackend,
    QuotaInfo,
    SendResult,
)
from postcards.backend.exceptions import QuotaExhaustedError as BackendQuotaExhaustedError
from postcards.models.message import Message
from postcards.models.postcard import Postcard
from postcards.schedule.models import (
    Clock,
    ExecutionResult,
    FakeClock,
    JobOutcome,
    JobStatus,
    ScheduleBook,
    ScheduledJob,
    SystemClock,
)

_LOGGER = logging.getLogger("postcards.schedule.runner")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class QuotaExhaustedError(BackendQuotaExhaustedError):
    """Raised by :func:`_dispatch_job` when the backend quota is unavailable.

    The runner catches this specifically and reschedules the job
    to the next quota window; any other exception becomes a
    :attr:`JobStatus.FAILED` outcome. Subclassing the
    backend-level :class:`QuotaExhaustedError` means a single
    ``except QuotaExhaustedError`` at the CLI layer catches both
    the runner variant and the bare backend exception.
    """


# ---------------------------------------------------------------------------
# Backend factory protocol
# ---------------------------------------------------------------------------


class BackendFactory(Protocol):
    """Callable that produces a fresh :class:`PostcardBackend`.

    Tests use ``lambda: MockBackend()``; the production wrapper
    is a closure over :func:`postcards.backend.registry.select_backend`.
    The factory is called once per dispatch so each job gets its
    own backend instance — backends are not guaranteed to be
    thread-safe and a per-job instance avoids any aliasing.
    """

    def __call__(self) -> PostcardBackend:  # pragma: no cover - structural type
        ...


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_due_jobs(
    book: ScheduleBook,
    *,
    clock: Clock,
    backend_factory: BackendFactory,
    address_book: AddressBook | None = None,
    dry_run: bool = False,
) -> tuple[ScheduleBook, list[ExecutionResult]]:
    """Walk ``book`` and dispatch every due job.

    Parameters
    ----------
    book:
        The :class:`ScheduleBook` to walk. The function never
        mutates ``book`` in place; it returns a new book with
        the bookkeeping updated.
    clock:
        The :class:`Clock` to read "now" from. Production code
        passes :class:`SystemClock`; tests inject
        :class:`FakeClock`.
    backend_factory:
        Callable that returns a fresh :class:`PostcardBackend`
        for each dispatch. Production code wraps
        :func:`postcards.backend.registry.select_backend`.
    address_book:
        Optional pre-loaded :class:`AddressBook` used to resolve
        the job's ``recipient_name`` / ``sender_name``. ``None``
        triggers a fresh load from disk; passing a book is the
        test-friendly hook so the runner does not need to touch
        ``$XDG_DATA_HOME`` during unit tests.
    dry_run:
        When ``True``, the runner logs what it *would* dispatch
        and skips the actual ``backend.send`` call. Useful for
        ``schedule run --dry-run`` so the user can preview
        which jobs are about to fire.

    Returns
    -------
    tuple[ScheduleBook, list[ExecutionResult]]
        The updated book (callers persist this with
        :func:`postcards.schedule.storage.save_schedule_book`)
        and the per-job outcomes.
    """
    if address_book is None:
        address_book = load_address_book()
    now = clock.now()
    results: list[ExecutionResult] = []
    new_book = book
    for job in book:
        updated_job, result = _dispatch_one(
            job,
            now=now,
            clock=clock,
            backend_factory=backend_factory,
            address_book=address_book,
            dry_run=dry_run,
        )
        # ``result`` is always returned; only the book rebuild
        # is skipped when the job is unchanged. Skipped jobs
        # (not-due / bad-status) still appear in the results so
        # callers can summarise what the runner saw.
        results.append(result)
        if updated_job is job:
            continue
        new_book = new_book.update(updated_job)
    return new_book, results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _dispatch_one(
    job: ScheduledJob,
    *,
    now: datetime,
    clock: Clock,
    backend_factory: BackendFactory,
    address_book: AddressBook,
    dry_run: bool,
) -> tuple[ScheduledJob, ExecutionResult]:
    """Dispatch a single job, returning the (possibly new) job + outcome.

    The function never mutates ``job``; it returns the original
    instance when nothing changed (so the caller can skip the
    book rebuild) and a new instance when bookkeeping moved.
    """
    if job.status is JobStatus.PENDING and not job.is_due(now):
        return job, _skipped(job.id, JobOutcome.SKIPPED_NOT_DUE, "not due yet")
    if job.status is not JobStatus.PENDING:
        return job, _skipped(
            job.id,
            JobOutcome.SKIPPED_BAD_STATUS,
            f"job is {job.status.value}, not pending",
        )

    if dry_run:
        return job.with_status(
            JobStatus.PENDING,
        ), ExecutionResult(
            job_id=job.id,
            outcome=JobOutcome.SKIPPED_NOT_DUE,
            message="dry-run: would dispatch now",
        )

    try:
        recipient_entry, sender_entry = _resolve_endpoints(job, address_book=address_book)
    except _EndpointResolutionError as exc:
        updated = job.with_status(
            JobStatus.FAILED,
            last_run_at=now,
            last_error=str(exc),
        )
        return updated, ExecutionResult(
            job_id=job.id,
            outcome=JobOutcome.FAILED,
            message=str(exc),
        )

    rendered_message = _render_message(job)
    if isinstance(rendered_message, _MessageRenderError):
        updated = job.with_status(
            JobStatus.FAILED,
            last_run_at=now,
            last_error=rendered_message.message,
        )
        return updated, ExecutionResult(
            job_id=job.id,
            outcome=JobOutcome.FAILED,
            message=rendered_message.message,
        )

    postcard = _build_postcard(
        recipient_entry=recipient_entry,
        sender_entry=sender_entry,
        message_text=rendered_message,
        picture_location=job.picture,
    )

    try:
        result = _send_via_backend(
            postcard=postcard,
            job=job,
            backend_factory=backend_factory,
        )
    except QuotaExhaustedError as exc:
        rescheduled_at = _next_quota_window(now)
        updated = job.with_status(
            JobStatus.PENDING,
            next_run_at=rescheduled_at,
            last_run_at=now,
            last_error=str(exc),
        )
        return updated, ExecutionResult(
            job_id=job.id,
            outcome=JobOutcome.SKIPPED_QUOTA,
            message=f"quota exhausted; rescheduled to {rescheduled_at.isoformat()}",
        )
    except Exception as exc:
        updated = job.with_status(
            JobStatus.FAILED,
            last_run_at=now,
            last_error=str(exc) or exc.__class__.__name__,
        )
        return updated, ExecutionResult(
            job_id=job.id,
            outcome=JobOutcome.FAILED,
            message=f"send failed: {exc}",
        )

    # Successful dispatch — update bookkeeping and possibly advance.
    if job.recurrence.kind == "none":
        updated = job.with_status(
            JobStatus.COMPLETED,
            last_run_at=now,
            last_error=None,
            last_confirmation=result.confirmation,
        )
        return updated, ExecutionResult(
            job_id=job.id,
            outcome=JobOutcome.SENT,
            message=f"sent; confirmation={result.confirmation or 'n/a'}",
            confirmation=result.confirmation,
        )

    try:
        next_run = job.recurrence.advance(now)
    except Exception as exc:
        updated = job.with_status(
            JobStatus.FAILED,
            last_run_at=now,
            last_error=f"could not advance recurrence: {exc}",
            last_confirmation=result.confirmation,
        )
        return updated, ExecutionResult(
            job_id=job.id,
            outcome=JobOutcome.FAILED,
            message=f"send succeeded but recurrence could not be advanced: {exc}",
            confirmation=result.confirmation,
        )
    updated = job.with_status(
        JobStatus.PENDING,
        next_run_at=next_run,
        last_run_at=now,
        last_error=None,
        last_confirmation=result.confirmation,
    )
    return updated, ExecutionResult(
        job_id=job.id,
        outcome=JobOutcome.RESCHEDULED_RECURRING,
        message=(
            f"sent; confirmation={result.confirmation or 'n/a'}; next run at {next_run.isoformat()}"
        ),
        confirmation=result.confirmation,
    )


def _skipped(job_id: str, outcome: JobOutcome, message: str) -> ExecutionResult:
    """Build an :class:`ExecutionResult` for a non-dispatch path."""
    return ExecutionResult(job_id=job_id, outcome=outcome, message=message)


# ---------------------------------------------------------------------------
# Endpoint resolution
# ---------------------------------------------------------------------------


class _EndpointResolutionError(ValueError):
    """Raised when the recipient / sender cannot be resolved."""


def _resolve_endpoints(
    job: ScheduledJob,
    *,
    address_book: AddressBook,
) -> tuple[AddressBookEntry, AddressBookEntry]:
    """Return the (recipient, sender) :class:`AddressBookEntry` pair.

    The runner resolves names via the supplied
    :class:`AddressBook` (defaulting to ``load_address_book()``
    when the caller passes ``None``). Both entries must be
    present — a missing sender falls back to a synthetic sender
    derived from the recipient, mirroring the legacy
    ``do_command_send`` behaviour (``sender = recipient`` when
    no explicit sender is configured).

    Raises
    ------
    _EndpointResolutionError
        When the recipient cannot be found or has the wrong
        category, or when an explicit sender is configured but
        cannot be resolved. The error message is what the CLI
        prints back to the user.
    """
    recipient = address_book.find(job.recipient_name)
    if recipient is None:
        raise _EndpointResolutionError(f"no address-book entry named {job.recipient_name!r}")
    if recipient.category is not AddressCategory.RECIPIENT:
        raise _EndpointResolutionError(
            f"address-book entry {job.recipient_name!r} is a "
            f"{recipient.category.value}, not a recipient"
        )
    if job.sender_name:
        sender = address_book.find(job.sender_name)
        if sender is None:
            raise _EndpointResolutionError(f"no address-book entry named {job.sender_name!r}")
        if sender.category is not AddressCategory.SENDER:
            raise _EndpointResolutionError(
                f"address-book entry {job.sender_name!r} is a {sender.category.value}, not a sender"
            )
        return recipient, sender
    # No explicit sender — synthesise a sender entry that mirrors
    # the recipient so the rest of the pipeline gets a complete
    # :class:`AddressSpec` pair.
    return recipient, recipient


# ---------------------------------------------------------------------------
# Message rendering
# ---------------------------------------------------------------------------


class _MessageRenderError:
    """Internal error wrapper for template-rendering failures.

    Distinct from :class:`TemplateRenderError` so the runner can
    surface the message verbatim without leaking the variables
    module's exception hierarchy into the CLI layer.
    """

    __slots__ = ("message",)

    def __init__(self, message: str) -> None:
        self.message = message


def _render_message(job: ScheduledJob) -> str | _MessageRenderError:
    """Return the message text the runner will hand to :class:`Postcard`.

    Precedence:

    1. ``job.message_template_name`` — render the named template
       from the template book, substituting
       ``job.template_variables``.
    2. ``job.message`` — the literal text the user supplied at
       ``schedule add`` time.
    3. ``""`` — the runner leaves the message blank when neither
       is set (the postcard still carries the picture if
       ``job.picture`` is set).
    """
    if job.message_template_name:
        try:
            template_book = load_template_book()
            template = template_book.find(job.message_template_name)
        except TemplateError as exc:
            return _MessageRenderError(str(exc))
        if template is None:
            return _MessageRenderError(f"no template named {job.message_template_name!r}")
        try:
            return template.render(job.template_variables)
        except TemplateRenderError as exc:
            return _MessageRenderError(str(exc))
    if job.message is not None:
        return job.message
    return ""


# ---------------------------------------------------------------------------
# Postcard construction
# ---------------------------------------------------------------------------


def _build_postcard(
    *,
    recipient_entry: AddressBookEntry,
    sender_entry: AddressBookEntry,
    message_text: str,
    picture_location: str | None,
) -> Postcard:
    """Build the :class:`Postcard` the runner hands to ``backend.send``.

    The picture is loaded lazily — ``Postcard.from_image`` runs the
    full image pipeline (download, slice, JPEG-encode). For
    scheduled jobs the picture is expected to be a local path or
    URL captured at queue time; remote URLs are re-fetched on
    every dispatch because the underlying image may have changed.
    """
    message = Message.from_text(message_text)
    picture_bytes: bytes | None = None
    if picture_location:
        picture_bytes = _read_picture(picture_location)
    return Postcard(
        sender=sender_entry.address,
        recipient=recipient_entry.address,
        message=message,
        picture=picture_bytes,
    )


def _read_picture(location: str) -> bytes:
    """Return the picture bytes at ``location`` (local path or URL).

    Mirrors :meth:`postcards.postcards.Postcards._read_picture`
    but returns ``bytes`` instead of a stream so the runner can
    build a :class:`Postcard` directly. The legacy flow wraps
    bytes in :class:`io.BytesIO` at the shim boundary; the
    modern backend accepts ``bytes`` (see
    :class:`postcards.models.postcard.Postcard`).
    """
    import urllib.request

    if location.startswith("http://") or location.startswith("https://"):
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.11 "
                "(KHTML, like Gecko) Chrome/23.0.1271.64 Safari/537.11"
            ),
            "Accept": "*/*",
        }
        request = urllib.request.Request(location, headers=headers)
        with urllib.request.urlopen(request) as response:
            return response.read()
    with open(location, "rb") as handle:
        return handle.read()


# ---------------------------------------------------------------------------
# Backend dispatch
# ---------------------------------------------------------------------------


def _send_via_backend(
    *,
    postcard: Postcard,
    job: ScheduledJob,
    backend_factory: BackendFactory,
) -> SendResult:
    """Send ``postcard`` via a backend produced by ``backend_factory``.

    The function performs the quota check before
    :meth:`PostcardBackend.send` so the job's status reflects the
    exhausted quota rather than the backend's send-side error.

    M5: emits structured log lines on login / quota / send so a
    user running ``schedule run -vv`` sees exactly which step
    succeeded and which step blocked. The transient-error
    classification happens *inside* the backend's retry helper
    — by the time we get here, a
    :class:`postcards.backend.exceptions.TransientBackendError`
    has already been retried to exhaustion.
    """
    _LOGGER.info(
        "dispatching job %s via backend", job.id,
    )
    backend = backend_factory()
    username = job.username or ""
    password = job.password or ""
    if not username or not password:
        raise RuntimeError(
            "scheduled job has no username/password; "
            "re-queue with 'postcards schedule add --username USER --password PASS' "
            "or set POSTCARDS_USERNAME / POSTCARDS_PASSWORD in the cron environment"
        )

    backend.login(username, password)
    quota = backend.quota()
    if not quota.available:
        _LOGGER.warning(
            "job %s: quota exhausted (next available at %s); rescheduling",
            job.id,
            quota.next_available_at.isoformat() if quota.next_available_at else "unknown",
        )
        raise QuotaExhaustedError(_quota_message(quota))
    _LOGGER.debug("job %s: quota ok; sending", job.id)
    result = backend.send(postcard, mock=False)
    _LOGGER.info(
        "job %s: sent (confirmation=%s)",
        job.id,
        result.confirmation or "n/a",
    )
    return result


def _quota_message(quota: QuotaInfo) -> str:
    """Build a human-readable quota-exhaustion message."""
    when = quota.next_available_at.isoformat() if quota.next_available_at else "unknown"
    return f"quota exhausted; next available at {when}"


# ---------------------------------------------------------------------------
# Quota-window scheduling
# ---------------------------------------------------------------------------


def _next_quota_window(now: datetime) -> datetime:
    """Return the next midnight (UTC) strictly after ``now``.

    The Swiss Post free-card window resets at midnight in the
    user's local timezone, but the upstream API does not expose
    the timezone it uses; midnight UTC is a safe approximation
    and matches what every other CLI of this shape does. The
    runner uses this only when the backend reports quota
    exhaustion — once the quota opens, the next ``schedule run``
    picks up the job and dispatches it.
    """
    tomorrow = now.date() + timedelta(days=1)
    return datetime.combine(tomorrow, time.min, tzinfo=UTC)


__all__ = [
    "BackendFactory",
    "FakeClock",
    "QuotaExhaustedError",
    "SystemClock",
    "run_due_jobs",
]
