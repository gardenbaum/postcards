"""Typed configuration layer for :mod:`postcards.backend`.

This package owns the credential- and config-resolution rules from
``docs/CONSTITUTION.md`` §2. The :class:`ConfigLayer` reads accounts,
addresses and the backend name from the same sources, in the same
order, as the constitution requires:

1. environment variables (``POSTCARDS_USERNAME``, ``POSTCARDS_PASSWORD``,
   ``POSTCARDS_BACKEND``);
2. the OS keyring (via the optional ``keyring`` PyPI package);
3. a user-local config file matched by ``.gitignore``.

The default config-file location is ``./config.json`` (overridable via
``POSTCARDS_CONFIG``); the legacy CLI accepts either ``config.json`` or
``accounts.json`` at the project root.

Public surface
--------------

* :class:`ConfigLayer` — the typed loader
* :class:`AccountConfig`, :class:`AddressSpec` (re-exported from the
  backend module) — the typed payloads returned by the loader
* :class:`ConfigError` — raised when a required field is missing or a
  credential cannot be resolved

The loader does not perform any I/O at construction time; each
``load_*`` call re-reads the requested source. That makes the loader
safe to construct early in ``main()`` and to invoke lazily from the
CLI's command handlers.
"""

from __future__ import annotations

from postcards.backend.base import AddressSpec
from postcards.config.keyring import KEYRING_SERVICE, KeyringError, KeyringStatus, KeyringStore
from postcards.config.loader import (
    AccountConfig,
    ConfigError,
    ConfigLayer,
)

__all__ = [
    "KEYRING_SERVICE",
    "AccountConfig",
    "AddressSpec",
    "ConfigError",
    "ConfigLayer",
    "KeyringError",
    "KeyringStatus",
    "KeyringStore",
]
