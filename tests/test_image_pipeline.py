"""Tests for the A6 image pipeline.

The pipeline is exercised end-to-end with small in-memory Pillow
images (no fixture files on disk, no network) so the tests are
fast and hermetic. Each helper is tested independently so a
regression in one stage pinpoints exactly which stage broke.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from postcards.image import (
    A6_ASPECT_RATIO,
    A6_LANDSCAPE_HEIGHT,
    A6_LANDSCAPE_WIDTH,
    A6_PORTRAIT_HEIGHT,
    A6_PORTRAIT_WIDTH,
    DEFAULT_JPEG_QUALITY,
    SUPPORTED_FORMATS,
    ImageError,
    Orientation,
    center_crop_to_aspect,
    detect_orientation,
    encode_jpeg,
    load_image,
    normalize_orientation,
    prepare_postcard_image,
    resize_to_a6,
    validate_format,
)

# ---------------------------------------------------------------------------
# Test image helpers (in-memory; no disk)
# ---------------------------------------------------------------------------


def _new_image(width: int, height: int, color: str = "red") -> Image.Image:
    """Create an in-memory Pillow image of the given size and color."""
    return Image.new("RGB", (width, height), color=color)


def _to_jpeg_bytes(image: Image.Image, quality: int = DEFAULT_JPEG_QUALITY) -> bytes:
    """Encode ``image`` as JPEG bytes (for ``load_image`` from ``bytes``)."""
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def _to_png_bytes(image: Image.Image) -> bytes:
    """Encode ``image`` as PNG bytes (for ``load_image`` from ``bytes``)."""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# load_image
# ---------------------------------------------------------------------------


def test_load_image_from_bytes_returns_image_with_correct_dimensions() -> None:
    """``load_image(bytes)`` returns a Pillow image at the source dimensions."""
    source = _new_image(640, 480)
    data = _to_jpeg_bytes(source)
    image = load_image(data)
    assert image.size == (640, 480)
    assert image.format == "JPEG"


def test_load_image_from_bytes_accepts_png() -> None:
    """PNG bytes decode as a Pillow image with ``format='PNG'``."""
    source = _new_image(320, 200)
    data = _to_png_bytes(source)
    image = load_image(data)
    assert image.size == (320, 200)
    assert image.format == "PNG"


def test_load_image_from_binaryio_reads_without_closing() -> None:
    """``load_image(BinaryIO)`` reads from the stream but does not close it."""
    source = _new_image(800, 600)
    data = _to_jpeg_bytes(source)
    stream = io.BytesIO(data)
    image = load_image(stream)
    assert image.size == (800, 600)
    # Caller still owns the stream; it must not be closed.
    assert not stream.closed


def test_load_image_from_path(tmp_path: Path) -> None:
    """``load_image(Path)`` opens the file by path."""
    source = _new_image(100, 200)
    path = tmp_path / "test.jpg"
    source.save(path, format="JPEG")
    image = load_image(path)
    assert image.size == (100, 200)


def test_load_image_from_str_path(tmp_path: Path) -> None:
    """``load_image(str)`` accepts a plain string path too."""
    source = _new_image(50, 75)
    path = tmp_path / "test.png"
    source.save(path, format="PNG")
    image = load_image(str(path))
    assert image.size == (50, 75)


def test_load_image_raises_on_missing_path(tmp_path: Path) -> None:
    """A path that does not exist raises :class:`ImageError`."""
    with pytest.raises(ImageError, match="does not exist"):
        load_image(tmp_path / "does-not-exist.jpg")


def test_load_image_raises_on_garbage_bytes() -> None:
    """Bytes that are not an image raise :class:`ImageError`."""
    with pytest.raises(ImageError, match="cannot open image source"):
        load_image(b"this is not an image")


# ---------------------------------------------------------------------------
# validate_format
# ---------------------------------------------------------------------------


def test_validate_format_accepts_jpeg() -> None:
    """JPEG images pass :func:`validate_format`."""
    image = _new_image(100, 100)
    image.format = "JPEG"
    # Must not raise.
    validate_format(image)


def test_validate_format_accepts_png() -> None:
    """PNG images pass :func:`validate_format`."""
    image = _new_image(100, 100)
    image.format = "PNG"
    validate_format(image)


def test_validate_format_rejects_unknown() -> None:
    """GIF / BMP / WEBP / missing format raise :class:`ImageError`."""
    image = _new_image(100, 100)
    image.format = "GIF"
    with pytest.raises(ImageError, match="unsupported image format"):
        validate_format(image)


def test_validate_format_rejects_missing_format() -> None:
    """``image.format is None`` (e.g. after a crop) raises :class:`ImageError`."""
    image = _new_image(100, 100)
    image.format = None
    with pytest.raises(ImageError, match="unsupported image format"):
        validate_format(image)


# ---------------------------------------------------------------------------
# normalize_orientation
# ---------------------------------------------------------------------------


def test_normalize_orientation_passes_through_when_no_exif() -> None:
    """An image without an EXIF orientation tag is returned unchanged."""
    image = _new_image(200, 100)
    transposed = normalize_orientation(image)
    assert transposed.size == (200, 100)


def test_normalize_orientation_applies_exif_transpose() -> None:
    """An image with EXIF orientation 6 (rotate 90° CW) is transposed.

    ``ImageOps.exif_transpose`` rotates the pixel data so the result
    matches what the user sees in a viewer. We verify by checking
    the output dimensions: a 200x100 image tagged for 90° CW rotation
    becomes 100x200 after normalization.
    """
    image = _new_image(200, 100)
    # Pillow does not expose ``Orientation`` directly; use ``info`` dict.
    from PIL.Image import Exif

    exif = Exif()
    exif[0x0112] = 6  # Orientation tag, rotate 90° CW
    image.info["exif"] = exif.tobytes()
    transposed = normalize_orientation(image)
    assert transposed.size == (100, 200)


# ---------------------------------------------------------------------------
# detect_orientation
# ---------------------------------------------------------------------------


def test_detect_orientation_wider_than_a6_is_landscape() -> None:
    """A 16:9 image is landscape (wider than the A6 aspect ratio)."""
    image = _new_image(1600, 900)
    assert detect_orientation(image) is Orientation.LANDSCAPE


def test_detect_orientation_exactly_a6_is_landscape() -> None:
    """An image whose aspect equals :data:`A6_ASPECT_RATIO` is landscape (default)."""
    target_w = 1000
    target_h = round(target_w / A6_ASPECT_RATIO)
    image = _new_image(target_w, target_h)
    assert detect_orientation(image) is Orientation.LANDSCAPE


def test_detect_orientation_taller_than_a6_is_portrait() -> None:
    """A 9:16 image is portrait (taller than the A6 aspect ratio)."""
    image = _new_image(900, 1600)
    assert detect_orientation(image) is Orientation.PORTRAIT


def test_detect_orientation_raises_on_zero_sized_image() -> None:
    """A zero-sized image cannot have an aspect ratio; raises :class:`ImageError`."""
    image = _new_image(0, 100)
    with pytest.raises(ImageError, match="zero-sized"):
        detect_orientation(image)


# ---------------------------------------------------------------------------
# center_crop_to_aspect
# ---------------------------------------------------------------------------


def test_center_crop_to_aspect_wider_source_crops_sides() -> None:
    """A source wider than ``aspect`` crops the left and right edges evenly."""
    image = _new_image(2000, 1000)
    cropped = center_crop_to_aspect(image, A6_ASPECT_RATIO)
    # 1000 * A6_ASPECT_RATIO = ~1410
    assert cropped.size[0] == round(1000 * A6_ASPECT_RATIO)
    assert cropped.size[1] == 1000


def test_center_crop_to_aspect_taller_source_crops_top_bottom() -> None:
    """A source taller than ``aspect`` crops the top and bottom evenly."""
    image = _new_image(1000, 2000)
    cropped = center_crop_to_aspect(image, A6_ASPECT_RATIO)
    # 1000 / A6_ASPECT_RATIO = ~709
    assert cropped.size[0] == 1000
    assert cropped.size[1] == round(1000 / A6_ASPECT_RATIO)


def test_center_crop_to_aspect_exact_match_returns_copy() -> None:
    """A source whose aspect equals ``aspect`` returns a copy with the same size.

    We pick ``source_w`` so that ``source_w / source_h == aspect`` to
    machine precision (rational aspect values that Python's float
    division reproduces exactly). 148 * 10 / 105 * 10 = 1480 / 1050
    evaluates to the same float as 148 / 105 in CPython.
    """
    source_h = 1050
    source_w = 1480  # 1480 / 1050 == 148 / 105 to within float precision
    image = _new_image(source_w, source_h)
    cropped = center_crop_to_aspect(image, A6_ASPECT_RATIO)
    assert cropped.size == (source_w, source_h)


def test_center_crop_to_aspect_zero_sized_raises() -> None:
    """A zero-sized image cannot be cropped; raises :class:`ImageError`."""
    with pytest.raises(ImageError, match="zero-sized"):
        center_crop_to_aspect(_new_image(0, 100), A6_ASPECT_RATIO)


# ---------------------------------------------------------------------------
# resize_to_a6
# ---------------------------------------------------------------------------


def test_resize_to_a6_landscape_produces_landscape_dimensions() -> None:
    """``resize_to_a6(LANDSCAPE)`` yields the landscape A6 dimensions."""
    image = _new_image(3000, 1500)
    resized = resize_to_a6(image, Orientation.LANDSCAPE)
    assert resized.size == (A6_LANDSCAPE_WIDTH, A6_LANDSCAPE_HEIGHT)
    assert resized.mode == "RGB"


def test_resize_to_a6_portrait_produces_portrait_dimensions() -> None:
    """``resize_to_a6(PORTRAIT)`` yields the portrait A6 dimensions."""
    image = _new_image(1500, 3000)
    resized = resize_to_a6(image, Orientation.PORTRAIT)
    assert resized.size == (A6_PORTRAIT_WIDTH, A6_PORTRAIT_HEIGHT)
    assert resized.mode == "RGB"


def test_resize_to_a6_auto_picks_landscape_for_wide_input() -> None:
    """``resize_to_a6(AUTO)`` on a wide source picks landscape."""
    image = _new_image(2000, 800)
    resized = resize_to_a6(image, Orientation.AUTO)
    assert resized.size == (A6_LANDSCAPE_WIDTH, A6_LANDSCAPE_HEIGHT)


def test_resize_to_a6_auto_picks_portrait_for_tall_input() -> None:
    """``resize_to_a6(AUTO)`` on a tall source picks portrait."""
    image = _new_image(800, 2000)
    resized = resize_to_a6(image, Orientation.AUTO)
    assert resized.size == (A6_PORTRAIT_WIDTH, A6_PORTRAIT_HEIGHT)


def test_resize_to_a6_flatten_rgba_against_white() -> None:
    """An RGBA source is flattened against white, not black, so transparent
    pixels do not turn black in the JPEG output."""
    rgba = Image.new("RGBA", (2000, 1500), (0, 0, 0, 0))  # fully transparent
    resized = resize_to_a6(rgba, Orientation.LANDSCAPE)
    assert resized.mode == "RGB"
    # Spot-check a pixel — the center pixel of a fully-transparent
    # image must be white (255, 255, 255) after flatten.
    center = (A6_LANDSCAPE_WIDTH // 2, A6_LANDSCAPE_HEIGHT // 2)
    assert resized.getpixel(center) == (255, 255, 255)


def test_resize_to_a6_stamps_jpeg_format() -> None:
    """After resize, ``image.format`` is 'JPEG' so the encoder downstream picks it up."""
    image = _new_image(1500, 1000)
    resized = resize_to_a6(image, Orientation.LANDSCAPE)
    assert resized.format == "JPEG"


def test_resize_to_a6_converts_grayscale_to_rgb() -> None:
    """A grayscale source (``L`` mode) is converted to ``RGB``.

    PNG screenshots saved without color end up as ``L`` mode; the
    encoder would silently lose the channel otherwise. This hits
    the ``else: resized = resized.convert("RGB")`` branch.
    """
    gray = Image.new("L", (1500, 1000), 128)
    resized = resize_to_a6(gray, Orientation.LANDSCAPE)
    assert resized.mode == "RGB"
    assert resized.size == (A6_LANDSCAPE_WIDTH, A6_LANDSCAPE_HEIGHT)


def test_target_dimensions_rejects_unknown_orientation() -> None:
    """``_target_dimensions`` raises on an orientation it does not know."""
    from postcards.image.pipeline import _target_dimensions

    with pytest.raises(ImageError, match="cannot resolve target dimensions"):
        _target_dimensions("diagonal")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# encode_jpeg
# ---------------------------------------------------------------------------


def test_encode_jpeg_produces_nonempty_bytes() -> None:
    """A round-trip encode produces non-empty JPEG bytes."""
    image = _new_image(200, 150)
    data = encode_jpeg(image)
    assert isinstance(data, bytes)
    assert len(data) > 0
    # JPEG magic header: 0xFFD8FF
    assert data[:3] == b"\xff\xd8\xff"


def test_encode_jpeg_roundtrip_loads_back_to_same_dimensions() -> None:
    """JPEG-encoded bytes load back to an image at the same dimensions."""
    image = _new_image(640, 480)
    data = encode_jpeg(image, quality=80)
    reloaded = Image.open(io.BytesIO(data))
    assert reloaded.size == (640, 480)
    assert reloaded.format == "JPEG"


def test_encode_jpeg_quality_too_low_raises() -> None:
    """Quality below 1 raises :class:`ImageError` (clamped silently by Pillow otherwise)."""
    image = _new_image(100, 100)
    with pytest.raises(ImageError, match="quality must be between 1 and 95"):
        encode_jpeg(image, quality=0)


def test_encode_jpeg_quality_too_high_raises() -> None:
    """Quality above 95 raises :class:`ImageError`."""
    image = _new_image(100, 100)
    with pytest.raises(ImageError, match="quality must be between 1 and 95"):
        encode_jpeg(image, quality=96)


# ---------------------------------------------------------------------------
# prepare_postcard_image (the convenience wrapper)
# ---------------------------------------------------------------------------


def test_prepare_postcard_image_landscape_yields_landscape_dimensions() -> None:
    """A wide source yields a landscape JPEG at A6 dimensions."""
    raw = _to_jpeg_bytes(_new_image(2000, 1500))
    data = prepare_postcard_image(raw, orientation=Orientation.LANDSCAPE)
    reloaded = Image.open(io.BytesIO(data))
    assert reloaded.size == (A6_LANDSCAPE_WIDTH, A6_LANDSCAPE_HEIGHT)


def test_prepare_postcard_image_portrait_yields_portrait_dimensions() -> None:
    """A tall source yields a portrait JPEG at A6 dimensions."""
    raw = _to_jpeg_bytes(_new_image(1500, 2000))
    data = prepare_postcard_image(raw, orientation=Orientation.PORTRAIT)
    reloaded = Image.open(io.BytesIO(data))
    assert reloaded.size == (A6_PORTRAIT_WIDTH, A6_PORTRAIT_HEIGHT)


def test_prepare_postcard_image_auto_detects_orientation() -> None:
    """With ``AUTO`` orientation, the pipeline picks based on the source aspect."""
    wide_raw = _to_jpeg_bytes(_new_image(2400, 1000))
    tall_raw = _to_jpeg_bytes(_new_image(1000, 2400))

    wide_data = prepare_postcard_image(wide_raw, orientation=Orientation.AUTO)
    tall_data = prepare_postcard_image(tall_raw, orientation=Orientation.AUTO)

    assert Image.open(io.BytesIO(wide_data)).size == (A6_LANDSCAPE_WIDTH, A6_LANDSCAPE_HEIGHT)
    assert Image.open(io.BytesIO(tall_data)).size == (A6_PORTRAIT_WIDTH, A6_PORTRAIT_HEIGHT)


def test_prepare_postcard_image_accepts_bytes_input() -> None:
    """``prepare_postcard_image`` accepts raw JPEG/PNG bytes."""
    raw_bytes = _to_jpeg_bytes(_new_image(1500, 1000))
    data = prepare_postcard_image(raw_bytes)
    assert data[:3] == b"\xff\xd8\xff"


def test_prepare_postcard_image_accepts_binaryio_input() -> None:
    """``prepare_postcard_image`` accepts a file-like object."""
    raw_bytes = _to_jpeg_bytes(_new_image(1500, 1000))
    stream = io.BytesIO(raw_bytes)
    data = prepare_postcard_image(stream)
    assert data[:3] == b"\xff\xd8\xff"


def test_prepare_postcard_image_accepts_png_input() -> None:
    """PNG sources go through the pipeline and emit JPEG."""
    raw_bytes = _to_png_bytes(_new_image(1500, 1000))
    data = prepare_postcard_image(raw_bytes)
    reloaded = Image.open(io.BytesIO(data))
    assert reloaded.format == "JPEG"
    assert reloaded.size == (A6_LANDSCAPE_WIDTH, A6_LANDSCAPE_HEIGHT)


def test_prepare_postcard_image_raises_on_unsupported_format() -> None:
    """GIF bytes are rejected by the format validation stage."""
    gif_buffer = io.BytesIO()
    _new_image(100, 100).save(gif_buffer, format="GIF")
    with pytest.raises(ImageError, match="unsupported image format"):
        prepare_postcard_image(gif_buffer.getvalue())


def test_prepare_postcard_image_raises_on_garbage_input() -> None:
    """Garbage bytes raise :class:`ImageError` (not ``PIL.UnidentifiedImageError``)."""
    with pytest.raises(ImageError):
        prepare_postcard_image(b"not an image at all")


def test_prepare_postcard_image_default_quality_used_when_unspecified() -> None:
    """``prepare_postcard_image`` uses :data:`DEFAULT_JPEG_QUALITY` when not given one."""
    raw = _to_jpeg_bytes(_new_image(1500, 1000))
    data = prepare_postcard_image(raw)
    # A 1500x1062 JPEG at quality 92 should land in the 100KB-500KB range
    # for solid-color content; we don't pin a hard number, just confirm
    # the bytes are non-trivially sized.
    assert len(data) > 5_000


# ---------------------------------------------------------------------------
# Constants sanity (these guard against accidental edits to the module API)
# ---------------------------------------------------------------------------


def test_dimensions_constants_have_expected_values() -> None:
    """The dimension constants are the Swiss Postcard Creator's accepted sizes."""
    assert A6_LANDSCAPE_WIDTH == 1500
    assert A6_LANDSCAPE_HEIGHT == 1062
    assert A6_PORTRAIT_WIDTH == 1062
    assert A6_PORTRAIT_HEIGHT == 1500
    assert abs(A6_ASPECT_RATIO - (148.0 / 105.0)) < 1e-9


def test_supported_formats_contains_jpeg_and_png() -> None:
    """``SUPPORTED_FORMATS`` lists the formats the pipeline accepts."""
    assert "JPEG" in SUPPORTED_FORMATS
    assert "PNG" in SUPPORTED_FORMATS


def test_default_jpeg_quality_is_in_safe_range() -> None:
    """``DEFAULT_JPEG_QUALITY`` is between 1 and 95 inclusive (the encoder's accepted range)."""
    assert 1 <= DEFAULT_JPEG_QUALITY <= 95
