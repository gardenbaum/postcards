"""Typed domain addresses — :class:`Recipient` and :class:`Sender`.

The Swiss Postcard Creator distinguishes recipient addresses from
sender addresses by which fields are optional and which are
required at the API layer:

* ``Recipient`` supports ``salutation`` and ``company_addition`` —
  the Swiss Post endpoint uses them for the printed greeting line.
* ``Sender`` supports ``country`` — the printed return address
  needs a country code; the recipient's country is implicit
  (Switzerland).

The underlying data class is the same :class:`AddressSpec` (see
:mod:`postcards.backend.base`) because structurally the two types
are interchangeable; the names exist as **type aliases** so that
:func:`Postcard` constructor signatures read like the upstream
API::

    postcard = Postcard(
        sender=Sender(prename="...", ...),
        recipient=Recipient(prename="...", ..., salutation="Mr."),
        message=Message.from_text("Hi!"),
    )

If a future milestone needs to differentiate them at runtime (for
example to add field-validation that only applies to senders), turn
the aliases into thin dataclass subclasses — the call sites do not
need to change.
"""

from __future__ import annotations

from postcards.backend.base import AddressSpec

#: Alias for :class:`AddressSpec` used as the destination of a postcard.
Recipient = AddressSpec

#: Alias for :class:`AddressSpec` used as the return address of a postcard.
Sender = AddressSpec

__all__ = ["Recipient", "Sender"]
