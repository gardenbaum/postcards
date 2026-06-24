"""Backend selection — which :class:`PostcardBackend` does the CLI use?

The CLI's backend is selected by, in order:

1. The ``POSTCARDS_BACKEND`` environment variable.
2. The ``backend`` field of the config file (``~/.config/postcards/config.json``
   or ``./config.json``).

Valid values are ``"swissid"`` (production) and ``"mock"`` (in-memory).
Anything else raises :class:`BackendNotAvailableError`; missing both
sources falls back to ``"swissid"`` so an unmodified install behaves
identically to the legacy CLI.

The registry is intentionally a function, not a class — there is no
state worth keeping across calls. Tests inject a custom env / config
file via the function arguments.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from postcards.backend.base import PostcardBackend


class BackendNotAvailableError(RuntimeError):
    """Raised when the requested backend name is not registered."""


# Built-in backends. New backends are added by appending here AND by
# exporting them from ``postcards.backend.__init__``.
_BUILTINS: dict[str, type[PostcardBackend]] = {}


def _register_builtins() -> None:
    """Populate :data:`_BUILTINS` lazily to avoid an import cycle.

    The registry module imports the backend implementations inside
    the function rather than at module scope so that ``import
    postcards.backend.registry`` does not pull in the full backend
    tree (which would defeat the lazy-loading intent of the SwissID
    wrapper).
    """
    if _BUILTINS:
        return
    from postcards.backend.mock import MockBackend
    from postcards.backend.swissid import SwissIdConsumerBackend

    _BUILTINS["mock"] = MockBackend
    _BUILTINS["swissid"] = SwissIdConsumerBackend


def available_backends() -> list[str]:
    """Return the sorted list of registered backend names."""
    _register_builtins()
    return sorted(_BUILTINS)


def select_backend(
    env: dict[str, str] | None = None,
    config: dict[str, Any] | None = None,
    *,
    default: str = "swissid",
) -> PostcardBackend:
    """Construct the backend named by ``env`` / ``config``.

    Parameters
    ----------
    env:
        Environment-variable mapping. ``None`` reads :data:`os.environ`.
        Tests pass a custom mapping to avoid touching real env vars.
    config:
        Parsed config-file mapping. ``None`` means no config file
        was loaded; the function falls back to the default backend.
    default:
        Backend name to use when neither ``env`` nor ``config``
        specifies one. ``"swissid"`` matches the legacy CLI's
        behaviour.

    Raises
    ------
    BackendNotAvailableError
        When the requested backend name is not registered. The error
        message lists the valid names so the user can fix the typo.
    """
    _register_builtins()

    env_source = env if env is not None else dict(os.environ)
    name = env_source.get("POSTCARDS_BACKEND")
    if name is None and config is not None:
        raw = config.get("backend")
        if isinstance(raw, str):
            name = raw
    if name is None:
        name = default

    if name not in _BUILTINS:
        valid = ", ".join(sorted(_BUILTINS))
        raise BackendNotAvailableError(f"unknown backend {name!r}; valid backends are: {valid}")

    cls = _BUILTINS[name]
    return cls()


__all__ = ["BackendNotAvailableError", "available_backends", "select_backend"]
