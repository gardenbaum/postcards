"""``unsplash`` — fetch a random photo from the Unsplash API.

The plugin uses the official ``/photos/random`` endpoint with
a ``Client-ID`` access token (the free, public-tier auth).
The access token is read from the environment variable
``POSTCARDS_UNSPLASH_ACCESS_KEY`` — there is intentionally no
config-file fallback, because the constitution forbids
secrets in tracked files (see ``docs/CONSTITUTION.md`` §2).

Two HTTP calls are issued per :meth:`render`:

1. ``GET https://api.unsplash.com/photos/random?query=...``
   — returns a JSON envelope with the photo's metadata
   (``urls``, ``user``, ...). We pick the ``urls.regular``
   field, which is the high-resolution variant Unsplash
   recommends for in-product display.

2. ``GET <urls.regular>`` — streams the JPEG bytes the
   postcard backend hands to PIL.

Both calls have a per-request timeout (default 30 s) and
share the same ``requests.Session`` so the ``User-Agent`` /
``Accept-Version`` headers are set in one place.

Configuration payload
---------------------

``payload.query`` (optional)
    Free-form search term. When ``None`` or empty, the
    plugin asks Unsplash for a random photo of any topic.
``payload.orientation`` (optional, default ``"landscape"``)
    One of ``"landscape"``, ``"portrait"``, ``"squarish"``.
    The postcards image pipeline rotates the picture to
    A6 landscape anyway, but picking landscape reduces
    the amount of cropping and so the loss of detail.
``payload.count`` (optional, default 1)
    How many photos to ask Unsplash for, in the range
    ``[1, 30]``. The plugin picks one at random from the
    returned list so the user can broaden the topic space
    without picking by hand.
``payload.message`` (optional)
    Postcard message text. When ``None``, the CLI's
    ``-m``/``--message`` option wins.

Environment
-----------

``POSTCARDS_UNSPLASH_ACCESS_KEY``
    Unsplash API access token. The plugin raises
    :class:`PluginConfigError` when this variable is unset
    so the user gets a clear message instead of a
    not-very-helpful 401 from Unsplash.
"""

from __future__ import annotations

import os
import random
from collections.abc import Mapping
from io import BytesIO
from typing import Any, BinaryIO, ClassVar

import requests

from postcards.plugins.base import PluginResult
from postcards.plugins.base_impl import PluginBase
from postcards.plugins.errors import PluginConfigError, PluginRenderError
from postcards.plugins.registry import register

#: Environment variable the access token is read from.
ENV_VAR: str = "POSTCARDS_UNSPLASH_ACCESS_KEY"

#: Default per-request timeout, in seconds.
_DEFAULT_TIMEOUT: float = 30.0

#: Default orientation — landscape matches the A6 postcard aspect.
_DEFAULT_ORIENTATION: str = "landscape"

#: Allowed values for ``payload.orientation`` (Unsplash rejects others).
_ALLOWED_ORIENTATIONS: frozenset[str] = frozenset({"landscape", "portrait", "squarish"})

#: Unsplash API base URL.
_API_BASE: str = "https://api.unsplash.com"

#: Max value of ``payload.count`` (Unsplash's documented hard cap).
_MAX_COUNT: int = 30


