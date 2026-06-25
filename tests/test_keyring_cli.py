"""Tests for the ``postcards keyring {set,get,delete,list,status}`` CLI.

These tests drive the Typer app through
:func:`postcards.cli.runner.run` — the same seam the
production entry point uses — so the assertions are
representative of what a user sees at the terminal. The
``set`` / ``get`` / ``delete`` subcommands are exercised
against a hand-rolled in-memory backend that the test
injects via :func:`unittest.mock.patch`; ``status`` and
``list`` are exercised against the real keyring
library (with a worst-case ``status`` test that simulates
"no backend" via an ``import_module`` patch).
"""

from __future__ import annotations

from typing import Any

import pytest
from keyring.errors import PasswordDeleteError

from postcards.cli import run as cli_run
from postcards.cli.commands import keyring as keyring_module
from postcards.config import KeyringStore

# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


class FakeBackend:
    """A minimal in-memory keyring backend.

    The fake implements the three-method protocol the
    :class:`KeyringStore` uses. ``raises`` lets a test inject
    a backend that fails every call so the error-mapping
    branches in :class:`KeyringStore` get exercised.
    """

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}
        self.calls: list[tuple[str, str, str | None]] = []
        self.raises: Exception | None = None

    def _maybe_raise(self) -> None:
        if self.raises is not None:
            raise self.raises

    def get_password(self, service: str, username: str) -> str | None:
        self.calls.append((service, username, None))
        self._maybe_raise()
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.calls.append((service, username, password))
        self._maybe_raise()
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self._maybe_raise()
        if (service, username) not in self.store:
            raise PasswordDeleteError(f"password for {username!r} not found")
        del self.store[(service, username)]


@pytest.fixture
def fake_backend() -> FakeBackend:
    """A fresh fake backend per test."""
    return FakeBackend()


@pytest.fixture
def patched_store(monkeypatch: pytest.MonkeyPatch, fake_backend: FakeBackend) -> FakeBackend:
    """Patch ``_make_store`` so the CLI uses the fake backend.

    Yields the backend so the test can assert on the
    recorded calls and the stored values.
    """
    monkeypatch.setattr(
        keyring_module,
        "_make_store",
        lambda: KeyringStore(backend=fake_backend),
    )
    return fake_backend


def _invoke(*args: str) -> Any:
    return cli_run(list(args))


# ----------------------------------------------------------------------
# set
# ----------------------------------------------------------------------


def test_keyring_set_stores_password(patched_store: FakeBackend) -> None:
    """``keyring set alice --password pw`` writes to the backend."""
    result = _invoke("keyring", "set", "alice", "--password", "alice-pw-123")
    assert result.exit_code == 0, result.output
    assert "alice" in result.output
    assert "12" in result.output  # password length (alice-pw-123 is 12 chars)
    assert "alice-pw-123" not in result.output  # never echoed
    # The fake backend recorded the write.
    assert ("postcards", "alice", "alice-pw-123") in patched_store.calls


def test_keyring_set_rejects_empty_password(patched_store: FakeBackend) -> None:
    """An empty password is a usage error (exit 2)."""
    result = _invoke("keyring", "set", "alice", "--password", "")
    assert result.exit_code == 2
    assert "password" in result.output


def test_keyring_set_rejects_empty_username(patched_store: FakeBackend) -> None:
    """An empty username is a usage error (exit 2)."""
    result = _invoke("keyring", "set", "", "--password", "pw")
    assert result.exit_code == 2
    assert "username" in result.output


def test_keyring_set_wraps_backend_errors(
    monkeypatch: pytest.MonkeyPatch, fake_backend: FakeBackend
) -> None:
    """A backend that raises surfaces as a CLI error."""
    from keyring.errors import KeyringError

    fake_backend.raises = KeyringError("locked keyring")
    monkeypatch.setattr(
        keyring_module,
        "_make_store",
        lambda: KeyringStore(backend=fake_backend),
    )
    result = _invoke("keyring", "set", "alice", "--password", "pw")
    assert result.exit_code != 0
    assert "locked keyring" in result.output


# ----------------------------------------------------------------------
# get
# ----------------------------------------------------------------------


