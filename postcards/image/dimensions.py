"""A6 postcard dimensions, aspect ratio, and supported image formats.

The Swiss Postcard Creator accepts JPEG images with the A6 aspect ratio
(105mm x 148mm, ratio 148 / 105 = sqrt(2) ~ 1.4095). The consumer endpoint
expects roughly 1500 x 1062 pixels for landscape cards and 1062 x 1500
for portrait cards; the upstream API documentation names those exact
numbers and rejects anything that deviates significantly.

This module is the single source of truth for those constants — the
pipeline (see :mod:`postcards.image.pipeline`) reads from here, and
the CLI surfaces them as ``postcards image --help`` defaults.

Aspect ratio
------------

``A6_ASPECT_RATIO`` is the *landscape* ratio: width / height = 148 / 105
(landscape is wider than tall). The pipeline uses it to decide which
orientation a square / moderately-wide source should be cropped to
when ``Orientation.AUTO`` is requested.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class Orientation(StrEnum):
    """Postcard orientation.

    ``AUTO`` lets the pipeline pick landscape vs. portrait based on the
    source image's aspect ratio relative to :data:`A6_ASPECT_RATIO`.
    ``LANDSCAPE`` / ``PORTRAIT`` force the pipeline to crop+resize the
    source into the corresponding A6 orientation regardless of input.
    """

    AUTO = "auto"
    LANDSCAPE = "landscape"
    PORTRAIT = "portrait"


#: Landscape target pixel dimensions accepted by the Swiss Postcard Creator.
A6_LANDSCAPE_WIDTH: Final[int] = 1500
A6_LANDSCAPE_HEIGHT: Final[int] = 1062

#: Portrait target pixel dimensions accepted by the Swiss Postcard Creator.
A6_PORTRAIT_WIDTH: Final[int] = 1062
A6_PORTRAIT_HEIGHT: Final[int] = 1500

#: Aspect ratio of A6 paper (landscape: width / height).
#:
#: Equals 148 / 105 = sqrt(2) ~ 1.4095. The pipeline uses this constant
#: to detect whether a source is wider or taller than an A6 postcard.
A6_ASPECT_RATIO: Final[float] = 148.0 / 105.0

#: Image formats accepted by :func:`postcards.image.pipeline.prepare_postcard_image`.
#:
#: These are the Pillow ``Image.format`` strings; ``"JPEG"`` and ``"PNG"``
#: cover essentially every consumer-grade still image. The pipeline
#: always *emits* JPEG because the Swiss Postcard Creator rejects
#: anything but JPEG for the picture.
SUPPORTED_FORMATS: Final[frozenset[str]] = frozenset({"JPEG", "PNG"})

#: Default JPEG encoder quality. 92 is a good trade-off between file
#: size and visual quality for a 1500x1062 photo (the upstream endpoint
#: accepts up to 10 MB; 92 lands well below that limit for typical
#: postcard source material).
DEFAULT_JPEG_QUALITY: Final[int] = 92


__all__ = [
    "A6_ASPECT_RATIO",
    "A6_LANDSCAPE_HEIGHT",
    "A6_LANDSCAPE_WIDTH",
    "A6_PORTRAIT_HEIGHT",
    "A6_PORTRAIT_WIDTH",
    "DEFAULT_JPEG_QUALITY",
    "SUPPORTED_FORMATS",
    "Orientation",
]
