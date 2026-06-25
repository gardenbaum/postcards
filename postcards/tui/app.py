"""Textual-based TUI for ``postcards``.

The :class:`PostcardsApp` is the top-level :class:`textual.app.App`
the ``postcards tui`` console-script target drives. It owns:

* the :class:`~postcards.tui.state.ComposeForm` instance the
  user is editing;
* a reference to the address book and template book (read once
  on startup, never written by the TUI);
* the screen stack — Compose / Preview / Send-Confirm.

The app deliberately does NOT instantiate the address-book /
template-book writers. The TUI is read-only against the user's
data; mutations happen via the existing CLI commands
(``postcards addresses add ...``,
``postcards templates add ...``) so a single source of truth
persists the JSON files atomically.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from postcards.addressbook.models import TemplateError
from postcards.addressbook.storage import load_address_book, load_template_book
from postcards.cli.errors import CLIError
from postcards.tui.screens import MainMenuScreen
from postcards.tui.state import ComposeForm

logger = logging.getLogger(__name__)


class PostcardsApp:
    """The TUI application.

    This is intentionally NOT a :class:`textual.app.App`
    subclass directly — the screens own their own App
    contexts and use :meth:`App.push_screen` /
    :meth:`App.pop_screen`. Wrapping the screen stack in a
    plain class lets the test harness drive the screens
    without booting the full Textual event loop machinery.

    The test harness in :mod:`tests.test_tui` instantiates
    this class, calls :meth:`initial_screen`, and feeds the
    returned screen into :class:`textual.pilot.Pilot`.
    """

    def __init__(
        self,
        *,
        config_path: Path = Path("config.json"),
        accounts_file: Path | None = None,
        dry_run: bool = True,
        data_dir: Path | None = None,
    ) -> None:
        """Initialise the app and load the user's books.

        Parameters
        ----------
        config_path:
            Path to the legacy ``config.json`` (passed to the
            backend). Mirrors the ``-c/--config`` CLI option.
        accounts_file:
            Optional path to a dedicated accounts file
            (``-a/--accounts-file``).
        dry_run:
            Whether the Send button defaults to dry-run. The
            CLI passes ``--dry-run`` by default; the TUI
            inherits the same default.
        data_dir:
            Override for ``$XDG_DATA_HOME/postcards``. The CLI
            honours the same env var via
            :data:`POSTCARDS_DATA_DIR`; the TUI accepts the
            override so tests can point at a temp directory.
        """
        self._data_dir = data_dir
        self._config_path = config_path
        self._accounts_file = accounts_file
        self.form = ComposeForm(
            config_path=config_path,
            accounts_file=accounts_file,
            dry_run=dry_run,
        )
        # Books are loaded lazily — the address-book path can
        # be slow on Windows where the keyring lookup blocks.
        # The screens call :meth:`get_address_book` /
        # :meth:`get_template_book` when they need the data.
        self._address_book: Any | None = None
        self._template_book: Any | None = None

    # ------------------------------------------------------------------
    # Book accessors
    # ------------------------------------------------------------------

    def get_address_book(self) -> Any:
        """Return the user's address book (loaded on first access)."""
        if self._address_book is None:
            self._address_book = load_address_book()
        return self._address_book

    def get_template_book(self) -> Any:
        """Return the user's template book (loaded on first access)."""
        if self._template_book is None:
            self._template_book = load_template_book()
        return self._template_book

    # ------------------------------------------------------------------
    # Pipeline bridge
    # ------------------------------------------------------------------

    def render_preview(self, output_path: Path) -> Path:
        """Render the current form into ``output_path``.

        Delegates to :mod:`postcards.render` after building
        a :class:`postcards.models.Postcard` from the form
        and the address book. The method is pure-Python (no
        Textual calls) so tests can exercise it without a
        Pilot harness.
        """
        from postcards.image import ImageError, prepare_postcard_image
        from postcards.models import Message, Postcard
        from postcards.render import RenderError, render_postcard

        recipient_name = self.form.recipient_name
        if not recipient_name:
            raise CLIError("no recipient selected")
        book = self.get_address_book()
        try:
            recipient_entry = book.get(recipient_name)
        except KeyError as exc:
            raise CLIError(f"unknown recipient {recipient_name!r}") from exc

        sender_entry = None
        if self.form.sender_name:
            try:
                sender_entry = book.get(self.form.sender_name)
            except KeyError as exc:
                raise CLIError(f"unknown sender {self.form.sender_name!r}") from exc

        recipient_address = recipient_entry.address
        sender_address = sender_entry.address if sender_entry is not None else recipient_address

        message_text = self.form.effective_message()
        msg = Message.from_text(message_text)

        picture_bytes: bytes | None = None
        if self.form.picture_path:
            picture_path = Path(self.form.picture_path)
            if not picture_path.is_file():
                raise CLIError(f"picture file not found: {picture_path}")
            picture_bytes = picture_path.read_bytes()
            try:
                picture_bytes = prepare_postcard_image(picture_bytes)
            except ImageError as exc:
                raise CLIError(f"cannot process picture: {exc}") from exc

        postcard = Postcard(
            sender=sender_address,
            recipient=recipient_address,
            message=msg,
            picture=picture_bytes,
        )
        try:
            return render_postcard(postcard, output_path)
        except RenderError as exc:
            raise CLIError(f"cannot render preview: {exc}") from exc

    def build_send_namespace(self) -> argparse.Namespace:
        """Build the :class:`argparse.Namespace` ``do_command_send`` expects.

        Mirrors the builder in :mod:`postcards.cli.commands.send`
        but reads from :attr:`form` instead of Typer options.
        The shape is identical so the existing
        :func:`postcards.postcards.Postcards.do_command_send`
        flow runs unchanged.
        """
        # The CLI path expects the *resolved* message after
        # template substitution. We do that resolution here so
        # the TUI displays the same final text the backend
        # would receive.
        message = self._render_template_message()

        return argparse.Namespace(
            config_file=[str(self.form.config_path)],
            accounts_file=(str(self.form.accounts_file) if self.form.accounts_file else False),
            picture=self._picture_arg(),
            message=[message] if message else [],
            mock=bool(self.form.dry_run),
            test_plugin=False,
            username="",
            password="",
            all_accounts=False,
            key=(None,),
        )

    def _picture_arg(self) -> str | None:
        """Return the value to pass as ``args.picture``.

        The CLI accepts either a local path or a plugin
        reference. The TUI tracks the two separately
        (:attr:`ComposeForm.picture_path` vs
        :attr:`ComposeForm.picture_plugin`); we collapse them
        here into the same string shape ``do_command_send``
        understands.

        For plugins, the format is ``<plugin>:<value>`` (the
        legacy parser convention).
        """
        if self.form.picture_path and self.form.picture_plugin:
            return f"{self.form.picture_plugin}:{self.form.picture_path}"
        return self.form.picture_path

    def _render_template_message(self) -> str:
        """Apply the template engine to the current form message.

        Returns the user's raw message when no template is
        selected, or the rendered template body when a
        template is selected. The function never raises on a
        missing variable — the CLI's :func:`send_cmd` does
        that and surfaces it as a user-friendly error; we
        only want to forward the *intent* here.
        """
        if not self.form.template_name:
            return self.form.message
        book = self.get_template_book()
        try:
            template = book.get(self.form.template_name)
        except (KeyError, TemplateError):
            return self.form.message
        # ``MessageTemplate.render`` accepts a single
        # ``variables`` mapping. The TUI collects
        # ``--var KEY=VALUE`` pairs from a comma-separated
        # ``Input``; convert them here.
        kwargs: dict[str, str] = {}
        for raw in self.form.template_vars:
            if "=" not in raw:
                continue
            key, _, value = raw.partition("=")
            kwargs[key.strip()] = value
        try:
            return template.render(kwargs)
        except Exception:
            return self.form.message

    # ------------------------------------------------------------------
    # Address-book / template convenience (used by the screens)
    # ------------------------------------------------------------------

    def list_recipients(self) -> list[tuple[str, str]]:
        """Return ``[(name, label), ...]`` for every recipient entry.

        ``label`` is a short human-readable summary
        (``"Alice Example — Bahnhofstr. 1, 8000 Zuerich"``).
        """
        book = self.get_address_book()
        out: list[tuple[str, str]] = []
        for name in book.names():
            entry = book.get(name)
            if entry.category.value == "recipient":
                addr = entry.address
                out.append(
                    (
                        name,
                        f"{addr.prename} {addr.lastname} — "
                        f"{addr.street}, {addr.zip_code} {addr.place}",
                    )
                )
        return out

    def list_senders(self) -> list[tuple[str, str]]:
        """Return ``[(name, label), ...]`` for every sender entry."""
        book = self.get_address_book()
        out: list[tuple[str, str]] = []
        for name in book.names():
            entry = book.get(name)
            if entry.category.value == "sender":
                addr = entry.address
                out.append(
                    (
                        name,
                        f"{addr.prename} {addr.lastname} — "
                        f"{addr.street}, {addr.zip_code} {addr.place}",
                    )
                )
        return out

    def list_templates(self) -> Iterable[tuple[str, str]]:
        """Return ``[(name, description), ...]`` for every template."""
        book = self.get_template_book()
        out: list[tuple[str, str]] = []
        for name in book.names():
            tpl = book.get(name)
            desc = tpl.description or "(no description)"
            out.append((name, desc))
        return out

    # ------------------------------------------------------------------
    # Entry points
    # ------------------------------------------------------------------

    def initial_screen(self) -> Any:
        """Return the screen the app should push on startup."""
        return MainMenuScreen(self).build()

    async def run_async(self, *, headless: bool = False) -> None:
        """Run the TUI event loop.

        ``headless=True`` is the test entry point — the app
        runs without a real terminal and exits as soon as the
        initial screen has composed. Production code calls
        :func:`run_tui` instead, which sets ``headless=False``.
        """
        from textual.app import App

        class _Runner(App[None]):
            def __init__(self, app: PostcardsApp) -> None:
                super().__init__()
                self._app = app

            def on_mount(self) -> None:
                self.push_screen(self._app.initial_screen())

        runner: Any = _Runner(self)
        if headless:
            await runner.run_test()
        else:
            await runner.run_async()

    # ------------------------------------------------------------------
    # CLI integration helpers
    # ------------------------------------------------------------------

    def build_in_memory_config(self) -> dict[str, Any]:
        """Build an in-memory ``config_dict`` for ``do_command_send``.

        Used when the user has not created a ``config.json``
        yet (the TUI is the user's first interaction with
        ``postcards``). The returned dict matches the shape
        :func:`postcards.cli.config_io.read_config` returns so
        :meth:`postcards.postcards.Postcards.do_command_send`
        can accept it as ``config_dict=`` without further
        adaptation.

        The recipient and sender dicts mirror the
        ``AddressSpec.to_dict`` shape, which is the same
        shape ``postcards config init`` writes to disk.
        """
        if not self.form.recipient_name:
            raise CLIError("no recipient selected")
        book = self.get_address_book()
        try:
            recipient_entry = book.get(self.form.recipient_name)
        except (KeyError, TemplateError) as exc:
            raise CLIError(f"unknown recipient {self.form.recipient_name!r}") from exc

        recipient_dict = self._addressspec_to_dict(recipient_entry.address)

        sender_dict: dict[str, Any]
        if self.form.sender_name:
            try:
                sender_entry = book.get(self.form.sender_name)
            except (KeyError, TemplateError) as exc:
                raise CLIError(f"unknown sender {self.form.sender_name!r}") from exc
            sender_dict = self._addressspec_to_dict(sender_entry.address)
        else:
            # Match the CLI default: sender == recipient.
            sender_dict = recipient_dict

        # ``accounts`` is intentionally empty here; the
        # credentials are sourced from env / keyring /
        # CLI-args at the do_command_send level (see
        # ``_get_accounts``), so the TUI does not need to
        # encode them in the config dict.
        return {
            "recipient": recipient_dict,
            "sender": sender_dict,
            "accounts": [],
            "payload": None,
        }

    @staticmethod
    def _addressspec_to_dict(addr: Any) -> dict[str, Any]:
        """Serialise an :class:`AddressSpec` to the legacy config-file shape.

        The vendored shim reads ``recipient`` / ``sender`` dicts
        via :meth:`postcards.postcards.Postcards._create_recipient`
        / ``_create_sender`, which expect the *config-file*
        keys (``firstname``, ``zipcode``, ``city``) — NOT the
        canonical :class:`AddressSpec` field names
        (``prename``, ``zip_code``, ``place``). This helper
        bridges the two so an address-book entry feeds straight
        into the legacy send flow.

        Mirrors :func:`postcards.cli.commands.send._address_to_legacy_dict`
        so a single source of truth governs the address shape.
        """
        payload: dict[str, Any] = {
            "firstname": addr.prename,
            "lastname": addr.lastname,
            "street": addr.street,
            "zipcode": addr.zip_code,
            "city": addr.place,
        }
        if getattr(addr, "company", ""):
            payload["company"] = addr.company
        if getattr(addr, "country", ""):
            payload["country"] = addr.country
        if getattr(addr, "salutation", ""):
            payload["salutation"] = addr.salutation
        if getattr(addr, "company_addition", ""):
            payload["companyAddition"] = addr.company_addition
        return payload


# ----------------------------------------------------------------------
# Console-script entry point
# ----------------------------------------------------------------------


def run_tui(
    *,
    config_path: Path = Path("config.json"),
    accounts_file: Path | None = None,
    dry_run: bool = True,
) -> None:
    """Launch the interactive TUI.

    Used by the ``postcards tui`` CLI command. Blocks until the
    user quits the app. Raises :class:`CLIError` if the TUI
    cannot start (e.g. ``textual`` is not installed).
    """
    try:
        from textual.app import App
    except ImportError as exc:  # pragma: no cover — guarded at runtime
        raise CLIError(
            "the TUI requires the 'gui' extra; install with 'pip install postcards[gui]'"
        ) from exc

    app = PostcardsApp(
        config_path=config_path,
        accounts_file=accounts_file,
        dry_run=dry_run,
    )

    class _Runner(App[None]):
        def on_mount(self) -> None:
            self.push_screen(app.initial_screen())

    asyncio.run(_Runner().run_async())


__all__ = ["PostcardsApp", "run_tui"]