class UnsplashPlugin(PluginBase):
    """Fetch a random photo from the Unsplash API."""

    name: ClassVar[str] = "unsplash"
    description: ClassVar[str] = "fetch a random photo from the Unsplash API"

    def configure(self, payload: Mapping[str, Any]) -> None:
        query = payload.get("query")
        if query is not None and not isinstance(query, str):
            raise PluginConfigError(self.name, "'query' must be a string when present")

        orientation = payload.get("orientation", _DEFAULT_ORIENTATION)
        if orientation not in _ALLOWED_ORIENTATIONS:
            raise PluginConfigError(
                self.name,
                f"'orientation' must be one of {sorted(_ALLOWED_ORIENTATIONS)}",
            )

        count = payload.get("count", 1)
        # ``bool`` is a subclass of ``int`` — reject explicitly so
        # ``count: True`` does not silently degrade to ``count: 1``.
        if isinstance(count, bool) or not isinstance(count, int):
            raise PluginConfigError(self.name, "'count' must be an int when present")
        if not 1 <= count <= _MAX_COUNT:
            raise PluginConfigError(self.name, f"'count' must be between 1 and {_MAX_COUNT}")

        message = payload.get("message")
        if message is not None and not isinstance(message, str):
            raise PluginConfigError(self.name, "'message' must be a string when present")

        super().configure(payload)

    def render(self) -> PluginResult:
        access_key = os.environ.get(ENV_VAR, "").strip()
        if not access_key:
            raise PluginRenderError(
                self.name,
                f"{ENV_VAR} is not set; Unsplash needs an access token. "
                f"Grab one at https://unsplash.com/oauth/applications and export it.",
            )

        query = self._payload.get("query")
        orientation = str(self._payload.get("orientation", _DEFAULT_ORIENTATION))
        count = int(self._payload.get("count", 1))

        photo_url = self._fetch_random_photo(
            access_key=access_key,
            query=str(query) if isinstance(query, str) else None,
            orientation=orientation,
            count=count,
        )
        self.logger.info("downloading unsplash photo: %s", photo_url)

        picture = self._download(photo_url)
        message_raw = self._payload.get("message")
        message: str | None = str(message_raw) if isinstance(message_raw, str) else None

        return PluginResult(image=picture, message=message)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _fetch_random_photo(
        self,
        *,
        access_key: str,
        query: str | None,
        orientation: str,
        count: int,
    ) -> str:
        """Call ``/photos/random`` and return one ``urls.regular`` value.

        Raises
        ------
        PluginRenderError
            When the API responds with a non-2xx status, the
            response body is not the expected JSON shape, or
            ``urls.regular`` is missing.
        """
        params: dict[str, str] = {"orientation": orientation, "count": str(count)}
        if query:
            params["query"] = query

        url = f"{_API_BASE}/photos/random"
        headers = {
            "Authorization": f"Client-ID {access_key}",
            "Accept-Version": "v1",
        }

        try:
            response = requests.get(url, params=params, headers=headers, timeout=_DEFAULT_TIMEOUT)
        except requests.RequestException as exc:
            raise PluginRenderError(self.name, f"network error calling Unsplash: {exc}") from exc

        if response.status_code == 401:
            raise PluginRenderError(
                self.name,
                f"Unsplash rejected the access token (HTTP 401); check that {ENV_VAR} is valid",
            )
        if response.status_code >= 400:
            raise PluginRenderError(
                self.name,
                f"Unsplash returned HTTP {response.status_code}: {response.text[:200]!r}",
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise PluginRenderError(
                self.name, f"Unsplash response is not valid JSON: {exc}"
            ) from exc

        photos = payload if isinstance(payload, list) else [payload]
        if not photos:
            raise PluginRenderError(
                self.name, "Unsplash returned no photos for the configured query"
            )

        chosen = random.choice(photos)
        if not isinstance(chosen, dict):
            raise PluginRenderError(self.name, "Unsplash photo entry is not an object")

        urls = chosen.get("urls")
        if not isinstance(urls, dict):
            raise PluginRenderError(self.name, "Unsplash photo is missing the 'urls' block")

        regular = urls.get("regular")
        if not isinstance(regular, str) or not regular:
            raise PluginRenderError(self.name, "Unsplash photo is missing 'urls.regular'")
        return regular

    def _download(self, url: str) -> BinaryIO:
        """Stream a JPEG from ``url`` into an in-memory :class:`BytesIO`."""
        try:
            response = requests.get(url, timeout=_DEFAULT_TIMEOUT)
        except requests.RequestException as exc:
            raise PluginRenderError(self.name, f"network error downloading picture: {exc}") from exc

        if response.status_code >= 400:
            raise PluginRenderError(
                self.name,
                f"HTTP {response.status_code} downloading {url}",
            )

        if not response.content:
            raise PluginRenderError(self.name, f"empty response body from {url}")

        return BytesIO(response.content)


register(UnsplashPlugin.name, UnsplashPlugin, description=UnsplashPlugin.description)


__all__ = ["UnsplashPlugin"]
