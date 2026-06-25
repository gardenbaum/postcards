"""Tests for :class:`postcards.config.KeyringStore`.

The keyring wrapper is the M5 user-facing surface for the
OS credential store. The tests cover the four operations
(set / get / delete / list-via-status) and the diagnostic
:class:`KeyringStatus` payload :func:`postcards.doctor`
consumes.

Hermetic
--------

The tests inject a hand-rolled in-memory backend instead of
the real :mod:`keyring` library so the suite never touches
the host's keyring. The fake implements the same three-method
protocol (``get_password`` / ``set_password`` /
``delete_password``) the real keyring uses, and raises
:class:`keyring.errors.PasswordDeleteError` on the "entry
not found" path the way the real backends do.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from keyring.errors import KeyringError, PasswordDeleteError

from postcards.config import (
    KEYRING_SERVICE,
    KeyringStatus,
    KeyringStore,
)
from postcards.config import (
    KeyringError as StoreKeyringError,
)

# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


class InMemoryBackend:
    """An in-memory stand-in for the real ``keyring`` backend.

    Implements the three-method protocol the
    :class:`KeyringStore` uses, plus the
    :class:`keyring.errors.PasswordDeleteError` semantics
    (raise on missing entry, return nothing on success). The
    ``raises`` field lets a test inject a backend that raises
    on every call — useful for the "keyring broken" branch.
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
def fake_backend() -> InMemoryBackend:
    """A fresh in-memory backend for each test."""
    return InMemoryBackend()


@pytest.fixture
def store(fake_backend: InMemoryBackend) -> Iterator[KeyringStore]:
    """A :class:`KeyringStore` bound to the fake backend."""
    yield KeyringStore(backend=fake_backend)


# ----------------------------------------------------------------------
# Set / get / delete
# ----------------------------------------------------------------------


def test_set_then_get_round_trips(store: KeyringStore, fake_backend: InMemoryBackend) -> None:
    """``set`` stores a value, ``get`` retrieves it; the service is constant."""
    store.set("alice", "secret-123")
    assert fake_backend.store == {(KEYRING_SERVICE, "alice"): "secret-123"}
    assert store.get("alice") == "secret-123"


def test_set_rejects_empty_username(store: KeyringStore) -> None:
    """Setting an empty username raises :class:`KeyringError` (validation)."""
    with pytest.raises(StoreKeyringError, match="username"):
        store.set("", "secret")


def test_set_rejects_empty_password(store: KeyringStore) -> None:
    """Setting an empty password raises :class:`KeyringError` (validation)."""
    with pytest.raises(StoreKeyringError, match="password"):
        store.set("alice", "")


def test_get_returns_none_for_missing_entry(store: KeyringStore) -> None:
    """``get`` on a username that was never set returns ``None``."""
    assert store.get("never-set") is None


def test_delete_returns_true_when_entry_existed(store: KeyringStore) -> None:
    """``delete`` returns ``True`` when the entry was present and removed."""
    store.set("alice", "secret")
    assert store.delete("alice") is True
    assert store.get("alice") is None


def test_delete_returns_false_when_no_entry(store: KeyringStore) -> None:
    """``delete`` returns ``False`` when there was no entry (idempotent)."""
    assert store.delete("never-set") is False


# ----------------------------------------------------------------------
# Error mapping
# ----------------------------------------------------------------------


def test_set_wraps_backend_errors(fake_backend: InMemoryBackend) -> None:
    """Backend failures (locked keyring, denied, ...) surface as :class:`KeyringError`."""
    fake_backend.raises = KeyringError("locked")
    store = KeyringStore(backend=fake_backend)
    with pytest.raises(StoreKeyringError, match="locked"):
        store.set("alice", "secret")


def test_get_swallows_backend_errors(fake_backend: InMemoryBackend) -> None:
    """``get`` returns ``None`` on backend failure (read path is best-effort)."""
    fake_backend.raises = KeyringError("locked")
    store = KeyringStore(backend=fake_backend)
    assert store.get("alice") is None


def test_delete_wraps_unexpected_errors(fake_backend: InMemoryBackend) -> None:
    """A backend that raises a non-``PasswordDeleteError`` exception surfaces as
    :class:`KeyringError`. ``PasswordDeleteError`` itself maps to
    ``False`` (no entry to delete).
    """
    fake_backend.raises = KeyringError("permission denied")
    store = KeyringStore(backend=fake_backend)
    with pytest.raises(StoreKeyringError, match="permission denied"):
        store.delete("alice")


