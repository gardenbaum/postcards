"""Typed domain models for postcards.

The :mod:`postcards.models` package groups the user-facing types the
CLI builds before handing a card to a :class:`PostcardBackend`:

* :class:`Recipient` / :class:`Sender` — typed aliases over
  :class:`postcards.backend.base.AddressSpec`, one each so call
  sites read like the upstream Swiss Post API.
* :class:`Message` — the typed greeting (≤500 chars).
* :class:`Postcard` — the high-level postcard bundling all of the
  above plus an optional processed picture.

The lower-level transport types — :class:`AddressSpec`,
:class:`PostcardSpec`, :class:`QuotaInfo`, :class:`PreviewInfo`,
:class:`SendResult` — live in :mod:`postcards.backend.base` and are
re-exported here for convenience so a caller that imports
``postcards.models`` has every public dataclass in scope.
"""

from __future__ import annotations

from postcards.backend.base import (
    AddressSpec,
    PostcardSpec,
    PreviewInfo,
    QuotaInfo,
    SendResult,
)
from postcards.models.address import Recipient, Sender
from postcards.models.message import MAX_MESSAGE_LENGTH, Message
from postcards.models.postcard import Postcard

__all__ = [
    "MAX_MESSAGE_LENGTH",
    "AddressSpec",
    "Message",
    "Postcard",
    "PostcardSpec",
    "PreviewInfo",
    "QuotaInfo",
    "Recipient",
    "SendResult",
    "Sender",
]
