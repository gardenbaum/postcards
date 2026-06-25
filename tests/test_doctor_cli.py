"""Tests for ``postcards doctor`` and the individual check functions.

The doctor is the M5 user-facing surface for "what is wrong
with my setup". The tests cover the five checks in
isolation (so a regression in one check does not mask the
others) and end-to-end through the Typer app (so the CLI
shape is exercised). All network calls are mocked; the
real Swiss Post endpoint is never reached.

The mock-login test
-------------------

The mock-login check is the one place the doctor actually
exercises a backend. It uses :class:`MockBackend` so the
assertion is hermetic — a real :class:`SwissIdConsumerBackend`
would raise ``NotImplementedError`` (see the swissid.py
module docstring) and the test would fail spuriously.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import requests

from postcards.cli import run as cli_run
from postcards.cli.commands import doctor as doctor_module
from postcards.cli.commands.doctor import (
    CONNECTIVITY_URL,
    CheckResult,
    CheckStatus,
    DoctorReport,
    check_config,
    check_connectivity,
    check_credentials,
    check_keyring,
    check_mock_login,
)

# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    """A valid config file with one account."""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "recipient": {
                    "firstname": "Hans",
                    "lastname": "Muster",
                    "street": "Bahnhofstrasse 1",
                    "zipcode": "8000",
                    "city": "Zurich",
                },
                "accounts": [{"username": "alice", "password": "alice-pw"}],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Strip POSTCARDS_* env vars so tests see a clean baseline.

    A developer's shell can have POSTCARDS_USERNAME,
    POSTCARDS_BACKEND, ... set, which would leak into the
    doctor and produce non-deterministic test results.
    """
    for var in (
        "POSTCARDS_USERNAME",
        "POSTCARDS_PASSWORD",
        "POSTCARDS_BACKEND",
        "POSTCARDS_KEY",
        "POSTCARDS_CONFIG",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


# ----------------------------------------------------------------------
# check_config
# ----------------------------------------------------------------------


def test_check_config_passes_for_valid_file(config_file: Path) -> None:
    """A present, parseable JSON config reports ``ok``."""
    result = check_config(config_file)
    assert result.status == CheckStatus.OK
    assert str(config_file) in result.summary


def test_check_config_warns_for_missing_file(tmp_path: Path) -> None:
    """A missing config is a ``warn`` (env-only credentials are valid)."""
    result = check_config(tmp_path / "does-not-exist.json")
    assert result.status == CheckStatus.WARN
    assert "env-only" in result.summary


def test_check_config_fails_for_invalid_json(tmp_path: Path) -> None:
    """An unparseable config is a ``fail`` (the user needs to fix it)."""
    path = tmp_path / "broken.json"
    path.write_text("{not valid json", encoding="utf-8")
    result = check_config(path)
    assert result.status == CheckStatus.FAIL
    assert "JSON" in result.summary


def test_check_config_fails_for_non_object_root(tmp_path: Path) -> None:
    """A config whose top-level value is not a dict is a ``fail``."""
    path = tmp_path / "array.json"
    path.write_text("[]", encoding="utf-8")
    result = check_config(path)
    assert result.status == CheckStatus.FAIL
    assert "object" in result.summary


# ----------------------------------------------------------------------
# check_credentials
# ----------------------------------------------------------------------


def test_check_credentials_resolves_account_from_config(config_file: Path, clean_env: None) -> None:
    """An account in the config file is resolved and reported."""
    result = check_credentials(config_file)
    assert result.status == CheckStatus.OK
    assert "alice" in result.summary
    assert "config_file" in result.summary


def test_check_credentials_uses_env_when_set(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``POSTCARDS_USERNAME`` + ``POSTCARDS_PASSWORD`` resolve to ``source=env``."""
    monkeypatch.setenv("POSTCARDS_USERNAME", "alice")
    monkeypatch.setenv("POSTCARDS_PASSWORD", "from-env")
    result = check_credentials(config_file)
    assert result.status == CheckStatus.OK
    assert "env" in result.summary


def test_check_credentials_fails_when_nothing_resolves(tmp_path: Path, clean_env: None) -> None:
    """With no env, no keyring, and no config-file accounts, the check fails."""
    result = check_credentials(tmp_path / "config.json")
    assert result.status == CheckStatus.FAIL
    assert "could not resolve" in result.summary
    assert result.hint is not None
    assert "POSTCARDS_USERNAME" in result.hint


# ----------------------------------------------------------------------
# check_keyring
# ----------------------------------------------------------------------


def test_check_keyring_reports_when_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the keyring library is missing, the check reports ``warn``."""
    import postcards.config.keyring as km

    original = km.importlib.import_module

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "keyring":
            raise ImportError("forced by test")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(km.importlib, "import_module", fake_import)
    result = check_keyring()
    assert result.status == CheckStatus.WARN
    assert "keyring" in result.summary.lower()
    assert result.hint is not None


def test_check_keyring_reports_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the keyring resolves a backend, the check reports ``ok``."""
    import postcards.config.keyring as km

    class FakeModule:
        class _Backend:
            name = "TestBackend"

        @staticmethod
        def get_keyring() -> Any:
            return FakeModule._Backend()

    original = km.importlib.import_module

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "keyring":
            return FakeModule
        return original(name, *args, **kwargs)

    monkeypatch.setattr(km.importlib, "import_module", fake_import)
    result = check_keyring()
    assert result.status == CheckStatus.OK
    assert "TestBackend" in result.summary


# ----------------------------------------------------------------------
# check_connectivity
# ----------------------------------------------------------------------


def test_check_connectivity_ok_on_2xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 2xx response is reported as ``ok``."""
    response = _mock_response(200)

    def fake_get(*args: Any, **kwargs: Any) -> Any:
        return response

    monkeypatch.setattr(doctor_module.requests, "get", fake_get)
    result = check_connectivity()
    assert result.status == CheckStatus.OK
    assert "200" in result.summary


def test_check_connectivity_ok_on_3xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 3xx response is reported as ``ok`` (the upstream is reachable)."""
    response = _mock_response(302)

    def fake_get(*args: Any, **kwargs: Any) -> Any:
        return response

    monkeypatch.setattr(doctor_module.requests, "get", fake_get)
    result = check_connectivity()
    assert result.status == CheckStatus.OK


def test_check_connectivity_fail_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A request timeout is reported as ``fail``."""

    def fake_get(*args: Any, **kwargs: Any) -> Any:
        raise requests.exceptions.Timeout("timed out")

    monkeypatch.setattr(doctor_module.requests, "get", fake_get)
    result = check_connectivity()
    assert result.status == CheckStatus.FAIL
    assert "timed out" in result.summary


def test_check_connectivity_fail_on_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused connection is reported as ``fail``."""

    def fake_get(*args: Any, **kwargs: Any) -> Any:
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(doctor_module.requests, "get", fake_get)
    result = check_connectivity()
    assert result.status == CheckStatus.FAIL
    assert "connection error" in result.summary


def test_check_connectivity_fail_on_5xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 5xx response is reported as ``fail`` (the upstream is unhealthy)."""
    response = _mock_response(503)

    def fake_get(*args: Any, **kwargs: Any) -> Any:
        return response

    monkeypatch.setattr(doctor_module.requests, "get", fake_get)
    result = check_connectivity()
    assert result.status == CheckStatus.FAIL
    assert "503" in result.summary


def test_check_connectivity_uses_constant_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe uses the module-level ``CONNECTIVITY_URL`` constant."""
    seen_urls: list[str] = []

    def fake_get(url: str, *args: Any, **kwargs: Any) -> Any:
        seen_urls.append(url)
        return _mock_response(200)

    monkeypatch.setattr(doctor_module.requests, "get", fake_get)
    check_connectivity()
    assert seen_urls == [CONNECTIVITY_URL]


# ----------------------------------------------------------------------
# check_mock_login
# ----------------------------------------------------------------------


def test_check_mock_login_succeeds_against_mock(config_file: Path, clean_env: None) -> None:
    """The mock login exercises :class:`MockBackend` and reports success."""
    result = check_mock_login(config_path=config_file)
    assert result.status == CheckStatus.OK
    assert "mock" in result.summary
    assert "alice" in result.summary


def test_check_mock_login_fails_when_no_credentials(tmp_path: Path, clean_env: None) -> None:
    """The mock login fails with a clear message when no account resolves."""
    result = check_mock_login(config_path=tmp_path / "no-config.json")
    assert result.status == CheckStatus.FAIL
    assert "credentials" in result.summary or "no accounts" in result.summary


def test_check_mock_login_fails_on_injected_exception(
    config_file: Path, clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A backend that raises on login produces a ``fail`` with the exception name."""
    from postcards.backend import MockBackend

    original_login = MockBackend.login

    def broken_login(self: MockBackend, username: str, password: str) -> None:
        raise RuntimeError("backend plumbing regression")

    monkeypatch.setattr(MockBackend, "login", broken_login)
    try:
        result = check_mock_login(config_path=config_file)
        assert result.status == CheckStatus.FAIL
        assert "RuntimeError" in result.summary
        assert "backend plumbing regression" in result.summary
    finally:
        monkeypatch.setattr(MockBackend, "login", original_login)


# ----------------------------------------------------------------------
# DoctorReport
# ----------------------------------------------------------------------


def test_doctor_report_add_computes_worst_status() -> None:
    """``add`` keeps the overall status as the worst of any appended check."""
    report = DoctorReport()
    assert report.overall == CheckStatus.OK
    report.add(CheckResult("a", CheckStatus.OK, ""))
    assert report.overall == CheckStatus.OK
    report.add(CheckResult("b", CheckStatus.WARN, ""))
    assert report.overall == CheckStatus.WARN
    report.add(CheckResult("c", CheckStatus.FAIL, ""))
    assert report.overall == CheckStatus.FAIL
    # Subsequent OKs do not lower the overall.
    report.add(CheckResult("d", CheckStatus.OK, ""))
    assert report.overall == CheckStatus.FAIL


def test_doctor_report_failed_property() -> None:
    """``failed`` is ``True`` iff the overall is not ``ok``."""
    report = DoctorReport()
    assert not report.failed
    report.add(CheckResult("a", CheckStatus.WARN, ""))
    assert report.failed
    report.add(CheckResult("b", CheckStatus.FAIL, ""))
    assert report.failed


def test_doctor_report_render_includes_all_rows() -> None:
    """``render`` includes every appended result, with hints indented."""
    report = DoctorReport()
    report.add(CheckResult("a", CheckStatus.OK, "all good"))
    report.add(CheckResult("b", CheckStatus.FAIL, "broken", hint="fix it"))
    text = report.render()
    assert "a" in text
    assert "b" in text
    assert "all good" in text
    assert "broken" in text
    assert "fix it" in text
    # The hint line is indented under the summary.
    assert "hint:" in text


# ----------------------------------------------------------------------
# doctor_cmd (end-to-end through the Typer app)
# ----------------------------------------------------------------------


def test_doctor_cmd_runs_all_checks(
    config_file: Path, monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """``postcards doctor`` runs every check and renders the report."""
    monkeypatch.setattr(doctor_module.requests, "get", lambda *a, **k: _mock_response(200))
    result = cli_run(["doctor", "-c", str(config_file)])
    # The mock login and config + credentials are all ok, the
    # connectivity is ok because we mocked the request, so the
    # command should exit 0.
    output = result.output
    assert "config" in output
    assert "credentials" in output
    assert "keyring" in output
    assert "connectivity" in output
    assert "mock login" in output


def test_doctor_cmd_exits_nonzero_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """A failed check makes the command exit 1."""
    # No config file, no env, no keyring → at least one check fails.
    monkeypatch.setattr(doctor_module.requests, "get", lambda *a, **k: _mock_response(200))
    result = cli_run(
        [
            "doctor",
            "-c",
            str(tmp_path / "missing.json"),
            "--skip-mock",
        ]
    )
    assert result.exit_code == 1
    assert "fail" in result.output.lower() or "warn" in result.output.lower()


def test_doctor_cmd_skip_network(
    config_file: Path, monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """``--skip-network`` does not call the connectivity probe."""
    calls: list[str] = []

    def fake_get(*args: Any, **kwargs: Any) -> Any:
        calls.append("called")
        return _mock_response(200)

    monkeypatch.setattr(doctor_module.requests, "get", fake_get)
    result = cli_run(
        [
            "doctor",
            "-c",
            str(config_file),
            "--skip-network",
            "--skip-mock",
        ]
    )
    assert calls == []  # connectivity was skipped
    # The output should not mention connectivity at all.
    assert "connectivity" not in result.output


def test_doctor_cmd_skip_mock(
    config_file: Path, monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """``--skip-mock`` does not run the smoke test."""
    monkeypatch.setattr(doctor_module.requests, "get", lambda *a, **k: _mock_response(200))
    result = cli_run(
        [
            "doctor",
            "-c",
            str(config_file),
            "--skip-mock",
        ]
    )
    assert "mock login" not in result.output


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _mock_response(status_code: int) -> Any:
    """A minimal stand-in for ``requests.Response``."""
    response = requests.models.Response()
    response.status_code = status_code
    return response