def test_keyring_get_reports_present(patched_store: FakeBackend) -> None:
    """``keyring get`` reports ``present`` when a value is stored."""
    patched_store.store[("postcards", "alice")] = "secret-pw-123"
    result = _invoke("keyring", "get", "alice")
    assert result.exit_code == 0
    assert "present" in result.output
    assert "13" in result.output  # length, not the value


def test_keyring_get_reports_absent(patched_store: FakeBackend) -> None:
    """``keyring get`` reports ``absent`` when nothing is stored."""
    result = _invoke("keyring", "get", "nobody")
    assert result.exit_code == 0
    assert "absent" in result.output


def test_keyring_get_never_prints_the_password(patched_store: FakeBackend) -> None:
    """``keyring get`` only reports presence/length, never the plaintext."""
    patched_store.store[("postcards", "alice")] = "super-secret-pw"
    result = _invoke("keyring", "get", "alice")
    assert "super-secret-pw" not in result.output


def test_keyring_get_rejects_empty_username(patched_store: FakeBackend) -> None:
    """An empty username is a usage error (exit 2)."""
    result = _invoke("keyring", "get", "")
    assert result.exit_code == 2
    assert "username" in result.output


# ----------------------------------------------------------------------
# delete
# ----------------------------------------------------------------------


def test_keyring_delete_removes_entry(patched_store: FakeBackend) -> None:
    """``keyring delete`` removes the entry and reports success."""
    patched_store.store[("postcards", "alice")] = "pw"
    result = _invoke("keyring", "delete", "alice")
    assert result.exit_code == 0
    assert "removed" in result.output
    assert ("postcards", "alice") not in patched_store.store


def test_keyring_delete_reports_no_entry(patched_store: FakeBackend) -> None:
    """``keyring delete`` is idempotent — no entry → "no entry" message."""
    result = _invoke("keyring", "delete", "nobody")
    assert result.exit_code == 0
    assert "no keyring entry" in result.output


def test_keyring_delete_rejects_empty_username(patched_store: FakeBackend) -> None:
    """An empty username is a usage error (exit 2)."""
    result = _invoke("keyring", "delete", "")
    assert result.exit_code == 2
    assert "username" in result.output


# ----------------------------------------------------------------------
# list
# ----------------------------------------------------------------------


def test_keyring_list_prints_explanation() -> None:
    """``keyring list`` explains why the keyring cannot list entries."""
    result = _invoke("keyring", "list")
    assert result.exit_code == 0
    # The explanation should at least mention the limitation.
    assert "list" in result.output.lower()
    assert "keyring" in result.output.lower()


# ----------------------------------------------------------------------
# status
# ----------------------------------------------------------------------


def test_keyring_status_reports_when_available() -> None:
    """``keyring status`` exits 0 and reports the backend name when keyring is up."""
    # Patch the import so the test is independent of the host's
    # real keyring backend.
    import postcards.config.keyring as km

    class FakeModule:
        class _Backend:
            name = "FakeBackend"

        @staticmethod
        def get_keyring() -> Any:
            return FakeModule._Backend()

    original = km.importlib.import_module

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "keyring":
            return FakeModule
        return original(name, *args, **kwargs)

    km.importlib.import_module = fake_import
    try:
        result = _invoke("keyring", "status")
        assert result.exit_code == 0
        assert "available" in result.output
        assert "FakeBackend" in result.output
    finally:
        km.importlib.import_module = original


def test_keyring_status_reports_when_unavailable() -> None:
    """``keyring status`` exits 1 when no keyring backend is reachable."""
    import postcards.config.keyring as km

    original = km.importlib.import_module

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "keyring":
            raise ImportError("forced by test")
        return original(name, *args, **kwargs)

    km.importlib.import_module = fake_import
    try:
        result = _invoke("keyring", "status")
        assert result.exit_code == 1
        assert "unavailable" in result.output
    finally:
        km.importlib.import_module = original


# ----------------------------------------------------------------------
# help
# ----------------------------------------------------------------------


def test_keyring_help_lists_subcommands() -> None:
    """``postcards keyring --help`` lists every subcommand."""
    result = _invoke("keyring", "--help")
    assert result.exit_code == 0
    output = result.output.lower()
    for sub in ("set", "get", "delete", "list", "status"):
        assert sub in output, f"missing subcommand {sub!r} in help:\n{result.output}"
