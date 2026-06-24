"""In-memory :class:`MockBackend` implementation.

The mock is the **single source of truth for the backend's contract**
in tests. It records every ``login`` / ``preview`` / ``send`` call so
tests can assert what the CLI would have done without ever touching
the network.

The mock is intentionally generous: every ``login`` succeeds, every
quota is available, every send is recorded. Tests that need a
failure mode mutate ``self.quota_info`` or ``self.should_fail_login``
before invoking the CLI.

The class is also exported as a CLI fallback via
``POSTCARDS_BACKEND=mock`` — useful for developers exercising the
CLI surface without burning a daily quota.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from postcards.backend.base import (
    PostcardBackend,
    PreviewInfo,
    QuotaInfo,
    SendResult,
)

if TYPE_CHECKING:
    from postcards.models.postcard import Postcard


@dataclass
class MockBackend:
    """In-memory backend that records every operation.

    The dataclass form keeps the mock ergonomic in tests: a single
    ``MockBackend()`` instance exposes ``sent``, ``previews`` and
    ``logins`` as plain lists the test can inspect. There is no need
    for ``unittest.mock.MagicMock`` plumbing.
    """

    name: str = "mock"
    quota_info: QuotaInfo = field(
        default_factory=lambda: QuotaInfo(available=True, retention_days=1)
    )

    # Records — declared here with default factories so the dataclass
    # generates a sensible ``__init__`` and the lists are mutable.
    sent: list[SendResult] = field(default_factory=list)
    previews: list[PreviewInfo] = field(default_factory=list)
    logins: list[tuple[str, str]] = field(default_factory=list)

    # Failure injection knobs (off by default; tests set them to exercise
    # the CLI's error-handling paths).
    should_fail_login: bool = False
    login_error: Exception | None = None

    # ------------------------------------------------------------------
    # PostcardBackend protocol implementation
    # ------------------------------------------------------------------

    def login(self, username: str, password: str) -> None:
        """Record the login attempt; raise when ``should_fail_login`` is set."""
        self.logins.append((username, password))
        if self.should_fail_login:
            if self.login_error is not None:
                raise self.login_error
            raise RuntimeError("MockBackend configured to fail login")

    def quota(self) -> QuotaInfo:
        """Return the configured :class:`QuotaInfo`."""
        return self.quota_info

    def preview(self, card: Postcard) -> PreviewInfo:
        """Record the preview and return a default :class:`PreviewInfo`."""
        info = PreviewInfo(postcard=card)
        self.previews.append(info)
        return info

    def send(self, card: Postcard, *, mock: bool = False) -> SendResult:
        """Record the send and return a :class:`SendResult`.

        The mock never raises on invalid cards — validation is the
        caller's responsibility. ``confirmation`` is a per-call
        counter prefixed with ``mock-`` so tests can correlate the
        result with the record.
        """
        result = SendResult(
            backend=self.name,
            account=self._last_account(),
            sent_at=datetime.now(UTC),
            mock=mock,
            postcard=card,
            confirmation=f"mock-{len(self.sent)}",
        )
        self.sent.append(result)
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _last_account(self) -> str:
        """Return the most recent ``login`` username, or ``''``."""
        if not self.logins:
            return ""
        return self.logins[-1][0]


# A static type-checker assertion: ``MockBackend`` implements the
# ``PostcardBackend`` protocol. Discarded assignment to ``_`` is the
# idiomatic way to spell "the relationship holds".
_: PostcardBackend = MockBackend()


__all__ = ["MockBackend"]
