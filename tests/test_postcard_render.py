"""Tests for the offline postcard renderer.

The renderer (:mod:`postcards.render`) takes a user-facing
:class:`postcards.models.Postcard` and emits a PNG / JPEG / PDF
file the user can inspect without contacting Swiss Post. These
tests exercise the public surface (:func:`render_postcard`,
:func:`render_front`, :func:`render_back`) plus a handful of
the helpers that hold enough logic to fail in interesting ways.

Why in-memory fixtures
----------------------

Like the image-pipeline tests, the renderer tests build small
Pillow images in memory and assemble :class:`Postcard`
instances directly — no fixture files, no network, no SwissID
mock. The renderer only depends on Pillow, so the suite stays
fast and hermetic.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from postcards.backend.base import AddressSpec
from postcards.models import Message, Postcard
from postcards.render import (
    RenderError,
    render_back,
    render_front,
    render_postcard,
)
from postcards.render.postcard_renderer import (
    _BACK_PADDING,
    RENDER_HEIGHT,
    RENDER_WIDTH,
    _format_address_lines,
    _normalise_message,
    _wrap_text,
)

# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _address(
    *,
    prename: str = "Alice",
    lastname: str = "Smith",
    street: str = "Hauptstrasse 1",
    zip_code: str = "8000",
    place: str = "Zurich",
    country: str = "",
    company: str = "",
    company_addition: str = "",
) -> AddressSpec:
    return AddressSpec(
        prename=prename,
        lastname=lastname,
        street=street,
        zip_code=zip_code,
        place=place,
        country=country,
        company=company,
        company_addition=company_addition,
    )


def _new_image(width: int = 320, height: int = 200, color: str = "red") -> Image.Image:
    """Create a tiny in-memory Pillow image (default 320x200, red)."""
    return Image.new("RGB", (width, height), color=color)


def _to_jpeg_bytes(image: Image.Image) -> bytes:
    """Encode ``image`` as JPEG bytes."""
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def _postcard(
    *,
    picture: bytes | None = None,
    message: str = "Hello from Zurich!",
    sender: AddressSpec | None = None,
    recipient: AddressSpec | None = None,
) -> Postcard:
    return Postcard(
        sender=sender or _address(),
        recipient=recipient or _address(prename="Hans", lastname="Muster"),
        message=Message.from_text(message),
        picture=picture,
    )


# ---------------------------------------------------------------------------
# render_front
# ---------------------------------------------------------------------------


def test_render_front_returns_image_at_render_dimensions() -> None:
    """``render_front`` returns a Pillow image at RENDER_WIDTH x RENDER_HEIGHT."""
    image = render_front(_postcard(picture=_to_jpeg_bytes(_new_image())))
    assert image.size == (RENDER_WIDTH, RENDER_HEIGHT)
    assert image.mode == "RGB"


def test_render_front_returns_placeholder_when_no_picture() -> None:
    """Without a picture, the front is a coloured placeholder, not blank white."""
    image = render_front(_postcard(picture=None))
    assert image.size == (RENDER_WIDTH, RENDER_HEIGHT)
    # The placeholder paints a vertical gradient, so the centre pixel
    # is one of the gradient colours (not pure white).
    centre = image.getpixel((image.width // 2, image.height // 2))
    assert centre != (255, 255, 255)


def test_render_front_decodes_picture_bytes() -> None:
    """The picture bytes are decoded into the rendered front panel."""
    picture_bytes = _to_jpeg_bytes(_new_image(color="blue"))
    image = render_front(_postcard(picture=picture_bytes))
    # Sample a non-edge pixel; the rendered front is dominated by the
    # blue picture so the pixel should be close to pure blue.
    pixel = image.getpixel((image.width // 2, image.height // 2))
    assert isinstance(pixel, tuple)
    assert pixel[2] > 200  # strong blue channel
    assert pixel[0] < 50  # weak red channel


def test_render_front_rejects_garbage_picture_bytes() -> None:
    """``render_front`` raises :class:`RenderError` when the picture bytes are invalid."""
    with pytest.raises(RenderError):
        render_front(_postcard(picture=b"this is not an image at all"))


# ---------------------------------------------------------------------------
# render_back
# ---------------------------------------------------------------------------


def test_render_back_returns_image_at_render_dimensions() -> None:
    """``render_back`` returns a Pillow image at RENDER_WIDTH x RENDER_HEIGHT."""
    image = render_back(_postcard())
    assert image.size == (RENDER_WIDTH, RENDER_HEIGHT)
    assert image.mode == "RGB"


def test_render_back_includes_recipient_name() -> None:
    """The recipient's name is painted onto the back panel."""
    recipient = _address(prename="Erika", lastname="Musterfrau")
    image = render_back(_postcard(recipient=recipient))
    # Scan the right half of the back panel (where the address
    # block lives) for any dark pixels — the rendered text is the
    # only non-background content there. We sample across the
    # vertical extent of the panel rather than a single scanline
    # so the assertion does not depend on the exact y-coordinate
    # of the recipient text (which is layout-dependent).
    dark_pixels = 0
    for y in range(_BACK_PADDING, RENDER_HEIGHT - _BACK_PADDING, 10):
        for x in range(RENDER_WIDTH // 2 + _BACK_PADDING, RENDER_WIDTH - _BACK_PADDING, 10):
            pixel = image.getpixel((x, y))
            if isinstance(pixel, tuple) and sum(pixel) < 600:
                dark_pixels += 1
    assert dark_pixels > 0, "expected at least one dark pixel in the address column"


# ---------------------------------------------------------------------------
# render_postcard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("suffix", ["png", "jpg", "jpeg", "pdf"])
def test_render_postcard_writes_file_at_output_path(tmp_path: Path, suffix: str) -> None:
    """``render_postcard`` writes a file at the requested path for each supported suffix."""
    output = tmp_path / f"preview.{suffix}"
    written = render_postcard(_postcard(picture=_to_jpeg_bytes(_new_image())), output)
    assert written == output
    assert output.is_file()
    assert output.stat().st_size > 0


def test_render_postcard_creates_parent_directory(tmp_path: Path) -> None:
    """``render_postcard`` creates missing parent directories."""
    output = tmp_path / "nested" / "deeper" / "preview.png"
    render_postcard(_postcard(), output)
    assert output.is_file()


def test_render_postcard_rejects_unsupported_extension(tmp_path: Path) -> None:
    """Unknown file extensions raise :class:`RenderError`."""
    output = tmp_path / "preview.bmp"
    with pytest.raises(RenderError, match="unsupported output format"):
        render_postcard(_postcard(), output)


def test_render_postcard_png_is_decodable(tmp_path: Path) -> None:
    """The PNG output is a real image that Pillow can re-open."""
    output = tmp_path / "preview.png"
    render_postcard(_postcard(picture=_to_jpeg_bytes(_new_image())), output)
    with Image.open(output) as reopened:
        reopened.load()
        size: tuple[int, int] = reopened.size
    # PNG composite is twice as wide as a single side.
    assert size == (RENDER_WIDTH * 2, RENDER_HEIGHT)


def test_render_postcard_pdf_has_two_pages(tmp_path: Path) -> None:
    """The PDF output is a two-page document (page 1 = front, page 2 = back).

    Pillow's PDF saver writes the file with ``save_all=True`` and
    the back image as ``append_images``, so the resulting PDF
    must carry two pages. Pillow only registers *save* for PDF
    (not *open*), so we inspect the raw bytes rather than re-open
    the file as an image.
    """
    output = tmp_path / "preview.pdf"
    render_postcard(_postcard(picture=_to_jpeg_bytes(_new_image())), output)
    data = output.read_bytes()
    # PDF files always start with the magic "%PDF-".
    assert data.startswith(b"%PDF-")
    # Pillow writes one /Type /Page per page; it also writes one
    # /Type /Pages entry for the page tree root. With a 2-page
    # output that yields 3 references, not 2. The robust assertion
    # is "more than one /Type /Page", which a 1-page PDF would not
    # satisfy.
    page_refs = data.count(b"/Type /Page") + data.count(b"/Type/Page")
    assert page_refs > 1, f"expected multi-page PDF, got {page_refs} page refs"
    # Sanity: a 2-page PDF with two A6-sized images must be
    # at least a few kilobytes.
    assert output.stat().st_size > 5_000


def test_render_postcard_jpeg_quality_is_acceptable(tmp_path: Path) -> None:
    """The JPEG output decodes cleanly into a Pillow image."""
    output = tmp_path / "preview.jpg"
    render_postcard(_postcard(picture=_to_jpeg_bytes(_new_image())), output)
    with Image.open(output) as reopened:
        reopened.load()
    assert reopened.format == "JPEG"
    assert reopened.size == (RENDER_WIDTH * 2, RENDER_HEIGHT)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_format_address_lines_includes_company_and_addition() -> None:
    """``_format_address_lines`` interleaves company, name, and addition."""
    addr = _address(company="Acme AG", company_addition="c/o Reception")
    lines = _format_address_lines(addr, kind="recipient")
    assert lines[0] == "To:"
    assert "Acme AG" in lines
    assert "Alice Smith" in lines
    assert "c/o Reception" in lines


def test_format_address_lines_includes_country_when_set() -> None:
    """``_format_address_lines`` appends the country code when set."""
    addr = _address(country="CH")
    lines = _format_address_lines(addr, kind="sender")
    assert lines[-1] == "CH"


def test_format_address_lines_omits_blank_fields() -> None:
    """Empty optional fields do not produce blank lines."""
    addr = _address(company="", company_addition="", country="")
    lines = _format_address_lines(addr, kind="recipient")
    # No line is just whitespace; no duplicate labels.
    assert all(line.strip() for line in lines)
    assert lines.count("To:") == 1


def test_normalise_message_strips_html_tags() -> None:
    """``_normalise_message`` strips HTML so the renderer treats the message as plain text."""
    assert _normalise_message("Hello <b>world</b>!") == "Hello world!"
    assert _normalise_message("<i>italic</i> text") == "italic text"


def test_normalise_message_converts_br_to_newline() -> None:
    """``<br>`` and ``<br/>`` both become newlines."""
    assert _normalise_message("line1<br>line2") == "line1\nline2"
    assert _normalise_message("line1<br/>line2") == "line1\nline2"
    assert _normalise_message("line1<br />line2") == "line1\nline2"


def test_normalise_message_preserves_ampersand_and_special_chars() -> None:
    """Only tag-like content is stripped; ``&amp;`` etc. pass through untouched."""
    # We deliberately do not decode HTML entities — the message
    # would be displayed as-is by the renderer.
    assert _normalise_message("A & B") == "A & B"


def test_wrap_text_breaks_long_words() -> None:
    """Words longer than the line width are hard-broken."""
    lines = _wrap_text("a" * 100, max_width=100, char_width=10)
    assert all(len(line) <= 10 for line in lines)
    assert "".join(lines) == "a" * 100


def test_wrap_text_breaks_on_whitespace_when_word_too_long() -> None:
    """Multi-word text is broken on whitespace when a single word fits."""
    lines = _wrap_text("hello world", max_width=200, char_width=10)
    assert lines == ["hello world"]


def test_wrap_text_handles_empty_input() -> None:
    """An empty input produces a single empty-line entry (no characters are lost)."""
    assert _wrap_text("", max_width=100, char_width=10) == [""]


def test_wrap_text_preserves_blank_lines_between_paragraphs() -> None:
    """``\\n\\n``-separated paragraphs render with a blank line between them."""
    lines = _wrap_text("first paragraph\n\nsecond paragraph", max_width=200, char_width=10)
    assert "" in lines


__all__ = [
    "test_render_front_returns_image_at_render_dimensions",
]
