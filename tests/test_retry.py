"""Unit tests for :mod:`postcards.retry`.

The retry helper is the M5 workhorse: every transient error
path runs through :func:`postcards.retry.with_retries`. These
tests are pure (no network, no async, no time) — a stub
:class:`Sleeper` records the requested durations, and a stub
classifier lets us drive arbitrary exception types.
"""

from __future__ import annotations

import logging
import random

import pytest

from postcards.backend.exceptions import (
    AuthenticationError,
    QuotaExhaustedError,
    TransientBackendError,
)
from postcards.retry import (
    RetryExhaustedError,
    RetryOutcome,
    RetryPolicy,
    with_retries,
)

# ---------------------------------------------------------------------------
# Recording fixtures
# ---------------------------------------------------------------------------


class _RecordingSleeper:
    """Sleeper stub that records every requested sleep duration."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture
def sleeper() -> _RecordingSleeper:
    """A fresh recorder per test."""
    return _RecordingSleeper()


@pytest.fixture
def seeded_policy(sleeper: _RecordingSleeper) -> RetryPolicy:
    """Deterministic policy — RNG is pinned, sleeper is recording.

    ``attempts=3`` keeps the loop short so tests stay readable;
    ``base_delay=0.01`` keeps the jitter window small enough that
    the recorded durations are stable.
    """
    return RetryPolicy(
        attempts=3,
        base_delay=0.01,
        multiplier=2.0,
        max_delay=0.04,
        sleeper=sleeper,
        rng=random.Random(0),
    )


# ---------------------------------------------------------------------------
# Successful call
# ---------------------------------------------------------------------------


class TestSuccess:
    def test_returns_value_on_first_try(
        self, seeded_policy: RetryPolicy, sleeper: _RecordingSleeper
    ) -> None:
        def func() -> str:
            return "ok"

        outcome = with_retries(
            func, policy=seeded_policy, classifier=lambda exc: True, description="probe"
        )
        assert outcome.result == "ok"
        assert outcome.retries == 0
        # History: one entry, no exception, no sleep.
        assert len(outcome.attempts) == 1
        assert outcome.attempts[0].exception is None
        assert outcome.attempts[0].slept_seconds == 0.0
        assert sleeper.calls == []

    def test_records_full_attempt_history(
        self, seeded_policy: RetryPolicy, sleeper: _RecordingSleeper
    ) -> None:
        calls = {"n": 0}

        def func() -> int:
            calls["n"] += 1
            if calls["n"] < 2:
                raise TransientBackendError("flap")
            return calls["n"]

        outcome = with_retries(
            func, policy=seeded_policy, classifier=lambda exc: True, description="probe"
        )
        assert outcome.result == 2
        assert len(outcome.attempts) == 2
        assert outcome.attempts[0].exception is not None
        assert outcome.attempts[1].exception is None
        assert outcome.attempts[0].slept_seconds > 0
        # Exactly one sleep for the one retry.
        assert len(sleeper.calls) == 1


# ---------------------------------------------------------------------------
# Retries exhausted
# ---------------------------------------------------------------------------


class TestExhaustion:
    def test_raises_retry_exhausted_after_all_attempts_fail(
        self, seeded_policy: RetryPolicy, sleeper: _RecordingSleeper
    ) -> None:
        def func() -> None:
            raise TransientBackendError("never recovers")

        with pytest.raises(RetryExhaustedError) as excinfo:
            with_retries(
                func,
                policy=seeded_policy,
                classifier=lambda exc: True,
                description="probe",
            )
        # The underlying exception is preserved.
        assert isinstance(excinfo.value.last_exception, TransientBackendError)
        # Sleep happens between attempts only — for ``attempts=3``
        # we get two sleeps.
        assert len(sleeper.calls) == seeded_policy.attempts - 1
        assert all(s > 0 for s in sleeper.calls)

    def test_exhausted_error_wraps_last_exception_message(self, seeded_policy: RetryPolicy) -> None:
        def func() -> None:
            raise TransientBackendError("downstream kaput")

        with pytest.raises(RetryExhaustedError) as excinfo:
            with_retries(
                func,
                policy=seeded_policy,
                classifier=lambda exc: True,
                description="probe",
            )
        assert "probe" in str(excinfo.value)
        assert "downstream kaput" in str(excinfo.value)
        assert excinfo.value.last_exception is not None
        assert str(excinfo.value.last_exception) == "downstream kaput"


# ---------------------------------------------------------------------------
# Non-retryable errors
# ---------------------------------------------------------------------------


class TestNonRetryable:
    def test_propagates_immediately_when_classifier_returns_false(
        self, seeded_policy: RetryPolicy, sleeper: _RecordingSleeper
    ) -> None:
        def func() -> None:
            raise AuthenticationError("wrong password")

        with pytest.raises(AuthenticationError):
            with_retries(
                func,
                policy=seeded_policy,
                classifier=lambda exc: isinstance(exc, TransientBackendError),
                description="login",
            )
        # No sleep happens — the error is permanent.
        assert sleeper.calls == []

    def test_quota_exhausted_is_not_retried(
        self, seeded_policy: RetryPolicy, sleeper: _RecordingSleeper
    ) -> None:
        def func() -> None:
            raise QuotaExhaustedError("quota exhausted")

        with pytest.raises(QuotaExhaustedError):
            with_retries(
                func,
                policy=seeded_policy,
                classifier=lambda exc: isinstance(exc, TransientBackendError),
                description="send",
            )
        assert sleeper.calls == []


# ---------------------------------------------------------------------------
# Cancellation signals
# ---------------------------------------------------------------------------


class TestCancellation:
    def test_keyboard_interrupt_propagates_without_retry(
        self, seeded_policy: RetryPolicy, sleeper: _RecordingSleeper
    ) -> None:
        def func() -> None:
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            with_retries(
                func,
                policy=seeded_policy,
                classifier=lambda exc: True,  # even an "always retry" classifier
                description="probe",
            )
        # The signal must propagate on the first attempt.
        assert sleeper.calls == []

    def test_system_exit_propagates_without_retry(
        self, seeded_policy: RetryPolicy, sleeper: _RecordingSleeper
    ) -> None:
        def func() -> None:
            raise SystemExit(1)

        with pytest.raises(SystemExit):
            with_retries(
                func,
                policy=seeded_policy,
                classifier=lambda exc: True,
                description="probe",
            )
        assert sleeper.calls == []


# ---------------------------------------------------------------------------
# Backoff shape
# ---------------------------------------------------------------------------


class TestBackoff:
    @pytest.mark.parametrize(
        ("base_delay", "multiplier", "max_delay", "attempt_index", "expected_ceiling"),
        [
            (0.5, 2.0, 8.0, 1, 0.5),
            (0.5, 2.0, 8.0, 2, 1.0),
            (0.5, 2.0, 8.0, 3, 2.0),
            (0.5, 2.0, 8.0, 4, 4.0),
            (0.5, 2.0, 8.0, 5, 8.0),  # capped at max_delay
            (0.5, 2.0, 8.0, 10, 8.0),
        ],
    )
    def test_backoff_ceiling_grows_exponentially(
        self,
        base_delay: float,
        multiplier: float,
        max_delay: float,
        attempt_index: int,
        expected_ceiling: float,
    ) -> None:
        # The recorded sleeper is the recording stub — the test
        # only inspects the upper bound, so an inline lambda
        # would also work. We use a recording stub so a future
        # refactor that changes the draw (e.g. to a fixed delay)
        # can still be observed here.
        rng = random.Random(0)
        sleeper = _RecordingSleeper()
        policy = RetryPolicy(
            attempts=20,
            base_delay=base_delay,
            multiplier=multiplier,
            max_delay=max_delay,
            sleeper=sleeper,
            rng=rng,
        )
        # Draw N times to estimate the ceiling.
        for _ in range(50):
            sample = policy.backoff_seconds(attempt_index=attempt_index)
            assert 0.0 <= sample <= expected_ceiling + 1e-9

    def test_backoff_caps_at_max_delay(self) -> None:
        policy = RetryPolicy(
            attempts=20,
            base_delay=1.0,
            multiplier=2.0,
            max_delay=4.0,
            sleeper=_RecordingSleeper(),
            rng=random.Random(0),
        )
        for attempt_index in range(1, 10):
            sample = policy.backoff_seconds(attempt_index=attempt_index)
            assert sample <= 4.0


# ---------------------------------------------------------------------------
# Policy validation
# ---------------------------------------------------------------------------


class TestPolicyValidation:
    def test_attempts_must_be_at_least_one(self) -> None:
        with pytest.raises(ValueError, match="attempts"):
            RetryPolicy(attempts=0)

    def test_base_delay_must_be_non_negative(self) -> None:
        with pytest.raises(ValueError, match="base_delay"):
            RetryPolicy(base_delay=-1.0)

    def test_multiplier_must_be_at_least_one(self) -> None:
        with pytest.raises(ValueError, match="multiplier"):
            RetryPolicy(multiplier=0.5)

    def test_max_delay_must_be_at_least_base_delay(self) -> None:
        with pytest.raises(ValueError, match="max_delay"):
            RetryPolicy(base_delay=1.0, max_delay=0.5)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class TestLogging:
    def test_logs_warning_per_retry_attempt(
        self, seeded_policy: RetryPolicy, caplog: pytest.LogCaptureFixture
    ) -> None:
        calls = {"n": 0}

        def func() -> int:
            calls["n"] += 1
            if calls["n"] < 3:
                raise TransientBackendError("flap")
            return calls["n"]

        with caplog.at_level(logging.WARNING, logger="postcards.retry"):
            with_retries(
                func,
                policy=seeded_policy,
                classifier=lambda exc: isinstance(exc, TransientBackendError),
                description="send",
            )
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        # Two retries => two warning lines.
        assert len(warnings) == 2
        for record in warnings:
            assert "send" in record.getMessage()
            assert "TransientBackendError" in record.getMessage()

    def test_logs_info_on_eventual_success(
        self, seeded_policy: RetryPolicy, caplog: pytest.LogCaptureFixture
    ) -> None:
        calls = {"n": 0}

        def func() -> int:
            calls["n"] += 1
            if calls["n"] < 2:
                raise TransientBackendError("flap")
            return calls["n"]

        with caplog.at_level(logging.INFO, logger="postcards.retry"):
            with_retries(
                func,
                policy=seeded_policy,
                classifier=lambda exc: isinstance(exc, TransientBackendError),
                description="send",
            )
        success_lines = [r for r in caplog.records if r.levelno == logging.INFO]
        assert any("attempt 2/3" in r.getMessage() for r in success_lines)


# ---------------------------------------------------------------------------
# Return value shape
# ---------------------------------------------------------------------------


class TestOutcome:
    def test_outcome_attributes(self, seeded_policy: RetryPolicy) -> None:
        outcome: RetryOutcome = with_retries(
            lambda: 42,
            policy=seeded_policy,
            classifier=lambda exc: True,
            description="noop",
        )
        assert outcome.result == 42
        assert outcome.retries == 0
        assert len(outcome.attempts) == 1
