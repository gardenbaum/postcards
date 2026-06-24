"""Free image fetcher used by the pexels plugin.

History
-------

The previous implementation used ``pypexels`` (an unofficial
Pexels.com API client) with a hard-coded API key. ``pypexels`` is no
longer maintained and its single release is a pre-release
(``1.0.0b4``); we have dropped it.

This module fetches random images from ``picsum.photos``, a free
Lorem-Picsum service that returns a random photo at
``https://picsum.photos/seed/<seed>/<width>/<height>``. No API key is
required. The ``seed`` is a stable string per keyword so the same
keyword always maps to the same image within a process — matching
the previous behaviour where a Pexels search term returned the same
photo each time.

The public API exposed here matches the previous pexels utility:

* ``get_random_image_url(keyword=None) -> str``
* ``read_from_url(url) -> BinaryIO``

The previous ``keyword`` argument was accepted but ignored (Pexels'
free search no longer matched reliably; the upstream printed a
deprecation warning). The current implementation also ignores
``keyword`` for the URL seed by default — keyword support is
deliberately not advertised — but if a non-empty ``keyword`` is
given it is hashed and used as the picsum seed so different keywords
land on different photos.
"""

from __future__ import annotations

import hashlib
import urllib.request
from typing import BinaryIO

# Standard postcard dimensions; the previous API was hard-coded to
# 154x111 px per the upstream ``postcard_creator.postcard_creator``.
_PICSUM_WIDTH = 800
_PICSUM_HEIGHT = 600

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.11 "
        "(KHTML, like Gecko) Chrome/23.0.1271.64 Safari/537.11"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Charset": "ISO-8859-1,utf-8;q=0.7,*;q=0.3",
    "Accept-Encoding": "none",
    "Accept-Language": "en-US,en;q=0.8",
    "Connection": "keep-alive",
}


def _seed_for(keyword: str | None) -> str:
    """Map ``keyword`` to a stable picsum seed.

    A non-empty keyword is hashed (sha1, truncated) so the seed is
    short and ASCII-safe. ``None`` or an empty string picks a stable
    default so ``get_random_image_url()`` returns the same image
    each call within a process.
    """
    if not keyword:
        return "postcards"
    return hashlib.sha1(keyword.encode("utf-8")).hexdigest()[:16]


def get_random_image_url(keyword: str | None = None) -> str:
    """Return a URL for a random postcard-sized image.

    ``keyword`` is optional; if given it is used as the picsum seed so
    different keywords land on different photos. If ``None`` (or
    empty) a stable default seed is used and the same image is
    returned every call.
    """
    seed = _seed_for(keyword)
    return f"https://picsum.photos/seed/{seed}/{_PICSUM_WIDTH}/{_PICSUM_HEIGHT}"


def read_from_url(url: str) -> BinaryIO:
    """Open ``url`` for reading and return a binary file-like object.

    Returns the raw ``http.client.HTTPResponse`` from
    ``urllib.request.urlopen``; callers should ``.read()`` from it or
    pass it to PIL.
    """
    request = urllib.request.Request(url, None, _HEADERS)
    return urllib.request.urlopen(request)


def get_random_image(keyword: str | None = None) -> BinaryIO:
    """Convenience wrapper: open the random image directly.

    Matches the previous ``pexels.get_random_image`` signature so
    legacy callers keep working.
    """
    return read_from_url(get_random_image_url(keyword))


if __name__ == "__main__":
    print(get_random_image_url())
