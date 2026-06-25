"""The interactive WYSIWYG web app for composing and sending postcards.

The package splits cleanly in two so the logic stays testable without a
browser:

* :mod:`postcards.web.service` — pure, typed, network-free functions
  that turn a :class:`~postcards.web.service.PostcardDraft` into a
  :class:`~postcards.models.Postcard`, render a live PNG preview
  (reusing :mod:`postcards.render`), and send via any
  :class:`~postcards.backend.base.PostcardBackend`. No NiceGUI import,
  so the unit tests drive it directly against a ``MockBackend``.
* :mod:`postcards.web.app` — the thin NiceGUI UI layer that wires form
  inputs to the service and refreshes the preview live. Imported lazily
  (it requires the optional ``app`` extra: ``pip install
  'postcards[app]'``).

The ``postcards app`` CLI command launches the UI.
"""

from __future__ import annotations

from postcards.web.service import (
    PostcardDraft,
    SendOutcome,
    build_postcard,
    process_image,
    render_preview,
    send_draft,
)

__all__ = [
    "PostcardDraft",
    "SendOutcome",
    "build_postcard",
    "process_image",
    "render_preview",
    "send_draft",
]
