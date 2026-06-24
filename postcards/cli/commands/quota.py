"""``postcards quota`` — print the free-card quota for an account.

This is a thin wrapper over :class:`postcards.backend.registry.select_backend`
followed by :meth:`PostcardBackend.quota`. It honours the same
envvar / config-file backend selection rules as the rest of
the CLI and exits non-zero when the quota is exhausted.

M2 puts ``quota`` on its own command (rather than only showing
it in ``status``) because it is the single most frequent
question the user has: "can I send a card right now?" — and
it should not require a config file to answer.
"""

from __future__ import annotations

import typer

from postcards.backend import QuotaInfo, select_backend
from postcards.backend.registry import BackendNotAvailableError
from postcards.cli.app import app
from postcards.cli.errors import raise_cli_error
from postcards.cli.options import backend_option, password_option, username_option


@app.command(
    name="quota",
    help="Show the free-card quota for the given (or active) account.",
    no_args_is_help=True,
)
def quota_cmd(
    username: str | None = username_option(),
    password: str | None = password_option(),
    backend: str | None = backend_option(),
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
        import os

        password = os.environ.get("POSTCARDS_PASSWORD") or ""
    if not password:
        raise_cli_error(
            "no password supplied (pass --password, set POSTCARDS_PASSWORD, "
            "or use the multi-account config file)",
            exit_code=2,
        )

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
    except Exception as exc:
        raise_cli_error(f"login failed: {exc}")

    info: QuotaInfo = instance.quota()
    if info.available:
        typer.echo("free postcard available now")
    else:
        when = info.next_available_at.isoformat() if info.next_available_at else "unknown"
        typer.echo(f"no free postcard; next available at {when}")
    typer.echo(f"retention: {info.retention_days} day(s)")
    typer.echo(f"backend: {instance.name}")


__all__ = ["quota_cmd"]
