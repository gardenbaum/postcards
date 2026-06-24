"""A6 postcard image pipeline.

The pipeline takes a source image (path, bytes, or file-like object),
normalizes its orientation, crops it to the A6 aspect ratio, and emits
a JPEG byte stream ready to hand to :class:`PostcardBackend.send`.

Why a separate module
---------------------

The Swiss Postcard Creator endpoint accepts only JPEG images at
specific pixel dimensions (1500 x 1062 landscape or 1062 x 1500
portrait). Users regularly hand the CLI photos that do not match —
PNG screenshots, square Instagram crops, phone JPEGs with an EXIF
orientation tag that browsers ignore, raw RGBA captures with a
transparency channel JPEG cannot encode. The pipeline centralizes
the validation, normalization, and resize so the backend protocol
only has to deal with a finished :class:`Postcard` carrying a
processed ``picture: BinaryIO``.

Pipeline stages
---------------

1. :func:`load_image` — open the source from path / bytes / file-like
   using Pillow, never raising Pillow-specific exceptions to the caller.
2. :func:`normalize_orientation` — apply the EXIF orientation tag so
   the pixel data matches what the user sees in their viewer.
3. :func:`validate_format` — reject anything that is not JPEG or PNG
   (the supported input formats — see :data:`SUPPORTED_FORMATS`).
4. :func:`detect_orientation` — choose landscape vs. portrait when
   :attr:`Orientation.AUTO` is requested.
5. :func:`resize_to_a6` — center-crop to the A6 aspect ratio, then
   downscale/upscale to the target pixel dimensions.
6. :func:`encode_jpeg` — emit a JPEG byte stream at the configured
   quality (dropping alpha if necessary).

:func:`prepare_postcard_image` is the convenience wrapper that runs
the full sequence and is what :meth:`Postcard.from_image` calls.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import BinaryIO

from PIL import Image, ImageOps, UnidentifiedImageError

from postcards.image.dimensions import (
    A6_ASPECT_RATIO,
    A6_LANDSCAPE_HEIGHT,
    A6_LANDSCAPE_WIDTH,
    A6_PORTRAIT_HEIGHT,
    A6_PORTRAIT_WIDTH,
    DEFAULT_JPEG_QUALITY,
    SUPPORTED_FORMATS,
    Orientation,
)

logger = logging.getLogger(__name__)

#: Accepted input sources for :func:`load_image` / :func:`prepare_postcard_image`.
#:
#: - ``str`` / :class:`pathlib.Path` — a filesystem path to read.
#: - ``bytes`` — raw image bytes (e.g. the body of an HTTP response).
#: - :class:`typing.BinaryIO` — an already-open file-like object;
#:   the pipeline reads from it but does not close it (caller owns
#:   the lifecycle).
ImageSource = str | Path | bytes | BinaryIO


class ImageError(ValueError):
    """Raised when an image cannot be processed.

    Wraps Pillow-specific errors (:class:`PIL.UnidentifiedImageError`,
    :class:`OSError`, etc.) so callers can catch a single exception
    type. The original cause is preserved via ``__cause__``.
    """


# ---------------------------------------------------------------------------
# Stage 1: load
# ---------------------------------------------------------------------------


def load_image(source: ImageSource) -> Image.Image:
    """Open ``source`` and return a Pillow :class:`Image.Image`.

    ``source`` may be a path (``str`` or :class:`Path`), raw ``bytes``,
    or an already-open :class:`BinaryIO`. EXIF orientation is **not**
    applied at this stage — callers should pass the result through
    :func:`normalize_orientation` if they care about rotation.

    Raises
    ------
    ImageError
        When the source cannot be opened or Pillow cannot identify
        the image format.
    """
    try:
        if isinstance(source, (str, Path)):
            path = Path(source)
            if not path.is_file():
                raise ImageError(f"image source does not exist or is not a file: {source!r}")
            with Image.open(path) as image:
                # ``.copy()`` materializes pixel data so the file handle
                # can close before any downstream crop / resize. Pillow
                # strips the ``format`` attribute on ``.copy()`` so we
                # restore it from the opened image.
                copied = image.copy()
                copied.format = image.format
                return copied
        if isinstance(source, bytes):
            with Image.open(io.BytesIO(source)) as image:
                copied = image.copy()
                copied.format = image.format
                return copied
        # Otherwise: BinaryIO. We do NOT close it; the caller does.
        # We also do NOT ``.copy()`` here — for a stream we want the
        # returned image to be the live view so downstream cropping /
        # resizing reads from the same source bytes. The caller owns
        # the stream's lifecycle.
        opened = Image.open(source)
        # Defensive: if the caller already advanced the stream past
        # the header, ``.format`` may be None. We still return the
        # image; :func:`validate_format` will surface the problem.
        return opened
    except (UnidentifiedImageError, OSError) as exc:
        raise ImageError(f"cannot open image source: {exc}") from exc


# ---------------------------------------------------------------------------
# Stage 2: validate
# ---------------------------------------------------------------------------


def validate_format(image: Image.Image) -> None:
    """Raise :class:`ImageError` if ``image.format`` is not accepted.

    Pillow assigns ``image.format`` from the source file header. The
    Swiss Postcard Creator only accepts JPEG for the picture; PNG is
    supported as an input format here because Pillow can decode it,
    but the encoder stage always emits JPEG.
    """
    fmt = image.format
    if fmt is None or fmt.upper() not in SUPPORTED_FORMATS:
        raise ImageError(
            f"unsupported image format {fmt!r}; supported formats are {sorted(SUPPORTED_FORMATS)}"
        )


# ---------------------------------------------------------------------------
# Stage 3: orientation
# ---------------------------------------------------------------------------


def normalize_orientation(image: Image.Image) -> Image.Image:
    """Apply the EXIF orientation tag and return a (possibly new) image.

    Many phones write JPEG images whose pixel layout is rotated 90°/
    180°/270° but whose ``Orientation`` EXIF tag tells the viewer to
    rotate on display. :func:`PIL.ImageOps.exif_transpose` applies
    that rotation to the pixels and resets the tag to "1" (top-left).
    PNG images have no EXIF tag and pass through unchanged.

    The pipeline runs this before :func:`validate_format` so that
    callers passing a phone JPEG with an orientation tag get the
    correctly-oriented image even before the format check.
    """
    transposed = ImageOps.exif_transpose(image)
    # ``exif_transpose`` returns the *same* object when there is no
    # orientation to apply (no EXIF tag, or already top-left). When it
    # does transpose, it returns a new image — and that new image may
    # not carry the original's ``format`` attribute. Re-stamp it so
    # downstream :func:`validate_format` keeps working.
    if transposed.format is None:
        transposed.format = image.format
    return transposed


# ---------------------------------------------------------------------------
# Stage 4: orientation detection
# ---------------------------------------------------------------------------


def detect_orientation(image: Image.Image) -> Orientation:
    """Return ``LANDSCAPE`` or ``PORTRAIT`` based on the image's aspect ratio.

    The threshold is :data:`A6_ASPECT_RATIO`: a wider-than-A6 image is
    landscape, a taller-than-A6 image is portrait. Exactly-A6 images
    default to landscape (matches the most common Swiss Postcard
    Creator upload mode).
    """
    if image.width == 0 or image.height == 0:
        raise ImageError("cannot detect orientation of a zero-sized image")
    ratio = image.width / image.height
    return Orientation.LANDSCAPE if ratio >= A6_ASPECT_RATIO else Orientation.PORTRAIT


# ---------------------------------------------------------------------------
# Stage 5: resize
# ---------------------------------------------------------------------------


def _target_dimensions(orientation: Orientation) -> tuple[int, int]:
    """Return ``(width, height)`` for the given orientation."""
    if orientation is Orientation.LANDSCAPE:
        return A6_LANDSCAPE_WIDTH, A6_LANDSCAPE_HEIGHT
    if orientation is Orientation.PORTRAIT:
        return A6_PORTRAIT_WIDTH, A6_PORTRAIT_HEIGHT
    raise ImageError(f"cannot resolve target dimensions for orientation {orientation!r}")


def center_crop_to_aspect(image: Image.Image, aspect: float) -> Image.Image:
    """Crop ``image`` to ``aspect`` (width / height) at the center.

    If the source is already at ``aspect``, returns a copy unchanged
    (no pixel data is dropped). If it is wider, crops the left/right
    edges evenly. If it is taller, crops the top/bottom evenly.
    """
    if image.width == 0 or image.height == 0:
        raise ImageError("cannot crop a zero-sized image")
    source_aspect = image.width / image.height
    if source_aspect > aspect:
        # Source is wider than the target — chop the sides.
        new_width = round(image.height * aspect)
        left = (image.width - new_width) // 2
        return image.crop((left, 0, left + new_width, image.height))
    if source_aspect < aspect:
        # Source is taller than the target — chop the top/bottom.
        new_height = round(image.width / aspect)
        top = (image.height - new_height) // 2
        return image.crop((0, top, image.width, top + new_height))
    return image.copy()


def resize_to_a6(image: Image.Image, orientation: Orientation = Orientation.AUTO) -> Image.Image:
    """Crop and resize ``image`` to the A6 dimensions for ``orientation``.

    ``AUTO`` resolves to landscape vs. portrait based on
    :func:`detect_orientation`. The function always returns an
    ``RGB`` image at the target dimensions — alpha channels (PNG
    sources) are flattened against a white background so the JPEG
    encoder downstream does not have to guess.
    """
    if orientation is Orientation.AUTO:
        orientation = detect_orientation(image)
    target_w, target_h = _target_dimensions(orientation)
    cropped = center_crop_to_aspect(image, A6_ASPECT_RATIO)
    resized = cropped.resize((target_w, target_h), Image.Resampling.LANCZOS)
    if resized.mode != "RGB":
        # Flatten alpha onto white. ``convert("RGB")`` already does
        # this against a black background; we composite against white
        # explicitly so transparent UI screenshots do not turn black.
        if resized.mode in {"RGBA", "LA"} or (
            resized.mode == "P" and "transparency" in resized.info
        ):
            background = Image.new("RGB", resized.size, (255, 255, 255))
            resized_rgba = resized.convert("RGBA")
            background.paste(resized_rgba, mask=resized_rgba.split()[-1])
            resized = background
        else:
            resized = resized.convert("RGB")
    # Stamp the format so the encoder downstream picks JPEG.
    resized.format = "JPEG"
    return resized


# ---------------------------------------------------------------------------
# Stage 6: encode
# ---------------------------------------------------------------------------


def encode_jpeg(image: Image.Image, quality: int = DEFAULT_JPEG_QUALITY) -> bytes:
    """Encode ``image`` as JPEG bytes at the given ``quality`` (1-95+).

    Pillow clamps ``quality`` silently; we clamp explicitly to surface
    typos in the CLI surface.
    """
    if not 1 <= quality <= 95:
        raise ImageError(f"jpeg quality must be between 1 and 95 inclusive (got {quality!r})")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Convenience wrapper
# ---------------------------------------------------------------------------


def prepare_postcard_image(
    source: ImageSource,
    orientation: Orientation = Orientation.AUTO,
    quality: int = DEFAULT_JPEG_QUALITY,
) -> bytes:
    """Run the full image pipeline and return the processed JPEG bytes.

    Equivalent to::

        image = load_image(source)
        image = normalize_orientation(image)
        validate_format(image)
        image = resize_to_a6(image, orientation)
        return encode_jpeg(image, quality)

    The order matters: orientation is normalized before the format
    check so that a phone JPEG with an orientation tag passes
    :func:`validate_format` (its ``format`` attribute is unaffected
    by :func:`normalize_orientation`).
    """
    image = load_image(source)
    image = normalize_orientation(image)
    validate_format(image)
    image = resize_to_a6(image, orientation)
    return encode_jpeg(image, quality)


__all__ = [
    "ImageError",
    "ImageSource",
    "center_crop_to_aspect",
    "detect_orientation",
    "encode_jpeg",
    "load_image",
    "normalize_orientation",
    "prepare_postcard_image",
    "resize_to_a6",
    "validate_format",
]
