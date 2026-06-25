"""NiceGUI UI for composing and sending a postcard with a live preview.

This is the thin presentation layer; all the logic lives in
:mod:`postcards.web.service` (and is unit-tested there). The page wires
form inputs to a per-client :class:`~postcards.web.service.PostcardDraft`
and re-renders the Front/Back PNG preview on every change, so the user
sees exactly what Swiss Post would print — including the 3 mm bleed, the
safe area, the stamp box and the recipient address zone.

The module imports NiceGUI at import time, so it must only be imported
when the optional ``app`` extra is installed (``pip install
'postcards[app]'``). The CLI command guards that.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, replace

from nicegui import ui

from postcards.backend import MockBackend, SwissIdConsumerBackend
from postcards.backend.base import AddressSpec, PostcardBackend
from postcards.image import Orientation
from postcards.models.message import MAX_MESSAGE_LENGTH
from postcards.web import service
from postcards.web.service import PostcardDraft

#: Address fields rendered in the recipient / sender forms, as
#: ``(field_name, label, placeholder)``. ``country`` is last and
#: optional (the Swiss endpoint defaults to CH for the recipient).
_ADDRESS_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("prename", "First name", "Erika"),
    ("lastname", "Last name", "Musterfrau"),
    ("street", "Street + no.", "Hauptstrasse 42"),
    ("zip_code", "ZIP", "8001"),
    ("place", "Place", "Zürich"),
    ("country", "Country (optional)", "CH"),
)


def _png_data_uri(png: bytes) -> str:
    """Encode PNG bytes as a ``data:`` URI for an ``ui.image`` source."""
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _build_backend(name: str) -> PostcardBackend:
    """Construct the backend the user selected (mock by default)."""
    if name == "swissid":
        return SwissIdConsumerBackend()
    return MockBackend()


@dataclass
class _UiState:
    """Per-client toggles captured by the page's event handlers."""

    guides: bool = True
    backend: str = "mock"
    dry_run: bool = True


