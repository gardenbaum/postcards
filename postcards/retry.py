"""Retry / backoff helper for transient network errors.

The CLI wraps every Swiss Post network call in :func:`with_retries`
so a transient blip (DNS hiccup, TLS reset, 502 from the upstream)
does not immediately surface as a fatal error to the user. The
policy is intentionally small and explicit:

* a fixed number of *attempts* (not "retries after the first call");
* exponential backoff with full jitter — ``delay = random.uniform(0, base * multiplier ** (n - 1))``,
  capped at :attr:`RetryPolicy.max_delay`;
* a pluggable :class:`Sleeper` so tests can drive the helper
  deterministically without monkey-patching :func:`time.sleep`;
* a pluggable :class:`Classifier` so the caller decides which
  exception types are retryable (the default retries on
  :class:`postcards.backend.exceptions.TransientBackendError` only).

Why hand-rolled instead of ``tenacity``
---------------------------------------

The project depends on ``requests``, ``Pillow``, ``typer``, and
``PyYAML``; pulling ``tenacity`` for ~30 lines of retry logic
would expand the dependency surface for a feature that has
exactly one callsite per backend method. The hand-rolled version
is fully type-annotated and easy to test.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

#: Module-level logger for the retry helper itself. Tests can
#: pin this with ``caplog`` to assert retry attempts were logged.
_LOGGER = logging.getLogger("postcards.retry")


@runtime_checkable
class Sleeper(Protocol):
    """Pluggable time source for backoff sleeps.

    Production code uses :data:`time_sleeper`, which delegates
    to :func:`time.sleep`. Tests inject a stub that records the
    requested sleep durations so the test asserts on the policy
    without ever blocking the test runner.
    """

    def __call__(self, seconds: float) -> None:  # pragma: no cover - structural
        ...


def time_sleeper(seconds: float) -> None:
    """The default :class:`Sleeper` — :func:`time.sleep` in disguise.

    Wrapped in a module-level function so tests can pass it
    explicitly (``with_retries(..., sleeper=time_sleeper)``)
    and the protocol-vs-function distinction is obvious.
    """
    time.sleep(seconds)


class RetryExhaustedError(RuntimeError):
    """Raised by :func:`with_retries` when every attempt failed.

    The ``last_exception`` attribute holds the most recent
    underlying exception so callers can inspect the failure
    cause. ``__cause__`` is also set so Python's "raise from"
    formatting works in tracebacks.
    """

    def __init__(self, message: str, *, last_exception: BaseException | None = None) -> None:
        super().__init__(message)
        self.last_exception = last_exception
        if last_exception is not None:
            self.__cause__ = last_exception


@dataclass(frozen=True)
class RetryPolicy:
    """Parameters for :func:`with_retries`.

    Attributes
    ----------
    attempts:
        Total attempts (including the first). ``attempts=1`` is
        equivalent to "no retries"; ``attempts=3`` means the
        operation runs up to three times before :class:`RetryExhaustedError`
        is raised.
    base_delay:
        Lower bound (in seconds) of the jitter window for the
        first backoff. The actual sleep is drawn uniformly from
        ``[0, base_delay]``. With ``base_delay=0.5``, the first
        retry sleeps up to half a second.
    multiplier:
        Multiplier applied to ``base_delay`` per attempt. With
        ``base_delay=0.5, multiplier=2.0`` the maximum sleeps
        are 0.5s, 1.0s, 2.0s, ... before the cap.
    max_delay:
        Hard ceiling on the jitter window. The AWS-style full
        jitter formula ``random.uniform(0, min(max_delay, base * mult ** (n-1)))``
        guarantees the sleep never exceeds :attr:`max_delay`.
    sleeper:
        Callable used to actually sleep. Tests pass a stub.
    rng:
        Random number source. Tests pass a seeded
        :class:`random.Random` to make the backoff
        deterministic.
    """

    attempts: int = 4
    base_delay: float = 0.5
    multiplier: float = 2.0
    max_delay: float = 8.0
    sleeper: Sleeper = field(default=time_sleeper)
    rng: random.Random = field(default_factory=random.Random)

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("RetryPolicy.attempts must be >= 1")
        if self.base_delay < 0:
            raise ValueError("RetryPolicy.base_delay must be >= 0")
        if self.multiplier < 1:
            raise ValueError("RetryPolicy.multiplier must be >= 1")
        if self.max_delay < self.base_delay:
            raise ValueError("RetryPolicy.max_delay must be >= base_delay")

    def backoff_seconds(self, *, attempt_index: int) -> float:
        """Return the sleep duration (in seconds) for attempt ``attempt_index``.

        ``attempt_index`` is 1-based: ``1`` is the delay before
        the *second* attempt, i.e. after the first failure. The
        formula implements AWS's "full jitter" — the actual sleep
        is drawn uniformly from ``[0, min(max_delay, base * mult ** (n - 1))]``.
        """
        if attempt_index < 1:
            return 0.0
        ceiling = min(self.max_delay, self.base_delay * (self.multiplier ** (attempt_index - 1)))
        if ceiling <= 0:
            return 0.0
        return self.rng.uniform(0.0, ceiling)


@runtime_checkable
class Classifier(Protocol):
    """Decide whether an exception is worth retrying.

    Implementations return ``True`` to retry, ``False`` to let
    the exception propagate. The default classifier in
    :func:`with_retries` retries on
    :class:`postcards.backend.exceptions.TransientBackendError`.
    """

    def __call__(self, exc: BaseException) -> bool:  # pragma: no cover - structural
        ...


def default_classifier(exc: BaseException) -> bool:
    """Retry on :class:`TransientBackendError`; raise everything else.

    Importing the typed exceptions eagerly here would create a
    cycle (``retry`` → ``backend.exceptions`` → ``retry``), so
    we lazy-import inside the function. The check is type-name
    based because we want the protocol to be importable from
    contexts that do not have the backend package loaded.
    """
    try:
        from postcards.backend.exceptions import TransientBackendError
    except ImportError:  # pragma: no cover - defensive only
        return False
    return isinstance(exc, TransientBackendError)


@dataclass(frozen=True)
class RetryAttempt:
    """One entry in the :attr:`RetryOutcome.attempts` history.

    ``index`` is 1-based and matches :meth:`RetryPolicy.backoff_seconds`.
    ``exception`` is the failure that triggered the retry; ``None``
    for the final successful attempt.
    """

    index: int
    exception: BaseException | None
    slept_seconds: float


@dataclass(frozen=True)
class RetryOutcome:
    """Structured return value of :func:`with_retries`.

    ``result`` is the value :func:`with_retries` returned from
    the last (successful) attempt. ``attempts`` is the full
    history so callers can log or assert on every retry.
    """

    result: Any
    attempts: tuple[RetryAttempt, ...]

    @property
    def retries(self) -> int:
        """Number of *failed* attempts before the final success."""
        return sum(1 for a in self.attempts if a.exception is not None)


def with_retries(
    func: Callable[[], Any],
    *,
    policy: RetryPolicy | None = None,
    classifier: Classifier | None = None,
    logger: logging.Logger | None = None,
    description: str = "operation",
) -> RetryOutcome:
    """Run ``func`` under a :class:`RetryPolicy`.

    Parameters
    ----------
    func:
        Zero-argument callable to invoke. Use ``functools.partial``
        to bind arguments.
    policy:
        The retry policy. ``None`` selects :class:`RetryPolicy`'s
        defaults — 4 attempts with 0.5s base, 2x multiplier,
        8s ceiling.
    classifier:
        Callable deciding whether an exception is transient.
        ``None`` uses :func:`default_classifier`. The CLI passes
        a custom classifier when it wants to retry on a
        different exception type.
    logger:
        Logger to receive the per-attempt lines. ``None`` uses
        the module logger :data:`_LOGGER`. Tests pass a
        :class:`logging.Logger` instance they control.
    description:
        Human-readable name of the operation, used in the retry
        log lines (e.g. ``"send"``, ``"quota fetch"``). The
        default is generic so a caller who forgets the
        parameter still gets a sensible log line.

    Returns
    -------
    RetryOutcome
        The function's return value plus the per-attempt history.

    Raises
    ------
    RetryExhaustedError
        Every attempt failed.
    BaseException
        Re-raised immediately when the classifier returns ``False``,
        so non-transient failures (bad credentials, quota gone,
        programming errors) propagate without retry.
    """
    effective_policy = policy or RetryPolicy()
    effective_classifier: Classifier = classifier or default_classifier
    effective_logger = logger if logger is not None else _LOGGER

    history: list[RetryAttempt] = []
    for index in range(1, effective_policy.attempts + 1):
        try:
            value = func()
        except BaseException as exc:
            # ``BaseException`` catches ``KeyboardInterrupt`` and
            # ``SystemExit`` along with the application errors we
            # want to retry. We must never retry a cancellation
            # signal — that would defeat the user's Ctrl-C and
            # could turn a single keypress into 4 attempts'
            # worth of sleep. Re-raise those immediately,
            # before the classifier gets a chance.
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            should_retry = effective_classifier(exc)
            if not should_retry or index >= effective_policy.attempts:
                if should_retry:
                    # Last attempt — record and raise as RetryExhaustedError.
                    history.append(RetryAttempt(index=index, exception=exc, slept_seconds=0.0))
                    effective_logger.error(
                        "%s failed after %d attempt(s) (%s: %s)",
                        description,
                        effective_policy.attempts,
                        exc.__class__.__name__,
                        exc,
                    )
                    raise RetryExhaustedError(
                        f"{description} failed after {effective_policy.attempts} attempt(s): {exc}",
                        last_exception=exc,
                    ) from exc
                # Non-retryable: propagate immediately so the
                # caller (or its user) sees the original error.
                raise
            slept = effective_policy.backoff_seconds(attempt_index=index)
            effective_logger.warning(
                "%s attempt %d/%d failed (%s: %s); retrying in %.2fs",
                description,
                index,
                effective_policy.attempts,
                exc.__class__.__name__,
                exc,
                slept,
            )
            history.append(RetryAttempt(index=index, exception=exc, slept_seconds=slept))
            if slept > 0:
                effective_policy.sleeper(slept)
            continue
        else:
            history.append(RetryAttempt(index=index, exception=None, slept_seconds=0.0))
            if index > 1:
                effective_logger.info(
                    "%s succeeded on attempt %d/%d",
                    description,
                    index,
                    effective_policy.attempts,
                )
            return RetryOutcome(result=value, attempts=tuple(history))
    # Unreachable: the for-loop always returns or raises.
    raise RetryExhaustedError(f"{description} failed unexpectedly")  # pragma: no cover


__all__ = [
    "Classifier",
    "RetryAttempt",
    "RetryExhaustedError",
    "RetryOutcome",
    "RetryPolicy",
    "Sleeper",
    "default_classifier",
    "time_sleeper",
    "with_retries",
]
