"""``postcards quota`` — print the free-card quota for an account.

This is a thin wrapper over :class:`postcards.backend.registry.select_backend`
followed by :meth:`PostcardBackend.quota`. It honours the same
envvar / config-file backend selection rules as the rest of
the CLI and exits non-zero when the quota is exhausted.

M2 put ``quota`` on its own command (rather than only showing
it in ``status``) because it is the single most frequent
question the user has: "can I send a card right now?" — and
it should not require a config file to answer.

M5 additions
-------------

* ``--no-fail`` — exit 0 even when the quota is exhausted, so a
  shell script can use ``postcards quota --no-fail`` as a gate.
* ``--wait`` — block until the next quota window opens
  (``max_wait`` seconds), then re-check and exit. Useful for a
  shell loop or for CI smoke tests.
* Clearer error messages — the M5
  :class:`postcards.backend.exceptions.QuotaExhaustedError` carries
  the next-available timestamp; the CLI prints it and tells the
  user about ``--wait`` so they do not have to read docs to
  understand the failure.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime

import typer

from postcards import __version__ as _postcards_version
from postcards.backend import QuotaInfo, select_backend
from postcards.backend.exceptions import QuotaExhaustedError
from postcards.backend.registry import BackendNotAvailableError
from postcards.cli.app import app
from postcards.cli.backend_errors import raise_for_backend_error
from postcards.cli.errors import raise_cli_error
from postcards.cli.options import backend_option, password_option, username_option

#: Importing ``__version__`` keeps the build graph honest (the
#: CLI uses it via ``--version``); the import itself is the
#: coupling. Discarded assignment to silence unused-binding.
_ = _postcards_version


# ------------------------------------------------------------------
# ``--wait`` loop
# ------------------------------------------------------------------


def _wait_for_quota(
    backend_name: str,
    username: str,
    password: str,
    *,
    max_wait: float,
    poll_interval: float = 30.0,
) -> QuotaInfo:
    """Poll the backend until the quota opens or ``max_wait`` elapses.

    Returns the most recent :class:`QuotaInfo`. The function
    raises :class:`QuotaExhaustedError` when ``max_wait`` elapses
    with the quota still closed so the caller can show the same
    actionable message the eager path produces.
    """
    deadline = time.monotonic() + max_wait
    instance = select_backend(env={"POSTCARDS_BACKEND": backend_name} if backend_name else None)
    while True:
        try:
            instance.login(username, password)
        except NotImplementedError as exc:
            raise_cli_error(
                f"backend {instance.name!r} does not support unattended login: {exc}. "
                "Use --backend=mock to exercise the path against the in-memory mock."
            )
        except QuotaExhaustedError:
            # A quota-exhausted answer from ``login`` is unusual
            # (the upstream returns it from the quota endpoint,
            # not from auth), but if it happens we treat it
            # the same as a quota endpoint response below.
            info = QuotaInfo(
                available=False,
                next_available_at=None,
                retention_days=1,
            )
        else:
            try:
                info = instance.quota()
            except QuotaExhaustedError as exc:
                info = QuotaInfo(
                    available=False,
                    next_available_at=exc.next_available_at,
                    retention_days=exc.retention_days,
                )
        if info.available:
            return info
        if time.monotonic() >= deadline:
            raise QuotaExhaustedError(
                f"quota still exhausted after waiting {max_wait:.0f}s",
                next_available_at=info.next_available_at,
                retention_days=info.retention_days,
            )
        sleep_for = min(poll_interval, max(1.0, deadline - time.monotonic()))
        typer.echo(
            f"  quota exhausted; sleeping {sleep_for:.0f}s "
            f"(next attempt at {datetime.now(UTC).isoformat(timespec='seconds')})"
        )
        time.sleep(sleep_for)


# ------------------------------------------------------------------
# Public command
# ------------------------------------------------------------------


@app.command(
    name="quota",
    help="Show the free-card quota for the given (or active) account.",
    no_args_is_help=True,
)
def quota_cmd(
    username: str | None = username_option(),
    password: str | None = password_option(),
    backend: str | None = backend_option(),
    wait: bool = typer.Option(
        False,
        "--wait",
        help=(
            "Block until the quota opens (or --max-wait elapses). "
            "Use with --max-wait to bound the wait; default cap is 24h."
        ),
    ),
    max_wait: float = typer.Option(
        24 * 3600.0,
        "--max-wait",
        help="Maximum seconds to wait when --wait is set. Default: 86400 (24h).",
        min=1.0,
    ),
    poll: float = typer.Option(
        30.0,
        "--poll",
        help="Seconds between quota checks while --wait is set. Default: 30.",
        min=0.1,
    ),
    no_fail: bool = typer.Option(
        False,
        "--no-fail",
        help="Exit 0 even when the quota is exhausted. Useful for shell scripts.",
    ),
) -> None:
    """Print the quota for ``username`` (or the active account).

    Without a username, the command falls back to
    ``POSTCARDS_USERNAME`` and otherwise refuses to guess. The
    backend defaults to ``POSTCARDS_BACKEND`` or
    ``mock`` when no backend is configured (so the user can
    test the path end-to-end without burning a daily quota).
    """
    if not username:
        raise_cli_error(
            "no username supplied (pass --username or set POSTCARDS_USERNAME)",
            exit_code=2,
        )
    if not password:
        # The CLI does not read ``POSTCARDS_PASSWORD`` directly
        # here (it goes through the ``ConfigLayer``); for the
        # bare-quota path we let the user pass it on the command
        # line or set the env var explicitly.
        password = os.environ.get("POSTCARDS_PASSWORD") or ""
    if not password:
        raise_cli_error(
            "no password supplied (pass --password, set POSTCARDS_PASSWORD, "
            "or use the multi-account config file)",
            exit_code=2,
        )

    if wait:
        info = _wait_for_quota(
            backend or "",
            username,
            password,
            max_wait=max_wait,
            poll_interval=poll,
        )
    else:
        try:
            instance = select_backend(env={"POSTCARDS_BACKEND": backend} if backend else None)
        except BackendNotAvailableError as exc:
            raise_cli_error(str(exc))

        try:
            instance.login(username, password)
        except NotImplementedError as exc:
            # The vendored shim raises ``NotImplementedError`` from
            # ``Token.has_valid_credentials`` because the upstream
            # SwissID login cannot run unattended. The mock backend
            # is the supported way to exercise the quota path in
            # CI / dev.
            raise_cli_error(
                f"backend {instance.name!r} does not support unattended login: {exc}. "
                "Use --backend=mock to exercise the path against the in-memory mock."
            )
        except QuotaExhaustedError as exc:
            # ``login`` itself reports the quota in some upstream
            # versions. Bubble the same structured payload to the
            # caller.
            info = QuotaInfo(
                available=False,
                next_available_at=exc.next_available_at,
                retention_days=exc.retention_days,
            )
        except Exception as exc:
            # ``AuthenticationError``, ``TransientBackendError``,
            # ``RetryExhaustedError`` and any future backend-level
            # exception flow through the centralised translator
            # so the user gets a consistent hint and exit code.
            raise_for_backend_error(exc)
        else:
            try:
                info = instance.quota()
            except QuotaExhaustedError as exc:
                info = QuotaInfo(
                    available=False,
                    next_available_at=exc.next_available_at,
                    retention_days=exc.retention_days,
                )
            except Exception as exc:
                # Same centralised translator as the login branch
                # above — a transient 5xx or a retry-exhausted
                # quota call ends with the same actionable message.
                raise_for_backend_error(exc)

    backend_name = backend or os.environ.get("POSTCARDS_BACKEND") or "swissid"
    if info.available:
        typer.echo("free postcard available now")
    else:
        when = info.next_available_at.isoformat() if info.next_available_at else "unknown"
        typer.echo(f"no free postcard; next available at {when}")
        if not wait:
            typer.echo("hint: pass --wait to block until the quota opens")
    typer.echo(f"retention: {info.retention_days} day(s)")
    typer.echo(f"backend: {backend_name}")

    if not info.available and not no_fail and not wait:
        # Exit non-zero so shell scripts see the exhaustion as a
        # failure by default; ``--no-fail`` opts out.
        raise typer.Exit(code=1)


__all__ = ["quota_cmd"]
