"""Network-free core for the WYSIWYG web app.

Everything the UI does that is *not* drawing widgets lives here so it
can be unit-tested without a browser and without touching Swiss Post:

* :class:`PostcardDraft` — the mutable form state (addresses, message,
  processed picture bytes).
* :func:`process_image` — run a raw upload through the A6 image pipeline
  once, so the live preview does not re-encode on every keystroke.
* :func:`build_postcard` — assemble an immutable :class:`Postcard` from
  a draft.
* :func:`render_preview` — render one side of the draft to PNG bytes
  (delegates to :mod:`postcards.render`).
* :func:`send_draft` — validate and hand the card to a
  :class:`PostcardBackend` (the caller chooses mock vs. live).

The module imports no UI framework. The only heavy dependency is
Pillow, already pulled in by the image pipeline and renderer.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from postcards.backend.base import AddressSpec, PostcardBackend
from postcards.image import Orientation, prepare_postcard_image
from postcards.models import Message, Postcard
from postcards.models.message import MAX_MESSAGE_LENGTH


def _empty_address() -> AddressSpec:
    """Return a blank :class:`AddressSpec` (all fields empty)."""
    return AddressSpec(prename="", lastname="", street="", zip_code="", place="")


@dataclass
class PostcardDraft:
    """Mutable form state for one postcard being composed in the app.

    ``picture`` holds the *processed* JPEG bytes (already run through
    :func:`process_image`), not the raw upload, so rendering a preview
    is cheap. ``picture_error`` carries the message from the last failed
    image processing so the UI can surface it instead of a blank
    preview.
    """

    recipient: AddressSpec = field(default_factory=_empty_address)
    sender: AddressSpec = field(default_factory=_empty_address)
    message: str = ""
    picture: bytes | None = None
    picture_error: str = ""

    def message_remaining(self) -> int:
        """Characters left before the Swiss Post message limit is hit."""
        return MAX_MESSAGE_LENGTH - len(self.message)


@dataclass(frozen=True)
class SendOutcome:
    """Result of a send attempt, in UI-friendly form."""

    ok: bool
    dry_run: bool
    backend: str
    confirmation: str = ""
    message: str = ""


def process_image(
    raw: bytes,
    *,
    orientation: Orientation = Orientation.AUTO,
) -> bytes:
    """Run ``raw`` upload bytes through the A6 image pipeline.

    Returns processed JPEG bytes ready to store in
    :attr:`PostcardDraft.picture`. Raises :class:`ImageError` when the
    upload is not a decodable / supported image — the caller turns that
    into a user-facing message.
    """
    return prepare_postcard_image(raw, orientation=orientation)


def build_postcard(draft: PostcardDraft) -> Postcard:
    """Assemble an immutable :class:`Postcard` from ``draft``.

    The picture is taken as-is from ``draft.picture`` (already processed
    by :func:`process_image`); no pipeline work happens here, so the
    function is cheap enough to call on every preview refresh.

    Raises
    ------
    ValueError
        When the message exceeds the Swiss Post length limit
        (propagated from :class:`Message`).
    """
    return Postcard(
        sender=draft.sender,
        recipient=draft.recipient,
        message=Message.from_text(draft.message),
        picture=draft.picture,
    )


def render_preview(draft: PostcardDraft, *, side: str, guides: bool = True) -> bytes:
    """Render one side (``"front"`` / ``"back"``) of ``draft`` to PNG bytes.

    ``guides`` defaults to ``True`` because the app's whole point is the
    WYSIWYG print guides (bleed, safe area, stamp/address zones).
    """
    # Imported lazily so importing the service does not pull the
    # renderer (and Pillow font machinery) until a preview is needed.
    from postcards.render import render_png_bytes

    return render_png_bytes(build_postcard(draft), side=side, guides=guides)


def validate_draft(draft: PostcardDraft) -> list[str]:
    """Return a list of human-readable reasons ``draft`` is not sendable.

    An empty list means the draft is valid. Mirrors
    :meth:`Postcard.is_valid` but reports *why* so the UI can guide the
    user instead of just disabling the button.
    """
    problems: list[str] = []
    if not draft.recipient.is_valid():
        problems.append(
            "Recipient address is incomplete (name, street, ZIP and place are required)."
        )
    if not draft.sender.is_valid():
        problems.append("Sender address is incomplete (name, street, ZIP and place are required).")
    if not draft.message.strip() and draft.picture is None:
        problems.append("Add a picture or a message — a card needs at least one.")
    if len(draft.message) > MAX_MESSAGE_LENGTH:
        problems.append(
            f"Message is too long ({len(draft.message)}/{MAX_MESSAGE_LENGTH} characters)."
        )
    return problems


def send_draft(
    draft: PostcardDraft,
    *,
    backend: PostcardBackend,
    username: str = "",
    password: str = "",
    dry_run: bool = True,
) -> SendOutcome:
    """Validate ``draft`` and hand the card to ``backend``.

    The caller chooses the backend: a ``MockBackend`` records the send
    without any network, while a live ``SwissIdConsumerBackend`` reaches
    Swiss Post. ``dry_run`` is forwarded as the ``mock`` flag of
    :meth:`PostcardBackend.send`, so a live dry-run validates the card
    upstream without consuming the daily quota.

    Login is attempted only when both ``username`` and ``password`` are
    given (the mock backend ignores them; the live backend needs them).
    Any backend exception is caught and returned as a failed
    :class:`SendOutcome` so the UI never crashes mid-send.
    """
    problems = validate_draft(draft)
    if problems:
        return SendOutcome(
            ok=False,
            dry_run=dry_run,
            backend=getattr(backend, "name", type(backend).__name__),
            message=" ".join(problems),
        )

    card = build_postcard(draft)
    backend_name = getattr(backend, "name", type(backend).__name__)
    try:
        if username and password:
            backend.login(username, password)
        result = backend.send(card, mock=dry_run)
    except Exception as exc:
        return SendOutcome(ok=False, dry_run=dry_run, backend=backend_name, message=str(exc))

    return SendOutcome(
        ok=True,
        dry_run=dry_run,
        backend=result.backend,
        confirmation=result.confirmation or "",
        message=(
            "Dry-run succeeded — the card is valid and was NOT sent."
            if dry_run
            else "Postcard sent."
        ),
    )


def with_recipient_field(draft: PostcardDraft, field_name: str, value: str) -> PostcardDraft:
    """Return ``draft`` with one recipient address field replaced.

    Convenience for the UI's field-change handlers; keeps the
    :class:`AddressSpec` immutability discipline (a new spec per edit)
    out of the widget callbacks.
    """
    draft.recipient = replace(draft.recipient, **{field_name: value})
    return draft


def with_sender_field(draft: PostcardDraft, field_name: str, value: str) -> PostcardDraft:
    """Return ``draft`` with one sender address field replaced."""
    draft.sender = replace(draft.sender, **{field_name: value})
    return draft


__all__ = [
    "PostcardDraft",
    "SendOutcome",
    "build_postcard",
    "process_image",
    "render_preview",
    "send_draft",
    "validate_draft",
    "with_recipient_field",
    "with_sender_field",
]