@ui.page("/")
def _compose_page() -> None:
    """Render the single compose-and-send page (one draft per client)."""
    draft = PostcardDraft()
    state = _UiState()

    ui.colors(primary="#c8102e")  # Swiss Post red
    with ui.header().classes("items-center justify-between"):
        ui.label("Postcards — Swiss Postcard Creator").classes("text-h6")
        ui.label("unofficial · live WYSIWYG preview").classes("text-caption opacity-70")

    # ------------------------------------------------------------------
    # Preview refresh — re-renders both sides into the image elements.
    # ------------------------------------------------------------------
    def refresh() -> None:
        for side, element in (("front", front_img), ("back", back_img)):
            try:
                png = service.render_preview(draft, side=side, guides=state.guides)
                element.set_source(_png_data_uri(png))
            except Exception as exc:
                element.set_source("")
                ui.notify(f"Preview error: {exc}", type="negative")
        problems = service.validate_draft(draft)
        if problems:
            status.classes(replace="text-orange-8")
            status.set_text("⚠ " + " ".join(problems))
            send_button.disable()
        else:
            status.classes(replace="text-green-8")
            status.set_text("✓ Ready to send.")
            send_button.enable()
        remaining = draft.message_remaining()
        msg_counter.set_text(f"{len(draft.message)}/{MAX_MESSAGE_LENGTH}")
        msg_counter.classes(replace="text-red-6" if remaining < 0 else "text-grey-6")

    # ------------------------------------------------------------------
    # Field-change handlers.
    # ------------------------------------------------------------------
    def on_message(value: str) -> None:
        draft.message = value
        refresh()

    def make_addr_handler(kind: str, field_name: str):
        def handler(value: str) -> None:
            current: AddressSpec = getattr(draft, kind)
            setattr(draft, kind, replace(current, **{field_name: value}))
            refresh()

        return handler

    def on_upload(event) -> None:
        raw = event.content.read()
        try:
            draft.picture = service.process_image(raw, orientation=Orientation.AUTO)
            draft.picture_error = ""
            ui.notify(f"Loaded {event.name}", type="positive")
        except Exception as exc:
            draft.picture = None
            draft.picture_error = str(exc)
            ui.notify(f"Could not load image: {exc}", type="negative")
        refresh()

    def clear_image() -> None:
        draft.picture = None
        refresh()

    def do_send() -> None:
        backend = _build_backend(state.backend)
        outcome = service.send_draft(
            draft,
            backend=backend,
            username=username_input.value if state.backend == "swissid" else "",
            password=password_input.value if state.backend == "swissid" else "",
            dry_run=state.dry_run,
        )
        ui.notify(
            outcome.message + (f" ({outcome.confirmation})" if outcome.confirmation else ""),
            type="positive" if outcome.ok else "negative",
            timeout=6000,
        )

    # ------------------------------------------------------------------
    # Layout: form (left) + live preview (right).
    # ------------------------------------------------------------------
    with ui.row().classes("w-full no-wrap gap-6 p-4"):
        # ---- Form column -------------------------------------------------
        with ui.column().classes("gap-4").style("min-width: 380px; max-width: 440px"):
            with ui.card().classes("w-full"):
                ui.label("Picture").classes("text-subtitle1")
                ui.upload(on_upload=on_upload, auto_upload=True, max_files=1).props(
                    "accept=image/* flat bordered"
                ).classes("w-full")
                with ui.row().classes("items-center gap-2"):
                    ui.button("Clear picture", on_click=clear_image).props("flat dense")
                    ui.label("→ leave empty for a text-only card").classes(
                        "text-caption opacity-70"
                    )

            with ui.card().classes("w-full"):
                ui.label("Message").classes("text-subtitle1")
                ui.textarea(placeholder="Liebe Grüsse aus den Bergen …").on_value_change(
                    lambda e: on_message(e.value or "")
                ).props("outlined autogrow").classes("w-full")
                msg_counter = ui.label(f"0/{MAX_MESSAGE_LENGTH}").classes(
                    "text-caption text-grey-6 self-end"
                )

            with ui.card().classes("w-full"):
                ui.label("Recipient").classes("text-subtitle1")
                _address_form("recipient", make_addr_handler)

            with ui.card().classes("w-full"):
                ui.label("Sender").classes("text-subtitle1")
                _address_form("sender", make_addr_handler)

            with ui.card().classes("w-full"):
                ui.label("Send").classes("text-subtitle1")
                backend_select = (
                    ui.select(
                        {
                            "mock": "Mock (safe · nothing is sent)",
                            "swissid": "SwissID (live Swiss Post)",
                        },
                        value="mock",
                        label="Backend",
                    )
                    .props("outlined dense")
                    .classes("w-full")
                )
                cred_box = ui.column().classes("w-full gap-2")
                with cred_box:
                    username_input = (
                        ui.input("SwissID e-mail").props("outlined dense").classes("w-full")
                    )
                    password_input = (
                        ui.input("SwissID password")
                        .props("outlined dense type=password")
                        .classes("w-full")
                    )
                    ui.label(
                        "Live login can require 2FA / fail Swiss Post anomaly checks — "
                        "see the README. Credentials are used only for this send."
                    ).classes("text-caption opacity-70")
                cred_box.set_visibility(False)

                def on_backend(value: str) -> None:
                    state.backend = value
                    cred_box.set_visibility(value == "swissid")

                backend_select.on_value_change(lambda e: on_backend(e.value))

                dry_switch = ui.switch("Dry-run (validate only, don't send)", value=True)

                def on_dry_run(value: bool) -> None:
                    state.dry_run = value

                dry_switch.on_value_change(lambda e: on_dry_run(bool(e.value)))

                send_button = (
                    ui.button("Send postcard", on_click=do_send)
                    .props("color=primary")
                    .classes("w-full")
                )
                status = ui.label("").classes("text-caption")

        # ---- Preview column ---------------------------------------------
        with ui.column().classes("grow gap-3").style("min-width: 480px"):
            with ui.row().classes("items-center gap-3"):
                ui.label("Live preview").classes("text-h6")
                guide_switch = ui.switch("Print guides", value=True)

                def on_guides(value: bool) -> None:
                    state.guides = value
                    refresh()

                guide_switch.on_value_change(lambda e: on_guides(bool(e.value)))
            ui.label("Front (A6 landscape · 3 mm bleed)").classes("text-subtitle2 opacity-80")
            front_img = ui.image().classes("w-full rounded shadow-2").style("max-width: 760px")
            ui.label("Back (message · address · stamp area)").classes("text-subtitle2 opacity-80")
            back_img = ui.image().classes("w-full rounded shadow-2").style("max-width: 760px")

    refresh()


def _address_form(kind: str, make_handler) -> None:
    """Render the six address inputs for ``kind`` (``recipient``/``sender``)."""
    with ui.column().classes("w-full gap-2"):
        for field_name, label, placeholder in _ADDRESS_FIELDS:
            handler = make_handler(kind, field_name)
            ui.input(label, placeholder=placeholder).on_value_change(
                lambda e, h=handler: h(e.value or "")
            ).props("outlined dense").classes("w-full")


def run_app(
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    show: bool = True,
    reload: bool = False,
) -> None:
    """Launch the NiceGUI server (blocks until the user quits).

    Called by the ``postcards app`` CLI command. ``show=True`` opens the
    default browser; ``reload`` is off in production (it requires running
    the module as ``__main__``).
    """
    ui.run(
        host=host,
        port=port,
        show=show,
        reload=reload,
        title="Postcards — Swiss Postcard Creator",
        favicon="📮",
    )


__all__ = ["run_app"]
