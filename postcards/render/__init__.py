"""Offline postcard rendering — preview a card without contacting Swiss Post.

The :mod:`postcards.render` package takes a user-facing
:class:`postcards.models.Postcard` and produces a local image
(PNG / JPEG) or PDF that shows what the card would look like
once printed. It is intentionally **purely local**: no Swiss
Post API call, no SwissID login, no quota consumption.

Why a separate package
----------------------

The legacy CLI's ``--mock`` flag on ``send`` was the only way
to "preview" a card, and it only validated that the picture /
message / addresses were acceptable to the upstream endpoint —
it never showed the user the actual rendered card. M2 promotes
that flow to a first-class command so the user can
``postcards preview`` and then ``postcards send`` as two
distinct steps (matching the upstream Postcard Creator web UI).

Public surface
--------------

* :func:`render_postcard` — high-level convenience: write a
  single ``output_path`` from a :class:`Postcard`. Format is
  inferred from the file extension (``.png``, ``.jpg``,
  ``.jpeg``, ``.pdf``).
* :func:`render_front` — return a Pillow ``Image`` for the
  front of the card.
* :func:`render_back` — return a Pillow ``Image`` for the
  back (message + addresses).
* :class:`RenderError` — raised when the postcard cannot be
  rendered (e.g. the picture bytes are not a decodable image).

The module does not depend on the network, the backend layer,
or the shim. The only third-party dependency is Pillow, which
the image pipeline already requires.
"""

from __future__ import annotations

from postcards.render.postcard_renderer import (
    RenderError,
    render_back,
    render_front,
    render_postcard,
)

__all__ = [
    "RenderError",
    "render_back",
    "render_front",
    "render_postcard",
]
