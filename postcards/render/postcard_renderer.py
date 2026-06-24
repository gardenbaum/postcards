"""Offline rendering of a :class:`postcards.models.Postcard` to an image.

This module is the implementation behind :mod:`postcards.render`. It
takes the user-facing :class:`postcards.models.Postcard` model (a
frozen dataclass carrying a sender, a recipient, a message, and
optional processed JPEG picture bytes) and emits a flat image file
the user can open in any image viewer to inspect what would be
printed and mailed.

Layout
------

A physical Swiss A6 postcard is 148 mm x 105 mm (landscape). The
Swiss Postcard Creator endpoint accepts JPEGs at 1500 x 1062
landscape (or 1062 x 1500 portrait). For the renderer we stick to
landscape — that matches the most common Postcard Creator upload
mode — and lay the **front** (the picture) on the left and the
**back** (the message + addresses) on the right when the output is
a flat image. For PDF output we emit a two-page document so each
side gets its own page.

The **front** is simply the processed JPEG embedded in
``postcard.picture`` (resized to fit if necessary), or a placeholder
gradient + caption when the postcard is text-only.

The **back** is divided into:

* a left half carrying the message (free-text greeting, plain text
  with simple word-wrap; the Swiss endpoint accepts HTML but the
  renderer is deliberately conservative and treats the message as
  plain text);
* a right half carrying the recipient's address (printed block at
  top) and the sender's address (smaller block at bottom).

Failure modes
-------------

:class:`RenderError` is raised when:

* the picture bytes embedded in the postcard are not a decodable
  image (the image pipeline is supposed to prevent this, but a
  caller who constructs a ``Postcard`` with raw bytes may still
  hit it);
* the requested output format (file extension) is not one of
  Pillow's supported ``.png`` / ``.jpg`` / ``.jpeg`` / ``.pdf``
  formats.

The renderer is otherwise dependency-light: only Pillow is
required, no network, no ``requests``, no SwissID code.
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path
from typing import Final

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError
from PIL.ImageFont import FreeTypeFont

from postcards.backend.base import AddressSpec
from postcards.image import (
    A6_LANDSCAPE_HEIGHT,
    A6_LANDSCAPE_WIDTH,
)
from postcards.models.postcard import Postcard

logger = logging.getLogger(__name__)

#: Output pixel dimensions for the rendered postcard.
#:
#: Matches the landscape A6 size the upstream Swiss Postcard Creator
#: accepts (1500 x 1062). The picture pipeline already produces
#: images at these dimensions, so the front render is a no-op
#: resize when ``postcard.picture`` is populated.
RENDER_WIDTH: Final[int] = A6_LANDSCAPE_WIDTH
RENDER_HEIGHT: Final[int] = A6_LANDSCAPE_HEIGHT

#: Formats the renderer accepts for ``render_postcard``. The mapping
#: is from the file extension (lower-case, no leading dot) to the
#: Pillow ``save(format=...)`` keyword.
SUPPORTED_OUTPUT_FORMATS: Final[frozenset[str]] = frozenset({"png", "jpg", "jpeg", "pdf"})

#: Padding (in pixels) inside the back panel between the edge of the
#: page and the printed content.
_BACK_PADDING: Final[int] = 60

#: Vertical padding (in pixels) between text lines on the back panel.
_BACK_LINE_SPACING: Final[int] = 8

#: Font size for the address block (recipient + sender names).
_ADDRESS_FONT_SIZE: Final[int] = 40

#: Font size for the message block.
_MESSAGE_FONT_SIZE: Final[int] = 36

#: Font size for the "no picture" placeholder caption.
_PLACEHOLDER_FONT_SIZE: Final[int] = 64

#: Approximate width of a font character in pixels at the configured
#: :data:`_MESSAGE_FONT_SIZE`. Used to compute simple word-wrap
#: without measuring every glyph (which is slow under Pillow's
#: default font).
#
#: The value is conservative — slightly overestimating per-character
#: width keeps text from spilling past the panel edge. Visually it
#: leaves a small right margin, which matches the look of a real
#: postcard.
_APPROX_CHAR_WIDTH: Final[int] = 18

#: Approximate width of a font character in pixels at the
#: :data:`_ADDRESS_FONT_SIZE`. Same rationale as
#: :data:`_APPROX_CHAR_WIDTH`.
_APPROX_ADDRESS_CHAR_WIDTH: Final[int] = 22

#: Background colour of the rendered back panel (warm white, evokes
#: paper).
_BACK_BG_COLOR: Final[tuple[int, int, int]] = (252, 250, 244)

#: Background colour used as the placeholder for text-only postcards.
_PLACEHOLDER_BG_TOP: Final[tuple[int, int, int]] = (220, 230, 245)
_PLACEHOLDER_BG_BOTTOM: Final[tuple[int, int, int]] = (180, 200, 230)

#: Text colour used for all printed content.
_TEXT_COLOR: Final[tuple[int, int, int]] = (24, 24, 30)

#: Text colour used for the sender address (lighter than the
#: recipient so the recipient is visually dominant — matches real
#: postcard convention).
_SENDER_TEXT_COLOR: Final[tuple[int, int, int]] = (90, 90, 110)

#: Fallback font path; Pillow's ``load_default`` returns Pillow's
#: built-in bitmap font which is tiny. We try a DejaVu family if
#: available, falling back to the default if not. The lookup is
#: intentionally best-effort: the renderer never crashes because
#: a font is missing.
_FONT_CANDIDATES: Final[tuple[str, ...]] = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
)


class RenderError(ValueError):
    """Raised when a :class:`Postcard` cannot be rendered.

    Wraps Pillow-specific errors (:class:`PIL.UnidentifiedImageError`,
    :class:`OSError`) so callers can catch a single exception type
    when integrating the renderer into the CLI. Mirrors the
    :class:`postcards.image.ImageError` pattern from the image
    pipeline.
    """


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_postcard(postcard: Postcard, output_path: Path) -> Path:
    """Render ``postcard`` to ``output_path`` and return the resolved path.

    The output format is inferred from the file extension of
    ``output_path``. Supported extensions: ``.png`` (single composite
    image with front and back side-by-side), ``.jpg`` / ``.jpeg``
    (same composition as PNG but lossy), ``.pdf`` (two-page document,
    page 1 = front, page 2 = back).

    The caller owns the parent directory; the function does not
    create missing parents.

    Raises
    ------
    RenderError
        When the file extension is unsupported, or the postcard's
        picture bytes are not a decodable image.
    OSError
        When the file cannot be written (propagated from Pillow).
    """
    suffix = output_path.suffix.lower().lstrip(".")
    if suffix not in SUPPORTED_OUTPUT_FORMATS:
        raise RenderError(
            f"unsupported output format {suffix!r}; "
            f"supported formats are {sorted(SUPPORTED_OUTPUT_FORMATS)}"
        )

    front = render_front(postcard)
    back = render_back(postcard)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if suffix == "pdf":
        _save_pdf(front, back, output_path)
    else:
        _save_composite(front, back, output_path, suffix=suffix)

    logger.info("preview written to %s", output_path)
    return output_path


def render_front(postcard: Postcard) -> Image.Image:
    """Return a Pillow image of the front of ``postcard``.

    When ``postcard.picture`` is set, the embedded JPEG bytes are
    decoded and returned at the renderer's pixel dimensions (the
    image pipeline already emits 1500 x 1062, so this is a no-op
    resize). When ``postcard.picture`` is ``None``, a coloured
    gradient placeholder is returned with a "Text only" caption.
    """
    canvas = Image.new("RGB", (RENDER_WIDTH, RENDER_HEIGHT), (255, 255, 255))
    if postcard.picture is None:
        return _render_placeholder_front(canvas)
    try:
        picture: Image.Image = Image.open(io.BytesIO(postcard.picture))
        picture.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise RenderError(f"cannot decode postcard picture: {exc}") from exc
    if picture.mode != "RGB":
        picture = picture.convert("RGB")
    if picture.size != (RENDER_WIDTH, RENDER_HEIGHT):
        picture = picture.resize((RENDER_WIDTH, RENDER_HEIGHT), Image.Resampling.LANCZOS)
    canvas.paste(picture, (0, 0))
    return canvas


def render_back(postcard: Postcard) -> Image.Image:
    """Return a Pillow image of the back of ``postcard``.

    The back panel shows the message on the left half and the
    recipient + sender addresses on the right half. When the
    message is empty the left half renders as a faint placeholder
    so the user can tell at a glance that the card would be
    address-only.
    """
    canvas = Image.new("RGB", (RENDER_WIDTH, RENDER_HEIGHT), _BACK_BG_COLOR)
    draw = ImageDraw.Draw(canvas)
    _draw_panel_split(draw)
    _draw_message_block(draw, postcard)
    _draw_address_block(draw, postcard)
    return canvas


# ---------------------------------------------------------------------------
# Composite / PDF writers
# ---------------------------------------------------------------------------


def _save_composite(
    front: Image.Image, back: Image.Image, output_path: Path, *, suffix: str
) -> None:
    """Save front+back as a single image (side-by-side)."""
    composite = Image.new(
        "RGB",
        (RENDER_WIDTH * 2, RENDER_HEIGHT),
        (255, 255, 255),
    )
    composite.paste(front, (0, 0))
    composite.paste(back, (RENDER_WIDTH, 0))
    pillow_format = "JPEG" if suffix in {"jpg", "jpeg"} else "PNG"
    save_kwargs: dict[str, object] = {}
    if pillow_format == "JPEG":
        save_kwargs["quality"] = 92
        save_kwargs["optimize"] = True
    composite.save(output_path, format=pillow_format, **save_kwargs)


def _save_pdf(front: Image.Image, back: Image.Image, output_path: Path) -> None:
    """Save front+back as a two-page PDF.

    Pillow's PDF saver requires RGB images, so both sides are
    converted defensively before the first page is written. Pillow
    then appends the second image as page 2 when ``save_all=True``.
    """
    front_rgb = front.convert("RGB")
    back_rgb = back.convert("RGB")
    front_rgb.save(
        output_path,
        format="PDF",
        save_all=True,
        append_images=[back_rgb],
    )


# ---------------------------------------------------------------------------
# Front rendering helpers
# ---------------------------------------------------------------------------


def _render_placeholder_front(canvas: Image.Image) -> Image.Image:
    """Paint the "text-only" placeholder used when no picture is set."""
    draw = ImageDraw.Draw(canvas)
    for y in range(canvas.height):
        # Linear interpolation between the two placeholder colours so
        # the placeholder looks intentional rather than flat.
        ratio = y / max(canvas.height - 1, 1)
        r = int(_PLACEHOLDER_BG_TOP[0] * (1 - ratio) + _PLACEHOLDER_BG_BOTTOM[0] * ratio)
        g = int(_PLACEHOLDER_BG_TOP[1] * (1 - ratio) + _PLACEHOLDER_BG_BOTTOM[1] * ratio)
        b = int(_PLACEHOLDER_BG_TOP[2] * (1 - ratio) + _PLACEHOLDER_BG_BOTTOM[2] * ratio)
        draw.line([(0, y), (canvas.width, y)], fill=(r, g, b))
    font = _load_font(_PLACEHOLDER_FONT_SIZE)
    label = "No picture (text-only card)"
    bbox = draw.textbbox((0, 0), label, font=font)
    text_width = int(bbox[2] - bbox[0])
    text_height = int(bbox[3] - bbox[1])
    x = (int(canvas.width) - text_width) // 2
    y = (int(canvas.height) - text_height) // 2
    draw.text((x, y), label, fill=_TEXT_COLOR, font=font)
    return canvas


# ---------------------------------------------------------------------------
# Back rendering helpers
# ---------------------------------------------------------------------------


def _draw_panel_split(draw: ImageDraw.ImageDraw) -> None:
    """Draw a faint vertical divider between the message half and the address half."""
    x = RENDER_WIDTH // 2
    draw.line(
        [(x, _BACK_PADDING // 2), (x, RENDER_HEIGHT - _BACK_PADDING // 2)],
        fill=(210, 205, 195),
        width=2,
    )


def _draw_message_block(draw: ImageDraw.ImageDraw, postcard: Postcard) -> None:
    """Render the message text on the left half of the back panel."""
    panel_x0 = _BACK_PADDING
    panel_y0 = _BACK_PADDING
    panel_x1 = RENDER_WIDTH // 2 - _BACK_PADDING
    panel_y1 = RENDER_HEIGHT - _BACK_PADDING
    panel_width = panel_x1 - panel_x0

    font = _load_font(_MESSAGE_FONT_SIZE)
    text = _normalise_message(postcard.message.text)

    if not text.strip():
        # Faint "no message" hint.
        hint_font = _load_font(_ADDRESS_FONT_SIZE)
        draw.text(
            (panel_x0, panel_y0),
            "(no message)",
            fill=(170, 170, 180),
            font=hint_font,
        )
        return

    lines = _wrap_text(text, panel_width, char_width=_APPROX_CHAR_WIDTH)
    line_height = _MESSAGE_FONT_SIZE + _BACK_LINE_SPACING
    y = panel_y0
    for line in lines:
        if y + line_height > panel_y1:
            break
        draw.text((panel_x0, y), line, fill=_TEXT_COLOR, font=font)
        y += line_height


def _draw_address_block(draw: ImageDraw.ImageDraw, postcard: Postcard) -> None:
    """Render the recipient + sender addresses on the right half."""
    panel_x0 = RENDER_WIDTH // 2 + _BACK_PADDING
    panel_y0 = _BACK_PADDING
    panel_x1 = RENDER_WIDTH - _BACK_PADDING
    panel_width = panel_x1 - panel_x0

    recipient_font = _load_font(_ADDRESS_FONT_SIZE)
    sender_font = _load_font(_ADDRESS_FONT_SIZE)

    recipient_lines = _format_address_lines(postcard.recipient, kind="recipient")
    sender_lines = _format_address_lines(postcard.sender, kind="sender")

    # Recipient block — top of the right half.
    y = panel_y0
    y = _draw_text_lines(
        draw,
        recipient_lines,
        x=panel_x0,
        y=y,
        max_width=panel_width,
        font=recipient_font,
        fill=_TEXT_COLOR,
        char_width=_APPROX_ADDRESS_CHAR_WIDTH,
        line_spacing=_BACK_LINE_SPACING,
    )

    # Sender block — bottom of the right half, separated by a gap.
    gap = _BACK_PADDING * 2
    sender_block_height = (len(sender_lines)) * (_ADDRESS_FONT_SIZE + _BACK_LINE_SPACING)
    sender_y = RENDER_HEIGHT - _BACK_PADDING - sender_block_height
    if sender_y > y + gap:
        _draw_text_lines(
            draw,
            sender_lines,
            x=panel_x0,
            y=sender_y,
            max_width=panel_width,
            font=sender_font,
            fill=_SENDER_TEXT_COLOR,
            char_width=_APPROX_ADDRESS_CHAR_WIDTH,
            line_spacing=_BACK_LINE_SPACING,
        )
    else:
        # Sender would overlap the recipient — append a "From:" label
        # beneath the recipient block instead.
        _draw_text_lines(
            draw,
            ("", "From:", *sender_lines),
            x=panel_x0,
            y=y + gap // 2,
            max_width=panel_width,
            font=sender_font,
            fill=_SENDER_TEXT_COLOR,
            char_width=_APPROX_ADDRESS_CHAR_WIDTH,
            line_spacing=_BACK_LINE_SPACING,
        )


def _draw_text_lines(
    draw: ImageDraw.ImageDraw,
    lines: tuple[str, ...],
    *,
    x: int,
    y: int,
    max_width: int,
    font: FreeTypeFont | ImageFont.ImageFont,
    fill: tuple[int, int, int],
    char_width: int,
    line_spacing: int,
) -> int:
    """Render ``lines`` word-wrapped at ``max_width`` and return the cursor y after.

    Returns the y-coordinate of the next line (i.e. ``start_y + rendered_lines *
    line_height``) so callers can stack additional blocks underneath.
    """
    cursor_y = y
    # ``font.size`` exists on FreeTypeFont (returned by ``truetype``) and
    # on the default bitmap font returned by ``load_default`` at
    # runtime; the type stubs disagree on which is which, so we read it
    # defensively.
    font_size = int(getattr(font, "size", line_spacing))
    line_height = font_size + line_spacing
    for line in lines:
        wrapped = _wrap_text(line, max_width, char_width=char_width) if line else [""]
        for sub in wrapped:
            draw.text((x, cursor_y), sub, fill=fill, font=font)
            cursor_y += line_height
    return cursor_y


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


_TAG_RE = re.compile(r"<[^>]+>")


def _normalise_message(text: str) -> str:
    """Strip simple HTML tags so the renderer can treat the message as plain text.

    The Swiss Postcard Creator accepts a small HTML subset (``<b>``,
    ``<i>``, ``<br>``) but the renderer is intentionally
    conservative: it renders the message as plain text and replaces
    ``<br>`` with a newline. Tags are stripped, not interpreted, so
    ``<script>`` etc. cannot reach Pillow as markup.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub("", text)
    return text