def test_set_without_backend_raises_keyring_error() -> None:
    """Writing without a usable backend raises :class:`KeyringError`."""
    store = KeyringStore()  # no backend injected
    # The host's real keyring may or may not be available; force the
    # "no backend" branch by injecting a backend that resolves to
    # ``None``. We do that by wrapping the class with a stub.
    store._backend = None
    # Force the resolution path to fail by patching the module
    # import; this avoids depending on the host's real keyring.
    import postcards.config.keyring as keyring_module

    original = keyring_module.importlib.import_module

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "keyring":
            raise ImportError("forced by test")
        return original(name, *args, **kwargs)

    keyring_module.importlib.import_module = fake_import
    try:
        with pytest.raises(StoreKeyringError, match="no keyring backend"):
            store.set("alice", "secret")
    finally:
        keyring_module.importlib.import_module = original


# ----------------------------------------------------------------------
# status() — what ``doctor`` consumes
# ----------------------------------------------------------------------


def test_status_reports_available_when_real_keyring_works() -> None:
    """When the real keyring resolves a non-fail backend, status reports it.

    We can't easily inject a real-looking backend into
    :meth:`status` (it deliberately probes the *host*'s
    keyring so :func:`postcards.doctor` can report the
    real backend name), so this test patches
    :func:`importlib.import_module` to return a stand-in
    module whose :func:`get_keyring` returns a fake
    backend. The patch is reverted on teardown so the rest
    of the suite sees the real keyring library.
    """
    import postcards.config.keyring as keyring_module

    class FakeModule:
        class _Backend:
            name = "FakeBackend"

        @staticmethod
        def get_keyring() -> Any:
            return FakeModule._Backend()

    original = keyring_module.importlib.import_module

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "keyring":
            return FakeModule
        return original(name, *args, **kwargs)

    keyring_module.importlib.import_module = fake_import
    try:
        store = KeyringStore()
        status = store.status()
        assert status.available is True
        assert status.backend_name == "FakeBackend"
        assert status.reason is None
    finally:
        keyring_module.importlib.import_module = original


def test_status_reports_unavailable_when_no_backend() -> None:
    """A store with no backend reports ``available=False`` with a reason."""
    store = KeyringStore()  # no backend; relies on the real keyring
    # Patch the module to force "keyring library not importable" so the
    # status path is deterministic regardless of the host.
    import postcards.config.keyring as keyring_module

    original = keyring_module.importlib.import_module

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "keyring":
            raise ImportError("forced by test")
        return original(name, *args, **kwargs)

    keyring_module.importlib.import_module = fake_import
    try:
        status = store.status()
        assert status.available is False
        assert status.backend_name is None
        assert status.reason is not None
        assert "keyring" in status.reason.lower()
    finally:
        keyring_module.importlib.import_module = original


def test_status_returns_keyring_status_dataclass() -> None:
    """``status`` returns a typed :class:`KeyringStatus` (frozen dataclass)."""
    # Patch the keyring module import to a stub that resolves a fake
    # backend; this makes the test independent of the host's actual
    # keyring state.
    import postcards.config.keyring as keyring_module

    class FakeModule:
        class _Backend:
            name = "FakeBackend"

        @staticmethod
        def get_keyring() -> Any:
            return FakeModule._Backend()

    original = keyring_module.importlib.import_module

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "keyring":
            return FakeModule
        return original(name, *args, **kwargs)

    keyring_module.importlib.import_module = fake_import
    try:
        store = KeyringStore()
        status = store.status()
        assert isinstance(status, KeyringStatus)
        # Frozen dataclass — assigning raises ``FrozenInstanceError``.
        with pytest.raises((AttributeError, Exception)):
            status.available = False  # type: ignore[misc]
    finally:
        keyring_module.importlib.import_module = original


# ----------------------------------------------------------------------
# Protocol conformance
# ----------------------------------------------------------------------


def test_in_memory_backend_satisfies_protocol() -> None:
    """The fake backend implements the same protocol the store expects."""
    from postcards.config.keyring import _KeyringProtocol

    backend = InMemoryBackend()
    assert isinstance(backend, _KeyringProtocol)
