"""Shared state model for the TUI.

The TUI is structured around a single :class:`ComposeForm`
value that flows between the :class:`ComposeScreen`,
:class:`PreviewScreen`, and :class:`SendConfirmScreen`. Keeping
the form in its own module — and not on the :class:`App`
itself — means the form is testable in isolation and the
screens can stay narrowly focused on widgets.

Why a dataclass
---------------

The form is a plain value object: every field has a sensible
default, every screen can read and write the same instance,
and the dataclass' ``__eq__`` lets tests assert "the form
moved from A to B" without inspecting private widget state.

A reactive attribute (Textual's :class:`textual.reactive.Reactive`)
would be more "Textual-native" but it would couple the state
to a single widget owner. A plain dataclass is shared by
reference and updated in place; each screen calls
:meth:`ComposeForm.snapshot` when it needs to capture the
"what would we send right now?" view.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Maximum length of a postcard message. The Swiss Postcard Creator
# web form caps the body at 500 characters; the legacy CLI mirrors
# that. The TUI shows a counter that turns red as the user
# approaches the limit, matching the upstream behaviour.
MESSAGE_MAX_LEN = 500


@dataclass
class ComposeForm:
    """The user-editable form backing the Compose screen.

    Attributes
    ----------
    recipient_name:
        Name of the address-book entry to use as the
        recipient. ``None`` means "no recipient selected yet";
        the screen refuses to render a preview until this is
        set. Empty string is treated the same as ``None`` by
        the screens.
    sender_name:
        Optional name of the address-book entry to use as the
        sender. ``None`` / empty means "use the recipient's
        address as the sender", matching the CLI's default.
    picture_path:
        Local filesystem path to the front-of-card picture, or
        a registered plugin reference (``folder``,
        ``pexels``, ...). The screen does no I/O — the CLI
        pipeline handles picture resolution.
    picture_plugin:
        When the picture is supplied through a plugin, the
        plugin name (``folder``, ``pexels`` ...). ``None`` means
        "the picture is a literal path / URL".
    message:
        The raw message body. May contain HTML — the CLI
        forwards the body to the backend as-is, and the legacy
        Swiss Postcard Creator web flow accepts a small
        HTML subset (``<b>``, ``<i>``, ``<br>``, ...).
    template_name:
        Optional name of a :class:`MessageTemplate` to render.
        When set, :attr:`template_vars` is rendered first and
        the result becomes :attr:`message`.
    template_vars:
        ``KEY=VALUE`` strings that the template engine
        substitutes into ``$name`` / ``${name}`` placeholders.
    config_path:
        Path to the legacy ``config.json`` (passed to the
        backend via ``--config``). Defaults to ``config.json``
        in the current working directory; the CLI flow
        honours :data:`POSTCARDS_CONFIG` for this.
    dry_run:
        When ``True`` (the default), the Send button runs the
        pipeline in mocked mode — no SwissID login, no
        network, no quota consumption. The user has to tick
        the "really send" toggle in the Compose screen to
        clear this.
    accounts_file:
        Optional path to a dedicated accounts file (the
        ``-a/--accounts-file`` CLI option). ``None`` means
        "use the main config".
    """

    recipient_name: str | None = None
    sender_name: str | None = None
    picture_path: str | None = None
    picture_plugin: str | None = None
    message: str = ""
    template_name: str | None = None
    template_vars: list[str] = field(default_factory=list[str])
    config_path: Path = field(default_factory=lambda: Path("config.json"))
    dry_run: bool = True
    accounts_file: Path | None = None

    # ------------------------------------------------------------------
    # Derived state
    # ------------------------------------------------------------------

    def has_recipient(self) -> bool:
        """Return ``True`` when a recipient has been selected."""
        return bool(self.recipient_name)

    def effective_message(self) -> str:
        """Return the message text the backend will see.

        Right now this is :attr:`message` verbatim — the
        template renderer is invoked by the CLI layer, not the
        TUI, so the TUI captures the user's raw input and lets
        the existing pipeline do the substitution. The helper
        exists so a future screen that renders templates
        client-side has a single seam to update.
        """
        return self.message

    def preview_path(self) -> Path:
        """Return the path the ``preview --output`` render will write to.

        The TUI defaults to a temp file so a Compose → Preview
        round-trip leaves a verifiable artefact on disk that
        tests can inspect (``tests/test_tui.py`` uses this
        path to confirm the preview was generated).
        """
        import tempfile

        fd, name = tempfile.mkstemp(prefix="postcards-preview-", suffix=".png")
        # ``mkstemp`` returns an open fd we do not need; close
        # it immediately so the renderer can overwrite the
        # path. The leak here is a kernel-side file handle,
        # not a Python resource.
        import os

        os.close(fd)
        return Path(name)

    def snapshot(self) -> dict[str, object]:
        """Return a JSON-friendly snapshot of the form.

        Used by the Preview screen to render a summary and by
        tests to assert "the form looks like X". Keys mirror
        the dataclass fields; values are coerced to
        JSON-friendly primitives (``Path`` -> ``str``,
        ``None`` -> ``None``, ``list`` -> ``list``).
        """
        return {
            "recipient_name": self.recipient_name,
            "sender_name": self.sender_name,
            "picture_path": self.picture_path,
            "picture_plugin": self.picture_plugin,
            "message": self.message,
            "template_name": self.template_name,
            "template_vars": list(self.template_vars),
            "config_path": str(self.config_path),
            "dry_run": self.dry_run,
            "accounts_file": str(self.accounts_file) if self.accounts_file else None,
        }


__all__ = ["MESSAGE_MAX_LEN", "ComposeForm"]
