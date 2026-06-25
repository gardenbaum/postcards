"""OS keyring wrapper for the ``postcards`` credential store.

M5 adds explicit, user-driven access to the OS keyring. The
:class:`ConfigLayer` already reads from the keyring as a
credential-resolution source (per ``docs/CONSTITUTION.md`` §2.2);
this module owns the *writes* and the *diagnostics* the user
needs when something goes wrong.

Why a wrapper rather than ``keyring`` calls scattered through the
code base
----------------------------------------------------------------

* The keyring library exposes a *protocol* (``keyring.get_password``
  / ``set_password`` / ``delete_password``) plus a global backend
  discovery mechanism. Tests want to inject an in-memory backend
  without monkey-patching module globals; production wants the
  real OS-native backend. A single :class:`KeyringStore` class
  is the injection point for both.
* Errors raised by the keyring backend vary (denied by the OS,
  locked keyring, no backend available, ...). The wrapper
  translates each into a typed
  :class:`postcards.config.keyring.KeyringError` so the CLI can
  surface a consistent message regardless of platform.
* Listing stored usernames — needed by ``postcards keyring list``
  and ``postcards doctor`` — is **not** part of the keyring
  protocol. The standard library backends do not support it
  (macOS Keychain, Windows Credential Manager, Secret Service,
  and KWallet all keep the username list private). The wrapper
  here is explicit about that limitation rather than papering
  over it with a misleading implementation.

The "doctor" hint
-----------------

When a user reports "the keyring isn't working", the typical
underlying cause is one of:

* the ``keyring`` package cannot find a backend on this host
  (e.g. headless server, container without a secret service);
* the backend is locked (e.g. an idle GNOME Keyring asking for
  the unlock password);
* the user has never set a keyring password for this service,
  so the first read returns ``None`` and the loader falls
  through to the next source silently.

:class:`KeyringStore.status` returns a structured
:class:`KeyringStatus` payload (available / backend name /
``None`` password sentinel) so ``postcards doctor`` can produce
a one-line diagnosis for each of these causes.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

#: Service name used when reading or writing credentials in the OS
#: keyring. Re-exported from :mod:`postcards.config.loader` so the
#: canonical constant lives in exactly one place. The :class:`KeyringStore`
#: writes to ``(KEYRING_SERVICE, username)`` and reads from the same
#: pair, which means the :class:`ConfigLayer` and this store always
#: agree on which key to look up.
from postcards.config.loader import KEYRING_SERVICE

#: Module-level logger. Routes through :mod:`postcards.log`'s
#: :func:`configure` so the per-call lines share the project's
#: format and end up on stderr at the right verbosity.
_LOGGER = logging.getLogger("postcards.config.keyring")


class KeyringError(RuntimeError):
    """Raised when the OS keyring cannot service a request.

    Wraps whatever the underlying backend raised so the CLI can
    surface one consistent error type. The ``reason`` attribute
    carries the original exception's message for diagnostics.
    """

    def __init__(self, message: str, *, reason: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason or message


@runtime_checkable
class _KeyringProtocol(Protocol):
    """Structural subset of the ``keyring`` library used by this module.

    Declared as a :class:`Protocol` so tests can pass a hand-rolled
    double without subclassing the real keyring backend. The
    production path imports the real :mod:`keyring` module; the
    method signatures match it exactly.
    """

    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def delete_password(self, service: str, username: str) -> None: ...


@dataclass(frozen=True)
class KeyringStatus:
    """Result of :meth:`KeyringStore.status` — what ``doctor`` displays.

    Attributes
    ----------
    available:
        ``True`` if the keyring library is importable *and* a
        backend is reachable. ``False`` means the keyring is not
        a usable credential source on this host.
    backend_name:
        Human-readable name of the active keyring backend
        (e.g. ``"macOS"``, ``"SecretService"``, ``"Windows"``).
        ``None`` when no backend is reachable.
    reason:
        Short explanation of *why* the keyring is or is not
        available. ``doctor`` prints this verbatim so the user
        sees the underlying cause (locked keyring, missing
        backend, etc.) without having to interpret a stack
        trace.
    """

    available: bool
    backend_name: str | None
    reason: str | None


class KeyringStore:
    """A typed, testable wrapper around the ``keyring`` library.

    The store is constructed with an optional ``backend`` argument;
    tests pass a hand-rolled double, production code passes
    ``None`` and the store uses the real :mod:`keyring` module's
    global backend. The class is intentionally small — every
    method does one thing and raises :class:`KeyringError` on
    failure.
    """

    def __init__(self, backend: Any = None) -> None:
        # ``self._backend`` is ``None`` until :meth:`_resolve` runs.
        # We do not resolve at construction time so a ``KeyringStore()``
        # instance can be created in tests even when the keyring
        # library is missing — :meth:`status` will report the
        # unavailability cleanly.
        self._backend: Any = backend

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, username: str) -> str | None:
        """Return the password for ``username`` or ``None`` if absent.

        Returns ``None`` when the entry is not present *and* when
        the keyring backend is unavailable; the loader treats
        both cases as "no value" so the next source can win. Use
        :meth:`status` to tell the two cases apart for diagnostics.
        """
        backend = self._resolve()
        if backend is None:
            return None
        try:
            return backend.get_password(KEYRING_SERVICE, username)
        except Exception as exc:
            _LOGGER.debug("keyring get failed for %r: %s", username, exc)
            return None

    def set(self, username: str, password: str) -> None:
        """Store ``password`` for ``username`` in the keyring.

        Raises :class:`KeyringError` on any failure (locked
        keyring, denied permission, missing backend, etc.). The
        caller is expected to translate the error into a
        user-facing message.
        """
        if not username:
            raise KeyringError("keyring username must not be empty")
        if not password:
            raise KeyringError("keyring password must not be empty")
        backend = self._resolve()
        if backend is None:
            raise KeyringError("no keyring backend is available on this host")
        try:
            backend.set_password(KEYRING_SERVICE, username, password)
        except Exception as exc:
            raise KeyringError(
                f"failed to store password in the keyring: {exc}",
                reason=str(exc),
            ) from exc

    def delete(self, username: str) -> bool:
        """Remove the entry for ``username`` from the keyring.

        Returns ``True`` if an entry was deleted, ``False`` when
        no entry was present. Raises :class:`KeyringError` on any
        backend failure (locked keyring, permission denied, ...).
        """
        backend = self._resolve()
        if backend is None:
            raise KeyringError("no keyring backend is available on this host")
        try:
            backend.delete_password(KEYRING_SERVICE, username)
            return True
        except Exception as exc:
            # The standard keyring API raises ``PasswordDeleteError``
            # when the entry is not found. We treat that as "no entry
            # to delete" and return ``False`` rather than surfacing an
            # error to the user. The class is imported lazily so this
            # module still imports cleanly when the keyring package
            # is missing.
            if _is_password_delete_error(exc):
                return False
            raise KeyringError(
                f"failed to delete keyring entry: {exc}",
                reason=str(exc),
            ) from exc

    def status(self) -> KeyringStatus:
        """Return a structured snapshot for ``postcards doctor``.

        The function probes the keyring twice: once to import the
        library, once to read the active backend. The output is
        deliberately coarse — :class:`KeyringStatus` is a three-field
        dataclass so ``doctor`` can render a one-line summary
        without having to know about the keyring library's
        exception hierarchy.
        """
        try:
            keyring_module = importlib.import_module("keyring")
        except ImportError:
            return KeyringStatus(
                available=False,
                backend_name=None,
                reason=(
                    "the 'keyring' Python package is not installed; "
                    "install it with 'pip install \"postcards[keyring]\"'"
                ),
            )
        try:
            backend = keyring_module.get_keyring()
        except Exception as exc:
            return KeyringStatus(
                available=False,
                backend_name=None,
                reason=f"keyring backend discovery failed: {exc}",
            )
        name = _safe_backend_name(backend)
        # ``get_keyring()`` may return a ``Fail`` proxy when no
        # backend is reachable; check the type name rather than
        # importing the class so the doctor does not depend on
        # internal keyring symbols.
        if backend is None or type(backend).__name__ in {"Fail", "NullKeyring"}:
            return KeyringStatus(
                available=False,
                backend_name=name,
                reason=(
                    f"no usable keyring backend on this host "
                    f"(discovered: {name!r}); "
                    "see https://pypi.org/project/keyring/#supported-backends"
                ),
            )
        return KeyringStatus(available=True, backend_name=name, reason=None)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve(self) -> Any:
        """Return the backend to use, importing :mod:`keyring` lazily.

        Caches the resolved backend on the instance so repeated
        calls in a single CLI invocation do not re-import the
        library or re-probe the OS.

        Returns ``None`` when the keyring library is not installed
        or no backend is available; callers translate that into
        either a silent fallback (read path) or a
        :class:`KeyringError` (write path).
        """
        if self._backend is not None:
            return self._backend
        try:
            keyring_module = importlib.import_module("keyring")
        except ImportError:
            return None
        try:
            backend = keyring_module.get_keyring()
        except Exception as exc:
            _LOGGER.debug("keyring backend resolution failed: %s", exc)
            return None
        # ``Fail`` / ``NullKeyring`` proxies are returned when no
        # backend is reachable; they would silently swallow writes
        # without persisting anything. Treat them as "no backend"
        # so the write path raises a clear error.
        if backend is None or type(backend).__name__ in {"Fail", "NullKeyring"}:
            return None
        return backend


def _safe_backend_name(backend: Any) -> str | None:
    """Return a human-readable backend name, or ``None`` on failure.

    Different backends expose their name under different
    attributes; we probe a few common ones in order of
    preference. The function never raises — the doctor uses it
    to label the active backend in its output and a missing
    label is preferable to a stack trace.
    """
    if backend is None:
        return None
    for attr in ("name", "priority", "__class__.__name__"):
        if attr == "__class__.__name__":
            value: Any = type(backend).__name__
        else:
            value = getattr(backend, attr, None)
        if isinstance(value, str) and value:
            return value
    return None


def _is_password_delete_error(exc: BaseException) -> bool:
    """Return ``True`` when ``exc`` is the keyring "not found" error.

    The real :class:`keyring.errors.PasswordDeleteError` is the
    canonical marker; we fall back to the class name so the
    check still works when the keyring package is missing or
    the test injects a backend that raises a plain
    :class:`Exception` with the same name.
    """
    try:
        from keyring.errors import PasswordDeleteError
    except ImportError:
        return type(exc).__name__ == "PasswordDeleteError"
    return isinstance(exc, PasswordDeleteError) or type(exc).__name__ == "PasswordDeleteError"


__all__ = [
    "KEYRING_SERVICE",
    "KeyringError",
    "KeyringStatus",
    "KeyringStore",
]
