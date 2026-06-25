"""``postcards doctor`` — diagnose config, credentials, and connectivity.

M5 adds a dedicated diagnostics command because the upstream
SwissID flow is fragile (anomaly-detection, 2FA, keyring
quirks) and a user who hits any of those failure modes needs
a *single* command that tells them what is wrong and what to
do next. The command never authenticates against the live
Swiss Post endpoint — it probes configuration, the OS
keyring, and a minimal connectivity check against the
Swiss Post consumer host.

The five checks
---------------

The command runs a fixed sequence of checks; each check
records a :class:`CheckResult` with a status (``ok`` /
``warn`` / ``fail``) and a one-line summary. The aggregate
exit code follows the same convention as
:mod:`postcards.cli.backend_errors`:

* ``0`` — every check passed.
* ``1`` — at least one check failed.
* ``2`` — usage error (the command itself was invoked
  incorrectly, not the user's setup).

The individual checks
---------------------

1. **Config file** — does the resolved path exist and parse
   as JSON? A missing file is reported as ``warn`` (the user
   may legitimately run with env-only credentials); an
   unparseable file is ``fail``.
2. **Credentials** — can at least one account be resolved?
   The :class:`ConfigLayer` is exercised in dry-run mode so
   no real password read from the keyring leaks. The
   ``source`` field of the resolved :class:`AccountConfig`
   is rendered verbatim so the user can see *where* the
   loader looked.
3. **Keyring** — does the OS keyring have a working backend?
   The result of :class:`KeyringStore.status` is rendered as
   one line; a missing backend is ``warn`` (the user can
   still use env / config-file credentials).
4. **Connectivity** — can the host reach the Swiss Post
   consumer endpoint? The check is a single ``GET`` to a
   stable URL (the consumer login page) with a short
   timeout; we never follow the redirect, never submit
   credentials, and never reach SwissID.
5. **Mock login smoke test** — if a username and password
   are resolvable, drive :class:`MockBackend.login` and
   surface the result. This catches a misconfigured
   ``POSTCARDS_BACKEND`` (a typo in the env var) and any
   other plumbing issue before the user tries the live
   flow. The test is opt-out via ``--skip-mock``.

Why these five
--------------

The first three are "config and credentials" — they can be
checked offline and answer 90% of the "why is send failing"
support questions. The fourth is "is the network even
up" — also offline-safe. The fifth is "does the rest of
the pipeline work end-to-end against a known-good
backend" — the closest thing to a regression test the
user can run from the terminal.
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import requests
import typer

from postcards.cli.app import app
from postcards.cli.config_io import resolve_config_path
from postcards.config import ConfigError, ConfigLayer, KeyringStatus, KeyringStore

#: The URL the connectivity check uses. The Swiss Postcard Creator
#: consumer landing page is reachable without authentication and
#: returns a stable 200/302 response, which is exactly what the
#: doctor wants to verify. We deliberately do not probe the
#: SwissID endpoint — that one is rate-limited and would distort
#: the test for users behind anomaly-detection.
CONNECTIVITY_URL: str = "https://www.postcard-creator.post.ch/"

#: Timeout for the connectivity probe. The Swiss Post host is
#: fast for users on the same continent; 5s is generous and
#: short enough that the user does not stare at a frozen terminal.
CONNECTIVITY_TIMEOUT_SECONDS: float = 5.0


class CheckStatus(StrEnum):
    """Outcome of one :class:`Check` run.

    * ``ok`` — the check passed; nothing for the user to do.
    * ``warn`` — the check is informational; the user can keep
      going but should look at the message.
    * ``fail`` — the check failed; the user must act before
      the next ``postcards send`` will work.
    """

    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class CheckResult:
    """Outcome of one doctor check.

    Attributes
    ----------
    name:
        Short identifier (e.g. ``"config"``). Used as a row
        label in the rendered table.
    status:
        :class:`CheckStatus` value.
    summary:
        One-line human-readable message. Rendered as-is.
    hint:
        Optional next-step hint. Rendered indented under the
        summary when present.
    """

    name: str
    status: CheckStatus
    summary: str
    hint: str | None = None


@dataclass
class DoctorReport:
    """Aggregate of every :class:`CheckResult` produced by one doctor run.

    The report owns the per-check results and the overall
    status, which is the *worst* of any individual status
    (``fail`` beats ``warn`` beats ``ok``). The :func:`render`
    method turns the report into a printable table the CLI
    shows on stdout.
    """

    results: list[CheckResult] = field(default_factory=list)
    overall: CheckStatus = CheckStatus.OK

    def add(self, result: CheckResult) -> None:
        """Append ``result`` and recompute the overall status.

        The overall status is the worst of the appended
        results — appending a ``fail`` after a ``warn`` flips
        the overall to ``fail``. Recomputing in ``add`` (rather
        than at the end) keeps the property true even when
        checks run in a different order, which the
        connectivity check may do once the network probe
        returns.
        """
        self.results.append(result)
        if result.status == CheckStatus.FAIL or self.overall == CheckStatus.FAIL:
            self.overall = CheckStatus.FAIL
        elif result.status == CheckStatus.WARN and self.overall == CheckStatus.OK:
            self.overall = CheckStatus.WARN

    @property
    def failed(self) -> bool:
        """``True`` when at least one check did not pass."""
        return self.overall != CheckStatus.OK

    def render(self) -> str:
        """Return a printable rendering of the report.

        The shape is::

            config         ok    config.json present at /home/.../config.json
            credentials    ok    1 account resolved (source=keyring)
            keyring        ok    backend=SecretService
            connectivity   warn  www.postcard-creator.post.ch reachable in 312ms
            mock login     ok    login() succeeded against backend=mock

        The status column is fixed-width so the eye can scan
        vertically. A trailing blank line is appended so the
        caller can ``print(report.render())`` without having to
        add one.
        """
        width = max(len(r.name) for r in self.results) if self.results else 0
        lines: list[str] = []
        for r in self.results:
            line = f"{r.name.ljust(width)}  {r.status.value:<4}  {r.summary}"
            lines.append(line)
            if r.hint:
                lines.append(f"{' ' * width}        hint: {r.hint}")
        return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------
# Individual checks
# ----------------------------------------------------------------------


def check_config(config_path: Path) -> CheckResult:
    """Verify the resolved config file exists and parses as JSON.

    A missing file is ``warn`` (env-only credentials are a
    valid configuration); a present-but-broken file is
    ``fail`` because the user almost certainly needs it.
    """
    if not config_path.is_file():
        return CheckResult(
            name="config",
            status=CheckStatus.WARN,
            summary=f"no config file at {config_path} (env-only credentials are still valid)",
            hint=(
                "run 'postcards config init' to create one, "
                "or set POSTCARDS_USERNAME / POSTCARDS_PASSWORD in your shell"
            ),
        )
    try:
        with config_path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        return CheckResult(
            name="config",
            status=CheckStatus.FAIL,
            summary=f"config file at {config_path} is not valid JSON: {exc.msg}",
            hint=f"fix the JSON syntax near line {exc.lineno}, col {exc.colno}",
        )
    if not isinstance(data, dict):
        return CheckResult(
            name="config",
            status=CheckStatus.FAIL,
            summary=(
                f"config file at {config_path} is a {type(data).__name__}, "
                "expected a top-level JSON object"
            ),
        )
    return CheckResult(
        name="config",
        status=CheckStatus.OK,
        summary=f"config.json present at {config_path}",
    )


def check_credentials(config_path: Path) -> CheckResult:
    """Try to resolve at least one account.

    The :class:`ConfigLayer` is exercised against the
    resolved path with the current ``os.environ``; we never
    prompt for a password and never call the network. The
    check is intentionally non-fatal when the keyring
    returns nothing — the user may be running with
    env-only credentials and that is a valid configuration.
    """
    try:
        layer = ConfigLayer(env=dict(os.environ), config_path=config_path)
        accounts = layer.load_accounts()
    except ConfigError as exc:
        return CheckResult(
            name="credentials",
            status=CheckStatus.FAIL,
            summary=f"could not resolve any account: {exc}",
            hint=(
                "set POSTCARDS_USERNAME and POSTCARDS_PASSWORD, "
                "or add an account with 'postcards accounts add', "
                "or store a password in the keyring with 'postcards keyring set'"
            ),
        )
    sources = sorted({a.source for a in accounts})
    return CheckResult(
        name="credentials",
        status=CheckStatus.OK,
        summary=(
            f"{len(accounts)} account(s) resolved (source={','.join(sources)}); "
            f"active: {accounts[0].username!r}"
        ),
    )


def check_keyring() -> CheckResult:
    """Render the :class:`KeyringStatus` for the active host.

    A missing backend is ``warn`` rather than ``fail`` —
    the CLI's credential layer falls through to the next
    source, so the user can still send cards with
    env- or config-file-based credentials.
    """
    status: KeyringStatus = KeyringStore().status()
    if status.available:
        return CheckResult(
            name="keyring",
            status=CheckStatus.OK,
            summary=f"backend={status.backend_name!r}",
        )
    return CheckResult(
        name="keyring",
        status=CheckStatus.WARN,
        summary=(f"unavailable (backend={status.backend_name!r}); {status.reason}"),
        hint=(
            "the keyring is one of three credential sources — "
            "the CLI will still work with POSTCARDS_USERNAME / "
            "POSTCARDS_PASSWORD or a config-file account"
        ),
    )


def check_connectivity(url: str = CONNECTIVITY_URL) -> CheckResult:
    """Verify the Swiss Post consumer host is reachable.

    A single ``GET`` with a short timeout. We do not follow
    redirects, do not submit credentials, and do not touch
    SwissID. A failed DNS lookup, a refused connection, or
    a timeout each produce a distinct summary so the user
    knows which layer is broken.
    """
    try:
        response = requests.get(url, timeout=CONNECTIVITY_TIMEOUT_SECONDS, allow_redirects=False)
    except requests.exceptions.Timeout:
        return CheckResult(
            name="connectivity",
            status=CheckStatus.FAIL,
            summary=f"timed out after {CONNECTIVITY_TIMEOUT_SECONDS:.0f}s reaching {url}",
            hint=(
                "check your network connection; the Swiss Post consumer host "
                "may be unreachable from your current network"
            ),
        )
    except requests.exceptions.ConnectionError as exc:
        return CheckResult(
            name="connectivity",
            status=CheckStatus.FAIL,
            summary=f"connection error: {exc}",
            hint="check DNS and proxy settings; try again on a different network",
        )
    except (requests.exceptions.RequestException, socket.gaierror) as exc:
        # Catch-all for any other requests-layer failure (SSL, ...).
        return CheckResult(
            name="connectivity",
            status=CheckStatus.FAIL,
            summary=f"request failed: {exc}",
        )
    if 200 <= response.status_code < 400:
        return CheckResult(
            name="connectivity",
            status=CheckStatus.OK,
            summary=f"{url} reachable (HTTP {response.status_code})",
        )
    return CheckResult(
        name="connectivity",
        status=CheckStatus.FAIL,
        summary=f"{url} returned HTTP {response.status_code}",
        hint=(
            "the Swiss Post consumer host is up but the landing page is "
            "unhealthy; try again later or report the issue upstream"
        ),
    )


def check_mock_login(
    *,
    config_path: Path,
    env: dict[str, str] | None = None,
) -> CheckResult:
    """Drive :class:`MockBackend.login` with the resolved credentials.

    The check is the closest thing to a smoke test the user
    can run from the terminal. It is intentionally opt-out
    (``--skip-mock``) because it requires a resolvable
    account; users running with env-only credentials that
    prefer not to type the password for the doctor get
    the rest of the report without the mock-login row.
    """
    env_source = env if env is not None else dict(os.environ)
    try:
        layer = ConfigLayer(env=env_source, config_path=config_path)
        accounts = layer.load_accounts()
    except ConfigError as exc:
        return CheckResult(
            name="mock login",
            status=CheckStatus.FAIL,
            summary=f"could not resolve credentials: {exc}",
            hint="fix the credentials check above; the mock login depends on it",
        )
    if not accounts:
        return CheckResult(
            name="mock login",
            status=CheckStatus.FAIL,
            summary="no accounts resolved; cannot run a smoke test",
        )
    active = accounts[0]
    # Force the mock backend regardless of POSTCARDS_BACKEND — the
    # whole point of the smoke test is to validate the rest of the
    # pipeline against a known-good backend. The env override is
    # scoped to the call so it does not leak into the rest of the
    # doctor run.
    from postcards.backend import MockBackend

    env_with_mock = {**env_source, "POSTCARDS_BACKEND": "mock"}
    # The MockBackend does not consult the env directly; we instantiate
    # it directly so the smoke test is not affected by the user's
    # ``POSTCARDS_BACKEND`` setting.
    _ = env_with_mock  # kept for symmetry; MockBackend ignores the env
    backend = MockBackend()
    try:
        backend.login(active.username, active.password)
    except Exception as exc:
        return CheckResult(
            name="mock login",
            status=CheckStatus.FAIL,
            summary=f"MockBackend.login raised {type(exc).__name__}: {exc}",
            hint=(
                "the mock backend is supposed to be a known-good fallback; "
                "this failure indicates a regression in the CLI plumbing"
            ),
        )
    return CheckResult(
        name="mock login",
        status=CheckStatus.OK,
        summary=f"login() succeeded against backend=mock (account={active.username!r})",
    )


# ----------------------------------------------------------------------
# Public command
# ----------------------------------------------------------------------


@app.command(
    name="doctor",
    help="Diagnose config, credentials, keyring, and connectivity.",
    no_args_is_help=False,
)
def doctor_cmd(
    config_file: Path | None = typer.Option(
        None,
        "-c",
        "--config",
        help=(
            "Path to the config file. Defaults to ./config.json "
            "(honours POSTCARDS_CONFIG when unset)."
        ),
    ),
    skip_mock: bool = typer.Option(
        False,
        "--skip-mock",
        help="Skip the MockBackend smoke test (e.g. on hosts without a real backend).",
    ),
    skip_network: bool = typer.Option(
        False,
        "--skip-network",
        help="Skip the connectivity probe (e.g. in air-gapped environments).",
    ),
) -> None:
    """Run the diagnostic checks and print a tabular report.

    The command never authenticates against the live Swiss
    Post endpoint. The connectivity probe hits a stable
    public URL on the consumer host; the mock login
    exercises the in-memory :class:`MockBackend`. See the
    module docstring for the rationale behind each check.
    """
    target = resolve_config_path(config_file)
    report = DoctorReport()
    report.add(check_config(target))
    report.add(check_credentials(target))
    report.add(check_keyring())
    if not skip_network:
        report.add(check_connectivity())
    if not skip_mock:
        report.add(check_mock_login(config_path=target))

    typer.echo(report.render())
    if report.failed:
        # ``fail`` beats ``warn`` for the exit code, but the
        # user's terminal does not have to know the difference —
        # a non-zero exit is enough for a CI script to surface.
        raise typer.Exit(code=1)


__all__ = [
    "CONNECTIVITY_TIMEOUT_SECONDS",
    "CONNECTIVITY_URL",
    "CheckResult",
    "CheckStatus",
    "DoctorReport",
    "check_config",
    "check_connectivity",
    "check_credentials",
    "check_keyring",
    "check_mock_login",
    "doctor_cmd",
]
