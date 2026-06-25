"""Unit tests for :mod:`postcards.backend.exceptions`.

The exceptions are the contract the retry helper, the schedule
runner, and the CLI agree on. This module pins the hierarchy,
the typed attributes on :class:`QuotaExhaustedError`, and the
mapping from :class:`QuotaInfo` so a future refactor that breaks
the contract fails loudly.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from postcards.backend.base import QuotaInfo
from postcards.backend.exceptions import (
    AuthenticationError,
    BackendError,
    QuotaExhaustedError,
    TransientBackendError,
)

# ---------------------------------------------------------------------------
# Hierarchy
# ---------------------------------------------------------------------------


class TestHierarchy:
    def test_all_inherit_from_backend_error(self) -> None:
        assert issubclass(AuthenticationError, BackendError)
        assert issubclass(QuotaExhaustedError, BackendError)
        assert issubclass(TransientBackendError, BackendError)

    def test_backend_error_inherits_from_runtime_error(self) -> None:
        # ``RuntimeError`` keeps the CLI's coarse ``except
        # RuntimeError`` branch able to catch backend failures
        # without us redefining the surface.
        assert issubclass(BackendError, RuntimeError)

    def test_typed_exceptions_are_distinct(self) -> None:
        # Each subclass must be its own type — the retry
        # classifier relies on ``isinstance`` checks, so a
        # mismerge would silently change retry behaviour.
        assert AuthenticationError is not QuotaExhaustedError
        assert QuotaExhaustedError is not TransientBackendError
        assert AuthenticationError is not TransientBackendError

    def test_can_be_raised_and_caught_by_base(self) -> None:
        for exc_cls in (
            AuthenticationError,
            QuotaExhaustedError,
            TransientBackendError,
        ):
            with pytest.raises(BackendError):
                raise exc_cls("boom")


# ---------------------------------------------------------------------------
# QuotaExhaustedError payload
# ---------------------------------------------------------------------------


class TestQuotaPayload:
    def test_carries_next_available_at(self) -> None:
        when = datetime(2026, 6, 26, 0, 0, tzinfo=UTC)
        exc = QuotaExhaustedError("quota gone", next_available_at=when)
        assert exc.next_available_at == when
        assert exc.retention_days == 1

    def test_carries_retention_days(self) -> None:
        exc = QuotaExhaustedError("quota gone", retention_days=7)
        assert exc.retention_days == 7

    def test_defaults_when_omitted(self) -> None:
        exc = QuotaExhaustedError("quota gone")
        assert exc.next_available_at is None
        assert exc.retention_days == 1

    def test_str_is_user_facing(self) -> None:
        # The CLI surfaces ``str(exc)`` directly in error
        # messages; the message must read like a human sentence.
        when: datetime = datetime(2026, 6, 26, 0, 0, tzinfo=UTC)
        exc = QuotaExhaustedError(
            f"quota exhausted; next free card at {when.isoformat()}",
            next_available_at=when,
        )
        assert "quota exhausted" in str(exc)
        assert "2026-06-26" in str(exc)

    def test_can_be_constructed_from_quota_info(self) -> None:
        when: datetime = datetime(2026, 6, 26, 0, 0, tzinfo=UTC)
        info = QuotaInfo(
            available=False,
            next_available_at=when,
            retention_days=3,
        )
        assert info.next_available_at is not None
        exc = QuotaExhaustedError(
            f"quota exhausted; next free card at {info.next_available_at.isoformat()}",
            next_available_at=info.next_available_at,
            retention_days=info.retention_days,
        )
        assert exc.next_available_at == info.next_available_at
        assert exc.retention_days == 3
        assert "quota exhausted" in str(exc)


# ---------------------------------------------------------------------------
# Discriminability
# ---------------------------------------------------------------------------


class TestDiscriminability:
    """The retry helper discriminates by ``isinstance``; these tests pin that."""

    def test_authentication_is_not_quota(self) -> None:
        exc: BackendError = AuthenticationError("bad pw")
        assert not isinstance(exc, QuotaExhaustedError)
        assert not isinstance(exc, TransientBackendError)

    def test_quota_is_not_transient(self) -> None:
        exc: BackendError = QuotaExhaustedError("quota gone")
        assert not isinstance(exc, TransientBackendError)
        assert not isinstance(exc, AuthenticationError)

    def test_transient_is_not_quota(self) -> None:
        exc: BackendError = TransientBackendError("connection reset")
        assert not isinstance(exc, QuotaExhaustedError)
        assert not isinstance(exc, AuthenticationError)


# ---------------------------------------------------------------------------
# Import surface
# ---------------------------------------------------------------------------


class TestImports:
    """The CLI imports these names from :mod:`postcards.backend` directly."""

    def test_exported_from_backend_package(self) -> None:
        from postcards.backend import (
            AuthenticationError as Imported,
        )
        from postcards.backend import (
            BackendError as BE,
        )
        from postcards.backend import (
            QuotaExhaustedError as QE,
        )
        from postcards.backend import (
            TransientBackendError as TE,
        )

        assert Imported is AuthenticationError
        assert BE is BackendError
        assert QE is QuotaExhaustedError
        assert TE is TransientBackendError