def _wrap_text(text: str, max_width: int, *, char_width: int) -> list[str]:
    """Wrap ``text`` so each line is at most ``max_width`` pixels wide.

    Splits on whitespace and uses an approximate character width
    (rather than measuring each glyph) because the default Pillow
    font's glyph metrics are not available portably and the
    renderer prefers a fast, good-enough wrap over a slow, perfect
    one. Lines longer than ``max_width // char_width`` characters
    are hard-broken so the back panel never overflows.
    """
    max_chars = max(max_width // max(char_width, 1), 1)
    result: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph:
            result.append("")
            continue
        current = ""
        for word in paragraph.split(" "):
            if not word:
                continue
            if len(word) > max_chars:
                # Hard-break an overlong word so it never overflows.
                if current:
                    result.append(current)
                    current = ""
                while len(word) > max_chars:
                    result.append(word[:max_chars])
                    word = word[max_chars:]
                current = word
                continue
            candidate = word if not current else f"{current} {word}"
            if len(candidate) > max_chars:
                result.append(current)
                current = word
            else:
                current = candidate
        if current:
            result.append(current)
    return result


def _format_address_lines(address: AddressSpec, *, kind: str) -> tuple[str, ...]:
    """Return the address lines that should be printed for ``address``.

    ``kind`` controls the leading line — the recipient block
    prepends "To:" so the user can tell recipient and sender apart
    at a glance when both are stacked vertically.
    """
    lines: list[str] = []
    if kind == "recipient":
        lines.append("To:")
    elif kind == "sender":
        lines.append("From:")
    name = " ".join(part for part in (address.prename, address.lastname) if part).strip()
    if address.company:
        if name:
            lines.append(address.company)
            lines.append(name)
        else:
            lines.append(address.company)
    elif name:
        lines.append(name)
    if address.company_addition:
        lines.append(address.company_addition)
    street = address.street.strip()
    if street:
        lines.append(street)
    city_line = " ".join(part for part in (address.zip_code, address.place) if part).strip()
    if city_line:
        lines.append(city_line)
    if address.country:
        lines.append(address.country)
    if kind == "sender" and len(lines) == 1:
        # No sender address was provided. Show a faint hint rather
        # than a blank line.
        lines.append("(no sender address — sender = recipient)")
    return tuple(lines)


# ---------------------------------------------------------------------------
# Font loading
# ---------------------------------------------------------------------------


def _load_font(size: int) -> FreeTypeFont | ImageFont.ImageFont:
    """Return the best available TrueType font at ``size``, falling back to the bitmap default.

    Pillow's :func:`ImageFont.load_default` returns a tiny bitmap
    font which produces ugly output at our large render sizes.
    When a TrueType font from :data:`_FONT_CANDIDATES` is
    available we prefer it. The lookup is best-effort: a missing
    font is not an error.
    """
    for candidate in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


__all__ = [
    "RENDER_HEIGHT",
    "RENDER_WIDTH",
    "SUPPORTED_OUTPUT_FORMATS",
    "RenderError",
    "render_back",
    "render_front",
    "render_postcard",
]
