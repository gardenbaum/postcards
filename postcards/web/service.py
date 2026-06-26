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

import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from postcards.backend.base import AddressSpec, PostcardBackend
from postcards.config import (
    AccountConfig,
    ConfigError,
    ConfigLayer,
    KeyringError,
    KeyringStore,
)
from postcards.image import Orientation, prepare_postcard_image
from postcards.models import Message, Postcard
from postcards.models.message import MAX_MESSAGE_LENGTH

if TYPE_CHECKING:
    from postcards.backend.swissid import SwissIdConsumerBackend


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


# ---------------------------------------------------------------------------
# Authentication — credential resolution, keyring, login/quota check.
#
# All of this is network-free *except* check_login, which goes through the
# backend (so it is still mock-testable). The app surfaces every piece so a
# user never has to drop to the CLI to manage SwissID credentials.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthState:
    """What the app knows about credential resolution, for the UI to render.

    ``accounts`` are the resolved SwissID accounts (env → keyring → config
    file, per the constitution's order). Each carries its ``source`` and
    whether a password was found, so the app can prefill the form and show
    where the credential came from — without displaying the secret.
    """

    accounts: tuple[AccountConfig, ...] = ()
    keyring_available: bool = False
    keyring_reason: str = ""
    config_path: str = ""
    error: str = ""

    def has_accounts(self) -> bool:
        return bool(self.accounts)

    def usernames(self) -> list[str]:
        return [a.username for a in self.accounts]

    def find(self, username: str) -> AccountConfig | None:
        return next((a for a in self.accounts if a.username == username), None)


@dataclass(frozen=True)
class LoginCheck:
    """Result of a live (or mock) login + quota probe."""

    ok: bool
    quota_available: bool | None = None
    detail: str = ""


def resolve_auth(
    *,
    config_path: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    keyring_backend: Any = None,
) -> AuthState:
    """Resolve SwissID accounts + keyring status for the app, never raising.

    Wraps :class:`ConfigLayer.load_accounts` (env → keyring → config file)
    and :meth:`KeyringStore.status`. A missing config / no accounts is a
    normal empty state, not an error; genuine parse errors are captured in
    :attr:`AuthState.error` so the app can show them inline.
    """
    store = KeyringStore(keyring_backend)
    status = store.status()
    layer = ConfigLayer(
        env=env if env is not None else os.environ,
        config_path=Path(config_path) if isinstance(config_path, str) else config_path,
        keyring_backend=keyring_backend,
    )
    accounts: tuple[AccountConfig, ...] = ()
    error = ""
    try:
        accounts = tuple(layer.load_accounts())
    except ConfigError as exc:
        # "no accounts configured" is expected; surface only real problems.
        if "no accounts found" not in str(exc):
            error = str(exc)
    return AuthState(
        accounts=accounts,
        keyring_available=status.available,
        keyring_reason=status.reason or "",
        config_path=str(layer.config_path_resolved()),
        error=error,
    )


def save_to_keyring(username: str, password: str, *, store: KeyringStore | None = None) -> str:
    """Store ``password`` for ``username`` in the OS keyring.

    Returns a short confirmation message. Raises :class:`KeyringError` when
    the keyring is unavailable or locked — the app turns that into a notice.
    """
    if not username or not password:
        raise KeyringError("username and password must both be set to save to the keyring")
    (store or KeyringStore()).set(username, password)
    return f"Saved password for {username} in the OS keyring."


def check_login(backend: PostcardBackend, username: str, password: str) -> LoginCheck:
    """Probe ``backend`` login + quota, catching every failure for the UI.

    With the mock backend this always succeeds; with the live SwissID
    backend it performs a real login (which may trigger 2FA / anomaly
    checks) and reads the daily quota.
    """
    if not username or not password:
        return LoginCheck(ok=False, detail="Enter a SwissID e-mail and password first.")
    try:
        backend.login(username, password)
        quota = backend.quota()
    except Exception as exc:  # surface any auth/network failure
        return LoginCheck(ok=False, detail=str(exc))
    if quota.available:
        return LoginCheck(
            ok=True, quota_available=True, detail="Login OK — a card is available today."
        )
    when = f" (next: {quota.next_available_at:%Y-%m-%d %H:%M})" if quota.next_available_at else ""
    return LoginCheck(
        ok=True,
        quota_available=False,
        detail=f"Login OK — daily quota already used{when}.",
    )


