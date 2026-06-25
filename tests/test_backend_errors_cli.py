"""Unit tests for :mod:`postcards.cli.backend_errors`.

The translator is the single place that turns typed backend
exceptions into user-facing CLI messages. The tests pin the
mapping (which exception → which exit code → which message
substring) so a future refactor that changes the wording does
not silently change the user experience.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import typer

from postcards.backend.exceptions import (
    AuthenticationError,
    BackendError,
    QuotaExhaustedError,
    TransientBackendError,
)
from postcards.cli.backend_errors import raise_for_backend_error, render_cli_error
from postcards.retry import RetryExhaustedError

# ---------------------------------------------------------------------------
# Per-exception mapping
# ---------------------------------------------------------------------------


class TestAuthenticationError:
    def test_message_mentions_credentials(self) -> None:
        exc = AuthenticationError("bad pw")
        msg, code = render_cli_error(exc)
        assert "credentials" in msg.lower() or "authentication" in msg.lower()
        assert code == 1

    def test_message_hints_next_step(self) -> None:
        exc = AuthenticationError("bad pw")
        msg, _ = render_cli_error(exc)
        # The user should be told how to fix this.
        assert "POSTCARDS_USERNAME" in msg
        assert "POSTCARDS_PASSWORD" in msg
        assert "--backend=mock" in msg

    def test_message_points_at_doctor(self) -> None:
        """M5: every AuthenticationError message mentions ``postcards doctor``."""
        exc = AuthenticationError("bad pw")
        msg, _ = render_cli_error(exc)
        assert "postcards doctor" in msg


class TestAuthenticationErrorSpecialCases:
    """M5: the auth translator detects 2FA / anomaly-detection signals.

    The upstream SwissID flow raises ``AuthenticationError`` for
    three distinct scenarios (wrong password, 2FA required,
    anomaly detection) that all share the same exception
    type. The translator inspects the message text and emits
    scenario-specific next-step hints so the user does not
    have to read docs to figure out what to do.
    """

    def test_2fa_message_points_at_browser(self) -> None:
        exc = AuthenticationError("2FA required: open the SwissID app")
        msg, code = render_cli_error(exc)
        assert code == 1
        assert "two-factor" in msg.lower() or "2fa" in msg.lower()
        # The user should be told to use the browser, not the CLI.
        assert "browser" in msg.lower() or "https" in msg.lower()

    def test_two_factor_hyphenated_message(self) -> None:
        """``two-factor`` (hyphenated) triggers the same branch as ``2FA``."""
        exc = AuthenticationError("Two-factor authentication required")
        msg, _ = render_cli_error(exc)
        assert "two-factor" in msg.lower() or "2fa" in msg.lower()

    def test_mfa_message(self) -> None:
        """``MFA`` is a synonym for 2FA and triggers the same branch."""
        exc = AuthenticationError("MFA challenge required")
        msg, _ = render_cli_error(exc)
        assert "two-factor" in msg.lower() or "2fa" in msg.lower()

    def test_anomaly_detection_message(self) -> None:
        exc = AuthenticationError("anomaly detected: verify your device")
        msg, code = render_cli_error(exc)
        assert code == 1
        assert "suspicious" in msg.lower() or "anomaly" in msg.lower() or "device" in msg.lower()
        # The user should be told to open the browser on the
        # *same* machine (anomaly detection is per-device).
        assert "browser" in msg.lower() or "https" in msg.lower()

    def test_suspicious_activity_message(self) -> None:
        """``suspicious activity`` triggers the anomaly-detection branch."""
        exc = AuthenticationError("Login blocked: suspicious activity detected")
        msg, _ = render_cli_error(exc)
        assert "suspicious" in msg.lower() or "anomaly" in msg.lower() or "device" in msg.lower()

    def test_generic_credentials_message_falls_through(self) -> None:
        """A plain wrong-password message hits the generic branch."""
        exc = AuthenticationError("invalid username or password")
        msg, _ = render_cli_error(exc)
        # Generic branch tells the user to check credentials.
        assert "POSTCARDS_USERNAME" in msg
        assert "POSTCARDS_PASSWORD" in msg


class TestQuotaExhaustedError:
    def test_includes_next_available_timestamp(self) -> None:
        when = datetime(2026, 6, 26, 0, 0, tzinfo=UTC)
        exc = QuotaExhaustedError("quota gone", next_available_at=when)
        msg, code = render_cli_error(exc)
        assert when.isoformat() in msg
        assert code == 1

    def test_handles_missing_timestamp(self) -> None:
        exc = QuotaExhaustedError("quota gone", next_available_at=None)
        msg, code = render_cli_error(exc)
        assert "unknown" in msg
        assert code == 1

    def test_message_hints_quota_command(self) -> None:
        exc = QuotaExhaustedError("quota gone")
        msg, _ = render_cli_error(exc)
        assert "quota --wait" in msg


class TestTransientBackendError:
    def test_exits_with_tempfail(self) -> None:
        # 75 (EX_TEMPFAIL) lets a cron job distinguish "retry later"
        # from "fix the config".
        exc = TransientBackendError("connection reset")
        _msg, code = render_cli_error(exc)
        assert code == 75

    def test_message_suggests_verbose_or_mock(self) -> None:
        exc = TransientBackendError("connection reset")
        msg, _code = render_cli_error(exc)
        assert "--verbose" in msg or "--backend=mock" in msg


class TestRetryExhaustedError:
    def test_includes_underlying_cause(self) -> None:
        cause = TransientBackendError("connection reset")
        exc = RetryExhaustedError(
            "send failed after 4 attempt(s): connection reset", last_exception=cause
        )
        msg, code = render_cli_error(exc)
        # ``str(exc)`` is preserved verbatim; the message text
        # comes from the retry helper's own constructor.
        assert "connection reset" in msg
        assert code == 75

    def test_includes_underlying_class_name(self) -> None:
        # Even when the underlying exception's str() is empty,
        # we surface the class name so the user has a clue.
        cause = RuntimeError("")
        exc = RetryExhaustedError("send failed after 4 attempt(s): ", last_exception=cause)
        msg, _ = render_cli_error(exc)
        assert "RuntimeError" in msg

    def test_works_without_underlying_cause(self) -> None:
        exc = RetryExhaustedError("send failed after 4 attempt(s)")
        msg, code = render_cli_error(exc)
        assert code == 75
        # Should not crash and should still mention the operation.
        assert "send failed" in msg


class TestBackendErrorFallback:
    def test_unrecognised_backend_error(self) -> None:
        exc = BackendError("something exotic")
        msg, code = render_cli_error(exc)
        assert code == 1
        assert "backend error" in msg.lower()
        assert "something exotic" in msg


class TestGenericException:
    def test_non_backend_exception_falls_through(self) -> None:
        exc = ValueError("oops")
        msg, code = render_cli_error(exc)
        assert code == 1
        assert "oops" in msg


# ---------------------------------------------------------------------------
# raise_for_backend_error
# ---------------------------------------------------------------------------


class TestRaiseForBackendError:
    @pytest.mark.parametrize(
        "exc",
        [
            AuthenticationError("bad pw"),
            QuotaExhaustedError("quota gone"),
            TransientBackendError("flap"),
            BackendError("generic"),
            ValueError("not a backend error"),
            RetryExhaustedError("send failed after 4 attempt(s)"),
        ],
    )
    def test_raises_typer_exit_with_matching_code(
        self,
        exc: BaseException,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        expected_msg, expected_code = render_cli_error(exc)
        with pytest.raises(typer.Exit) as excinfo:
            raise_for_backend_error(exc)
        assert excinfo.value.exit_code == expected_code
        # ``raise_cli_error`` echoes ``error: <message>`` to stderr
        # before raising, so we assert the substring appears in the
        # captured stderr output.
        captured = capsys.readouterr().err
        assert expected_msg in captured


# ---------------------------------------------------------------------------
# Import surface
# ---------------------------------------------------------------------------


class TestImports:
    def test_exports(self) -> None:
        from postcards.cli import backend_errors

        assert hasattr(backend_errors, "render_cli_error")
        assert hasattr(backend_errors, "raise_for_backend_error")
