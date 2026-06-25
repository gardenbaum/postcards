"""Translate backend-level exceptions into actionable user-facing messages.

Both the CLI (``postcards.cli.backend_errors``) and the
schedule runner (``postcards.schedule.runner``) need to turn
backend exceptions into messages the user can act on. To keep
the wording in one place, this module owns the mapping; the
CLI layer re-exports it under the name ``render_cli_error``.

The translator's contract
-------------------------

* ``translate(exc)`` returns a ``(message, exit_code)`` pair.
  ``message`` is human-readable and ends with a hint about the
  next step; ``exit_code`` follows the conventional Unix
  mapping (``1`` for general backend failures, ``75`` for
  transient failures after retries — matching ``EX_TEMPFAIL``
  from ``sysexits.h`` so a cron job can distinguish "retry
  later" from "fix the config").

* The translator is deliberately *coarse*: it returns plain
  text plus an exit code. The CLI renders the text on stderr;
  the schedule runner stores it in ``last_error`` for
  ``schedule list`` to display.

* A new exception type added to ``postcards.backend.exceptions``
  but not yet handled here falls through to the
  ``BackendError`` catch-all branch. That keeps the door open
  for extension without immediately breaking callers.

Exit-code conventions
---------------------

* ``1`` — general backend failure (auth, quota, unexpected
  backend error). Used as the default.
* ``75`` (``EX_TEMPFAIL``) — transient failure after retries;
  the caller may legitimately retry from cron.
* ``2`` — usage error. The translator does not currently emit
  this code; callers that hit usage errors raise
  :class:`postcards.cli.errors.CLIError` directly.
"""

from __future__ import annotations

from postcards.backend.exceptions import (
    AuthenticationError,
    BackendError,
    QuotaExhaustedError,
    TransientBackendError,
)
from postcards.retry import RetryExhaustedError

#: Conventional exit code for "transient failure, try again later".
#: Matches ``EX_TEMPFAIL`` from ``sysexits.h`` so a cron job can
#: distinguish upstream hiccups from configuration errors.
EX_TEMPFAIL: int = 75


def translate(exc: BaseException) -> tuple[str, int]:
    """Translate ``exc`` into ``(message, exit_code)``.

    The function walks the backend exception hierarchy in
    reverse order of specificity (most specific first) so a
    :class:`QuotaExhaustedError` is recognised as such even
    when it also inherits from a broader base the helper does
    not know about. :class:`RetryExhaustedError` is checked
    before the generic :class:`BackendError` because the
    retry helper wraps transient failures that did not
    recover.
    """
    # Most specific first.
    if isinstance(exc, RetryExhaustedError):
        # ``str(exc)`` is rendered by ``RetryExhaustedError.__init__``
        # as ``"<description> failed after <N> attempt(s): <last>"``.
        # If the underlying exception's str is empty (e.g. ``ValueError("")``),
        # surface the class name so the user has a clue what failed.
        last = exc.last_exception
        last_label = ""
        if last is not None and not str(last):
            last_label = f" ({last.__class__.__name__})"
        msg = (
            f"{exc}{last_label}. "
            "Check your network connection, run with --verbose to see "
            "the per-attempt retry log, or pass --backend=mock to validate "
            "the command offline."
        )
        return msg, EX_TEMPFAIL
    if isinstance(exc, QuotaExhaustedError):
        when = exc.next_available_at.isoformat() if exc.next_available_at else "unknown"
        msg = (
            f"quota exhausted; next free card available at {when}. "
            "Use 'postcards quota --wait' to block until the quota opens, "
            "or schedule the job for tomorrow with 'postcards schedule add --at ...'."
        )
        return msg, 1
    if isinstance(exc, AuthenticationError):
        msg = (
            "authentication failed; the upstream rejected the credentials. "
            "Check POSTCARDS_USERNAME / POSTCARDS_PASSWORD, "
            "update your accounts file with 'postcards accounts add', "
            "or pass --backend=mock to exercise the path without authenticating."
        )
        return msg, 1
    if isinstance(exc, TransientBackendError):
        # ``TransientBackendError`` only escapes the retry helper if a
        # caller chose not to retry it. Suggest the user investigate.
        msg = (
            "transient backend error; the network may be unstable. "
            "Retry with --verbose to see the per-attempt log, "
            "or pass --backend=mock to validate the command offline."
        )
        return msg, EX_TEMPFAIL
    if isinstance(exc, BackendError):
        # Catch-all for the typed hierarchy (new exception types added
        # in the future fall through to this branch). The message is
        # the backend-supplied text plus a generic next-step hint.
        return f"backend error: {exc}", 1
    # Non-backend exception — let the caller's default handler render
    # the traceback so the user can see what really happened.
    return f"unexpected error: {exc}", 1


__all__ = ["EX_TEMPFAIL", "translate"]
