"""A6 image pipeline — load, validate, orient, crop, resize, encode.

See :mod:`postcards.image.pipeline` for the full sequence and
:mod:`postcards.image.dimensions` for the constants. The public
surface re-exports the most common entry points so callers can do::

    from postcards.image import prepare_postcard_image, Orientation
"""

from __future__ import annotations

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
from postcards.image.pipeline import (
    ImageError,
    ImageSource,
    center_crop_to_aspect,
    detect_orientation,
    encode_jpeg,
    load_image,
    normalize_orientation,
    prepare_postcard_image,
    resize_to_a6,
    validate_format,
)

__all__ = [
    "A6_ASPECT_RATIO",
    "A6_LANDSCAPE_HEIGHT",
    "A6_LANDSCAPE_WIDTH",
    "A6_PORTRAIT_HEIGHT",
    "A6_PORTRAIT_WIDTH",
    "DEFAULT_JPEG_QUALITY",
    "SUPPORTED_FORMATS",
    "ImageError",
    "ImageSource",
    "Orientation",
    "center_crop_to_aspect",
    "detect_orientation",
    "encode_jpeg",
    "load_image",
    "normalize_orientation",
    "prepare_postcard_image",
    "resize_to_a6",
    "validate_format",
]
