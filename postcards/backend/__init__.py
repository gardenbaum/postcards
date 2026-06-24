"""Backend abstraction for the Swiss Postcard Creator integration.

This package defines the ``PostcardBackend`` protocol that all Swiss
Post network calls MUST go through (see ``docs/CONSTITUTION.md`` §1.1).
The package ships two implementations:

* :class:`SwissIdConsumerBackend` — wraps the vendored
  ``postcard_creator`` shim and authenticates with SwissID. This is the
  production backend; it never reaches the network in CI because the
  shim's network methods raise ``NotImplementedError`` unless they are
  monkey-patched by the test.
* :class:`MockBackend` — in-memory implementation that records every
  send. It is the single source of truth for the backend's contract in
  tests; if the live API drifts, the mock and the contract stay in sync
  and the live wrapper is fixed to match.

Backend selection is handled by :func:`select_backend`, which reads the
``POSTCARDS_BACKEND`` environment variable or the ``backend`` field of
the config file.

Public surface
--------------

* :class:`PostcardBackend` — the runtime-checkable Protocol
* :class:`SwissIdConsumerBackend` — production implementation
* :class:`MockBackend` — test / dry-run implementation
* :func:`select_backend` — construct the configured backend
* :class:`AddressSpec`, :class:`PostcardSpec`, :class:`QuotaInfo`,
  :class:`PreviewInfo`, :class:`SendResult` — typed payloads exchanged
  with the backend.
"""

from __future__ import annotations

from postcards.backend.base import (
    AddressSpec,
    PostcardBackend,
    PostcardSpec,
    PreviewInfo,
    QuotaInfo,
    SendResult,
)
from postcards.backend.mock import MockBackend
from postcards.backend.registry import BackendNotAvailableError, available_backends, select_backend
from postcards.backend.swissid import SwissIdConsumerBackend

__all__ = [
    "AddressSpec",
    "BackendNotAvailableError",
    "MockBackend",
    "PostcardBackend",
    "PostcardSpec",
    "PreviewInfo",
    "QuotaInfo",
    "SendResult",
    "SwissIdConsumerBackend",
    "available_backends",
    "select_backend",
]
