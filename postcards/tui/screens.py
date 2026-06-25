"""Textual screens for the ``postcards`` TUI.

The TUI is organised as a small stack of screens, each
focused on one task:

* :class:`MainMenuScreen` — landing screen. Buttons for
  Compose, Browse addresses, Browse templates, Help, Quit.
* :class:`ComposeScreen` — the form the user fills in
  (recipient, sender, picture, message). Live preview of
  the rendered message body and a char counter against
  :data:`postcards.tui.state.MESSAGE_MAX_LEN`.
* :class:`PreviewScreen` — modal that shows the rendered
  preview path and asks the user to confirm the send.
* :class:`SendConfirmScreen` — the last-line-of-defence
  modal. Refuses to call the network unless the user
  explicitly types ``YES``.

The screens are deliberately small. Heavy logic (config
dict building, render dispatch) lives on
:class:`postcards.tui.app.PostcardsApp` so it stays
testable without a Textual Pilot harness.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from postcards.tui.app import PostcardsApp

from postcards.cli.errors import CLIError
from postcards.tui.state import MESSAGE_MAX_LEN

# Textual is an optional dep (postcards[gui]). Import it lazily
# inside each screen so a missing textual install fails at
# screen-mount time with a clear message instead of at package
# import time.


# ----------------------------------------------------------------------
# Main menu
# ----------------------------------------------------------------------


class MainMenuScreen:
    """Landing screen.

    Implemented as a thin wrapper that exposes a single
    ``compose()``-equivalent method and a ``BINDINGS`` table.
    The actual Textual :class:`textual.screen.Screen` class is
    built dynamically by :meth:`build` so the rest of the
    module does not need to import :mod:`textual` at
    type-check time.
    """

    def __init__(self, app: PostcardsApp) -> None:
        self._app = app

    def build(self) -> Any:
        """Return the underlying :class:`textual.screen.Screen` instance."""
        from textual.binding import Binding
        from textual.containers import Container, Vertical
        from textual.screen import Screen
        from textual.widgets import Button, Footer, Header, Static

        app = self._app
        form = app.form

        class _Screen(Screen[None]):
            BINDINGS: ClassVar[list] = [
                Binding("q", "quit_app", "Quit", show=True),
                Binding("c", "compose", "Compose", show=True),
                Binding("a", "addresses", "Addresses", show=True),
                Binding("t", "templates", "Templates", show=True),
                Binding("?", "help", "Help", show=True),
            ]

            def compose(self) -> Any:
                yield Header(show_clock=False)
                with Container(id="main-menu"):
                    yield Static(
                        "[bold]Postcards[/bold] — local TUI for the Swiss Postcard Creator",
                        id="title",
                    )
                    yield Static(
                        "Compose a postcard, browse your address book / templates, "
                        "or send a dry-run preview without contacting Swiss Post.",
                        id="subtitle",
                    )
                    yield Static(
                        f"Dry-run default: {'ON' if form.dry_run else 'OFF'}", id="dry-run"
                    )
                    with Vertical(id="main-buttons"):
                        yield Button("Compose (c)", id="btn-compose", variant="primary")
                        yield Button("Browse addresses (a)", id="btn-addresses")
                        yield Button("Browse templates (t)", id="btn-templates")
                        yield Button("Help / about (?)", id="btn-help")
                        yield Button("Quit (q)", id="btn-quit")
                yield Footer()

            def on_button_pressed(self, event: Any) -> None:
                bid = event.button.id
                if bid == "btn-compose":
                    self.action_compose()
                elif bid == "btn-addresses":
                    self.action_addresses()
                elif bid == "btn-templates":
                    self.action_templates()
                elif bid == "btn-help":
                    self.action_help()
                elif bid == "btn-quit":
                    self.action_quit_app()

            def action_compose(self) -> None:
                self.app.push_screen(ComposeScreen(app).build())

            def action_addresses(self) -> None:
                self.app.push_screen(AddressBookScreen(app).build())

            def action_templates(self) -> None:
                self.app.push_screen(TemplateBookScreen(app).build())

            def action_help(self) -> None:
                self.app.push_screen(HelpScreen(app).build())

            def action_quit_app(self) -> None:
                self.app.exit()

        return _Screen()


# ----------------------------------------------------------------------
# Address book browser
# ----------------------------------------------------------------------


class AddressBookScreen:
    """Read-only browser for the user's address book.

    The TUI is read-only against the user's data; mutations
    happen via ``postcards addresses add ...``. We surface
    this rule in the header so the user is not surprised
    when their edits do not persist.
    """

    def __init__(self, app: PostcardsApp) -> None:
        self._app = app

    def build(self) -> Any:
        from textual.binding import Binding
        from textual.containers import Container, Vertical
        from textual.screen import Screen
        from textual.widgets import Button, Footer, Header, Static

        app = self._app

        class _Screen(Screen[None]):
            BINDINGS: ClassVar[list] = [
                Binding("escape", "app.pop_screen", "Back"),
                Binding("q", "quit_app", "Quit"),
            ]

            def compose(self) -> Any:
                yield Header(show_clock=False)
                with Container(id="address-book"):
                    yield Static(
                        "[bold]Address book[/bold] — read-only in the TUI. "
                        "Edit entries with: postcards addresses add NAME --prename ...",
                        id="ab-help",
                    )
                    recipients = app.list_recipients()
                    senders = app.list_senders()
                    if not recipients and not senders:
                        yield Static("(no entries yet — see the README quickstart)", id="ab-empty")
                    else:
                        with Vertical(id="ab-list"):
                            for name, label in recipients:
                                yield Static(f"  [bold]→ {name}[/bold]  {label}", classes="entry")
                            for name, label in senders:
                                yield Static(f"  [bold]← {name}[/bold]  {label}", classes="entry")
                    yield Static("", id="ab-spacer")
                    yield Button("Back (esc)", id="btn-back")
                yield Footer()

            def on_button_pressed(self, event: Any) -> None:
                if event.button.id == "btn-back":
                    self.app.pop_screen()

            def action_quit_app(self) -> None:
                self.app.exit()

        return _Screen()


# ----------------------------------------------------------------------
# Template book browser
# ----------------------------------------------------------------------


class TemplateBookScreen:
    """Read-only browser for message templates."""

    def __init__(self, app: PostcardsApp) -> None:
        self._app = app

    def build(self) -> Any:
        from textual.binding import Binding
        from textual.containers import Container, Vertical
        from textual.screen import Screen
        from textual.widgets import Button, Footer, Header, Static

        app = self._app

        class _Screen(Screen[None]):
            BINDINGS: ClassVar[list] = [
                Binding("escape", "app.pop_screen", "Back"),
                Binding("q", "quit_app", "Quit"),
            ]

            def compose(self) -> Any:
                yield Header(show_clock=False)
                with Container(id="templates"):
                    yield Static(
                        "[bold]Templates[/bold] — read-only in the TUI. "
                        "Edit templates with: postcards templates add NAME --body ...",
                        id="tpl-help",
                    )
                    templates = list(app.list_templates())
                    if not templates:
                        yield Static("(no templates yet)", id="tpl-empty")
                    else:
                        with Vertical(id="tpl-list"):
                            for name, desc in templates:
                                yield Static(f"  [bold]{name}[/bold] — {desc}", classes="entry")
                    yield Static("", id="tpl-spacer")
                    yield Button("Back (esc)", id="btn-back")
                yield Footer()

            def on_button_pressed(self, event: Any) -> None:
                if event.button.id == "btn-back":
                    self.app.pop_screen()

            def action_quit_app(self) -> None:
                self.app.exit()

        return _Screen()


# ----------------------------------------------------------------------
# Compose screen
# ----------------------------------------------------------------------


class ComposeScreen:
    """The form the user fills in to compose a postcard.

    The screen exposes four editable fields (recipient,
    sender, picture path, message) plus three buttons
    (Preview, Send dry-run, Send real). The Send-real path
    pushes :class:`SendConfirmScreen`; the user must type
    ``YES`` there to actually call the network.

    Why Textual widgets and not a plain form
    ----------------------------------------

    Textual's :class:`Input`, :class:`TextArea`, and
    :class:`Select` widgets are accessible, keyboard-driven,
    and rendering-aware (they redraw on resize). Writing a
    custom form would mean re-implementing all of that.
    """

    def __init__(self, app: PostcardsApp) -> None:
        self._app = app

    def build(self) -> Any:
        from textual.binding import Binding
        from textual.containers import Container, Horizontal
        from textual.screen import Screen
        from textual.widgets import Button, Checkbox, Footer, Header, Input, Label, Select, Static

        app = self._app
        form = app.form

        recipient_options = [(label, name) for name, label in app.list_recipients()]
        sender_options = [(label, name) for name, label in app.list_senders()]
        template_options = [("(none)", "__none__")]
        for name, desc in app.list_templates():
            template_options.append((f"{name} — {desc}", name))

        class _Screen(Screen[None]):
            BINDINGS: ClassVar[list] = [
                Binding("escape", "app.pop_screen", "Back"),
                Binding("ctrl+p", "preview", "Preview"),
                Binding("ctrl+s", "send_dry", "Send (dry-run)"),
                Binding("ctrl+shift+s", "send_real", "Send (real)"),
            ]

            def compose(self) -> Any:
                yield Header(show_clock=False)
                with Container(id="compose-form"):
                    yield Static("[bold]Compose a postcard[/bold]", id="compose-title")
                    yield Label("Recipient")
                    yield Select(
                        options=recipient_options
                        or [("(no recipients — add one first)", "__none__")],
                        value=form.recipient_name if form.recipient_name else Select.NULL,
                        id="select-recipient",
                        allow_blank=not bool(recipient_options),
                    )
                    yield Label("Sender (optional — defaults to recipient)")
                    yield Select(
                        options=[
                            ("(use recipient as sender)", "__recipient__"),
                            *sender_options,
                        ],
                        value=form.sender_name if form.sender_name else "__recipient__",
                        id="select-sender",
                        allow_blank=False,
                    )
                    yield Label("Picture (path to a local image, or 'plugin:value')")
                    yield Input(
                        value=form.picture_path or "",
                        placeholder="/path/to/photo.jpg",
                        id="input-picture",
                    )
                    yield Label("Message (max 500 chars)")
                    yield Input(
                        value=form.message,
                        placeholder="Hello from Zuerich!",
                        id="input-message",
                        max_length=MESSAGE_MAX_LEN,
                    )
                    yield Static(
                        f"Chars: {len(form.message)}/{MESSAGE_MAX_LEN}",
                        id="char-counter",
                    )
                    yield Label("Template (optional)")
                    yield Select(
                        options=template_options,
                        value=form.template_name if form.template_name else Select.NULL,
                        id="select-template",
                        allow_blank=True,
                    )
                    yield Label("Template vars (KEY=VALUE, comma-separated)")
                    yield Input(
                        value=",".join(form.template_vars),
                        placeholder="name=Alice,occasion=birthday",
                        id="input-template-vars",
                    )
                    yield Checkbox(
                        "Dry-run (no network; safe default)",
                        value=form.dry_run,
                        id="checkbox-dry-run",
                    )
                    with Horizontal(id="compose-buttons"):
                        yield Button("Preview (Ctrl+P)", id="btn-preview", variant="primary")
                        yield Button("Send dry-run (Ctrl+S)", id="btn-send-dry")
                        yield Button("Send real (Ctrl+Shift+S)", id="btn-send-real")
                        yield Button("Back (esc)", id="btn-back")
                yield Footer()

            # ------------------------------------------------------------------
            # Form updates
            # ------------------------------------------------------------------

            def on_input_changed(self, event: Any) -> None:
                if event.input.id == "input-picture":
                    form.picture_path = event.value or None
                elif event.input.id == "input-message":
                    form.message = event.value
                    counter = self.query_one("#char-counter", Static)
                    counter.update(f"Chars: {len(form.message)}/{MESSAGE_MAX_LEN}")
                elif event.input.id == "input-template-vars":
                    form.template_vars = [v.strip() for v in event.value.split(",") if v.strip()]

            def on_select_changed(self, event: Any) -> None:
                if event.select.id == "select-recipient":
                    val = str(event.value) if event.value is not None else ""
                    form.recipient_name = val if val and val != "__none__" else None
                elif event.select.id == "select-sender":
                    val = str(event.value) if event.value is not None else ""
                    if val == "__recipient__":
                        form.sender_name = None
                    elif val and val != "__none__":
                        form.sender_name = val
                elif event.select.id == "select-template":
                    val = str(event.value) if event.value is not None else ""
                    form.template_name = val if val and val != "__none__" else None

            def on_checkbox_changed(self, event: Any) -> None:
                if event.checkbox.id == "checkbox-dry-run":
                    form.dry_run = bool(event.value)

            # ------------------------------------------------------------------
            # Actions
            # ------------------------------------------------------------------

            def on_button_pressed(self, event: Any) -> None:
                bid = event.button.id
                if bid == "btn-preview":
                    self.action_preview()
                elif bid == "btn-send-dry":
                    self.action_send_dry()
                elif bid == "btn-send-real":
                    self.action_send_real()
                elif bid == "btn-back":
                    self.app.pop_screen()

            def action_preview(self) -> None:
                try:
                    self._validate_form()
                except CLIError as exc:
                    self._notify(str(exc), severity="error")
                    return
                output_path = form.preview_path()
                try:
                    written = app.render_preview(output_path)
                except CLIError as exc:
                    self._notify(str(exc), severity="error")
                    return
                self.app.push_screen(PreviewScreen(app, written).build())

            def action_send_dry(self) -> None:
                form.dry_run = True
                self._dispatch_send()

            def action_send_real(self) -> None:
                # First check: refuse to even open the confirm
                # modal when dry-run is on. The user has to
                # explicitly turn dry-run off to send for real.
                if form.dry_run:
                    self._notify(
                        "Turn off 'Dry-run' to enable the real send button.",
                        severity="warning",
                    )
                    return
                self.app.push_screen(SendConfirmScreen(app).build())

            def _dispatch_send(self) -> None:
                try:
                    self._validate_form()
                except CLIError as exc:
                    self._notify(str(exc), severity="error")
                    return
                # Delegate to do_command_send via the legacy
                # Postcards.do_command_send flow.
                from postcards.postcards import Postcards

                config_dict = app.build_in_memory_config()
                args = app.build_send_namespace()
                cards = Postcards()
                try:
                    cards.do_command_send(args, config_dict=config_dict)
                except SystemExit as exc:
                    # do_command_send calls sys.exit(1) on
                    # "no valid account given". Surface that as a
                    # TUI notification rather than killing the app.
                    if exc.code not in (None, 0):
                        self._notify(
                            "send aborted: no valid account (check credentials / keyring / config)",
                            severity="error",
                        )
                    return
                self._notify(
                    "Dry-run OK — postcard would have been sent.",
                    severity="information",
                )

            def _validate_form(self) -> None:
                if not form.has_recipient():
                    raise CLIError("pick a recipient first")
                if not form.message and not form.picture_path:
                    raise CLIError("either a message or a picture is required")
                if len(form.message) > MESSAGE_MAX_LEN:
                    raise CLIError(f"message too long ({len(form.message)} > {MESSAGE_MAX_LEN})")

            def _notify(self, message: str, *, severity: str = "information") -> None:
                """Show a transient notification to the user."""
                # textual.app.App.notify accepts ``severity`` as a
                # ``Literal["information", "warning", "error"]``;
                # we keep the parameter stringly-typed at the
                # boundary so the test suite can call it with
                # any value without mypy complaining about the
                # literals the screens happen to use.
                allowed = ("information", "warning", "error")
                self.app.notify(
                    message,
                    severity=severity if severity in allowed else "information",  # type: ignore[arg-type]
                    timeout=5,
                )

        return _Screen()


# ----------------------------------------------------------------------
# Preview screen
# ----------------------------------------------------------------------


class PreviewScreen:
    """Modal that shows the result of a preview render."""

    def __init__(self, app: PostcardsApp, output_path: Path) -> None:
        self._app = app
        self._output_path = output_path

    def build(self) -> Any:
        from textual.binding import Binding
        from textual.containers import Container
        from textual.screen import ModalScreen
        from textual.widgets import Button, Footer, Static

        output_path = self._output_path
        size = output_path.stat().st_size if output_path.exists() else 0

        class _Screen(ModalScreen[None]):
            BINDINGS: ClassVar[list] = [
                Binding("escape", "app.pop_screen", "Close"),
                Binding("enter", "app.pop_screen", "Close"),
            ]

            DEFAULT_CSS: ClassVar[str] = """
            _Screen {
                align: center middle;
            }
            #preview-modal {
                width: 70%;
                height: auto;
                border: round $primary;
                background: $surface;
                padding: 1 2;
            }
            """

            def compose(self) -> Any:
                with Container(id="preview-modal"):
                    yield Static("[bold]Preview rendered[/bold]")
                    yield Static(f"Path: {output_path}")
                    yield Static(f"Size: {size} bytes")
                    yield Static(
                        "Open this file with your image viewer to inspect the "
                        "rendered card. The file is on local disk; no network was "
                        "touched.",
                    )
                    yield Static("")
                    yield Button("Close (esc / enter)", id="btn-close")
                yield Footer()

            def on_button_pressed(self, event: Any) -> None:
                if event.button.id == "btn-close":
                    self.app.pop_screen()

        return _Screen()


# ----------------------------------------------------------------------
# Send confirm screen
# ----------------------------------------------------------------------


class SendConfirmScreen:
    """Last-line-of-defence modal before a real send.

    The user must type ``YES`` (uppercase, exactly) to enable
    the Send button. This is intentionally stricter than a
    plain y/N prompt — the cost of an accidental send is one
    of the day's free cards.
    """

    def __init__(self, app: PostcardsApp) -> None:
        self._app = app

    def build(self) -> Any:
        from textual.binding import Binding
        from textual.containers import Container, Horizontal
        from textual.screen import ModalScreen
        from textual.widgets import Button, Input, Static

        app = self._app
        form = app.form

        class _Screen(ModalScreen[None]):
            BINDINGS: ClassVar[list] = [
                Binding("escape", "app.pop_screen", "Cancel"),
            ]

            DEFAULT_CSS: ClassVar[str] = """
            _Screen {
                align: center middle;
            }
            #confirm-modal {
                width: 60%;
                height: auto;
                border: thick $error;
                background: $surface;
                padding: 1 2;
            }
            #confirm-input {
                margin: 1 0;
            }
            """

            def compose(self) -> Any:
                with Container(id="confirm-modal"):
                    yield Static("[bold red]Confirm real send[/bold red]")
                    yield Static(
                        "This will authenticate against SwissID and consume the "
                        "free 1-card-per-day quota. Type YES to enable the button.",
                    )
                    yield Static(f"Recipient: {form.recipient_name or '(none)'}")
                    yield Static(f"Picture:   {form.picture_path or '(none — text only)'}")
                    yield Static(
                        f"Message:   {form.message[:60]}{'...' if len(form.message) > 60 else ''}"
                    )
                    yield Input(placeholder="YES", id="confirm-input")
                    with Horizontal(id="confirm-buttons"):
                        yield Button(
                            "Send (disabled until you type YES)",
                            id="btn-confirm-send",
                            disabled=True,
                            variant="error",
                        )
                        yield Button("Cancel (esc)", id="btn-cancel")

            def on_input_changed(self, event: Any) -> None:
                if event.input.id == "confirm-input":
                    btn = self.query_one("#btn-confirm-send", Button)
                    btn.disabled = event.value.strip() != "YES"

            def on_button_pressed(self, event: Any) -> None:
                bid = event.button.id
                if bid == "btn-confirm-send":
                    self._dispatch_send()
                elif bid == "btn-cancel":
                    self.app.pop_screen()

            def _dispatch_send(self) -> None:
                from postcards.postcards import Postcards

                config_dict = app.build_in_memory_config()
                args = app.build_send_namespace()
                cards = Postcards()
                try:
                    cards.do_command_send(args, config_dict=config_dict)
                except SystemExit as exc:
                    if exc.code not in (None, 0):
                        self.app.notify(
                            "send aborted: no valid account",
                            severity="error",
                        )
                    self.app.pop_screen()
                    return
                self.app.notify("Card sent.", severity="information")
                self.app.pop_screen()

        return _Screen()


# ----------------------------------------------------------------------
# Help screen
# ----------------------------------------------------------------------


class HelpScreen:
    """The TUI's about / keybindings screen."""

    def __init__(self, app: PostcardsApp) -> None:
        self._app = app

    def build(self) -> Any:
        from textual.binding import Binding
        from textual.containers import Container
        from textual.screen import Screen
        from textual.widgets import Button, Footer, Header, Static

        class _Screen(Screen[None]):
            BINDINGS: ClassVar[list] = [
                Binding("escape", "app.pop_screen", "Back"),
                Binding("q", "quit_app", "Quit"),
            ]

            def compose(self) -> Any:
                yield Header(show_clock=False)
                with Container(id="help"):
                    yield Static("[bold]Postcards TUI — Help[/bold]")
                    yield Static("")
                    yield Static("Keyboard shortcuts:")
                    yield Static("  c — Compose a postcard")
                    yield Static("  a — Browse address book")
                    yield Static("  t — Browse templates")
                    yield Static("  ? — This help screen")
                    yield Static("  q — Quit the TUI")
                    yield Static("  esc — Back / Cancel")
                    yield Static("")
                    yield Static("The TUI defaults to dry-run mode: no card is sent,")
                    yield Static("no SwissID login, no network call. Toggle the")
                    yield Static("'Dry-run' checkbox off and type YES at the")
                    yield Static("confirm modal to send for real.")
                    yield Static("")
                    yield Static("Data on disk: address book + templates live in")
                    yield Static("$XDG_DATA_HOME/postcards (override with POSTCARDS_DATA_DIR).")
                    yield Static("")
                    yield Static("See also: postcards doctor, postcards --help")
                    yield Static("")
                    yield Button("Back (esc)", id="btn-back")
                yield Footer()

            def on_button_pressed(self, event: Any) -> None:
                if event.button.id == "btn-back":
                    self.app.pop_screen()

            def action_quit_app(self) -> None:
                self.app.exit()

        return _Screen()


__all__ = [
    "AddressBookScreen",
    "ComposeScreen",
    "HelpScreen",
    "MainMenuScreen",
    "PreviewScreen",
    "SendConfirmScreen",
    "TemplateBookScreen",
]