def begin_browser_login() -> tuple[str, str]:
    """Start a browser-assisted SwissID login (for accounts with 2FA).

    Returns ``(authorize_url, code_verifier)``. Network-free — it only builds
    the OAuth authorize URL + PKCE verifier. The caller shows the URL, the
    user logs in in a browser (completing push / passkey / SMS 2FA), and the
    pasted code goes to :func:`complete_browser_login` with this verifier.
    """
    from postcards.backend import SwissIdConsumerBackend

    return SwissIdConsumerBackend().begin_browser_login()


def complete_browser_login(
    code_or_url: str, verifier: str, *, session: Any = None
) -> PostcardBackend:
    """Exchange the pasted code for a token; return an authenticated backend.

    Raises :class:`~postcards.backend.exceptions.AuthenticationError` on
    failure (bad / expired code). The returned backend is ready to
    :func:`send_draft` (pass it with no username/password).
    """
    from postcards.backend import SwissIdConsumerBackend

    backend = SwissIdConsumerBackend()
    backend.complete_browser_login(code_or_url, verifier, session=session)
    return backend


@dataclass(frozen=True)
class SmsLoginState:
    """Result of an SMS-login step, in UI-friendly form.

    ``backend`` carries the in-flight login (cookies, pending ``authId``) and
    must be passed back to :func:`submit_sms_code`. When ``authenticated`` is
    true it is ready for :func:`send_draft`; when ``needs_code`` is true the
    user must enter the SMS code SwissID just sent.
    """

    backend: SwissIdConsumerBackend
    ok: bool
    authenticated: bool = False
    needs_code: bool = False
    prompt: str = ""
    detail: str = ""


def begin_sms_login(username: str, password: str, *, session: Any = None) -> SmsLoginState:
    """Start a native SwissID login (e-mail + password → SMS code).

    Never raises: any auth failure is returned as ``ok=False`` with a detail
    message so the UI can show it. On success either ``authenticated`` (no 2FA)
    or ``needs_code`` (SMS code required) is set.
    """
    from postcards.backend import SwissIdConsumerBackend

    backend = SwissIdConsumerBackend()
    if not username or not password:
        return SmsLoginState(
            backend=backend, ok=False, detail="Enter a SwissID e-mail and password first."
        )
    try:
        done = backend.begin_sms_login(username, password, session=session)
    except Exception as exc:
        return SmsLoginState(backend=backend, ok=False, detail=str(exc))
    if done:
        return SmsLoginState(
            backend=backend, ok=True, authenticated=True, detail="Login OK — no second factor."
        )
    return SmsLoginState(
        backend=backend,
        ok=True,
        needs_code=True,
        prompt=backend.second_factor_prompt,
        detail="SwissID sent an SMS code — enter it to finish logging in.",
    )


def submit_sms_code(
    backend: SwissIdConsumerBackend, code: str, *, session: Any = None
) -> SmsLoginState:
    """Finish an SMS login by submitting ``code`` to the pending ``backend``.

    Never raises: a rejected / expired code comes back as ``ok=False``.
    """
    if not code.strip():
        return SmsLoginState(
            backend=backend, ok=False, needs_code=True, detail="Enter the SMS code."
        )
    try:
        backend.submit_sms_code(code, session=session)
    except Exception as exc:
        return SmsLoginState(backend=backend, ok=False, needs_code=True, detail=str(exc))
    return SmsLoginState(
        backend=backend,
        ok=True,
        authenticated=True,
        detail="SMS login complete — you can send now.",
    )


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
    "AuthState",
    "LoginCheck",
    "PostcardDraft",
    "SendOutcome",
    "SmsLoginState",
    "begin_browser_login",
    "begin_sms_login",
    "build_postcard",
    "check_login",
    "complete_browser_login",
    "process_image",
    "render_preview",
    "resolve_auth",
    "save_to_keyring",
    "send_draft",
    "submit_sms_code",
    "validate_draft",
    "with_recipient_field",
    "with_sender_field",
]
