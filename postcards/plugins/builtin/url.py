"""``url`` — fetch a postcard picture from a user-supplied URL.

The simplest of the M3 image sources: the user hands us a URL,
we GET it, return the bytes wrapped in a :class:`BytesIO`. The
plugin accepts an optional ``message`` to be used as the
postcard body text and an optional ``headers`` mapping for
authentication (e.g. ``Authorization: Bearer ...``).

Configuration payload
---------------------

``payload.url`` (required)
    Absolute ``http://`` or ``https://`` URL pointing at a
    picture (``image/jpeg``, ``image/png``, ...). Other
    content types are accepted and forwarded; the postcards
    image pipeline will reject them at send time.
``payload.message`` (optional)
    Postcard message text. When ``None``, the CLI's
    ``-m``/``--message`` option wins.
``payload.headers`` (optional, default ``{}``)
    Mapping of HTTP headers to attach to the GET request.
    Useful for ``Authorization`` and ``User-Agent`` overrides.
``payload.timeout`` (optional, default 30)
    Per-request timeout in seconds. Float; ``0`` is rejected.

Notes
-----

* The plugin uses the ``requests`` library (already a runtime
  dep) rather than ``urllib`` because the API surface is
  friendlier for header injection and JSON-style mocking.
* The URL is fetched on every :meth:`render` call. ``render``
  is allowed to be expensive (it does I/O) but it is also
  expected to be idempotent within a single send: callers
  should not call :meth:`render` twice for the same send.
* Network errors are wrapped in
  :class:`postcards.plugins.PluginRenderError` so the CLI can
  surface them as a clean failure message instead of a raw
  :class:`requests.RequestException` traceback.
"""

from __future__ import annotations

from collections.abc import Mapping
from io import BytesIO
from typing import Any, BinaryIO, ClassVar

import requests

from postcards.plugins.base import PluginResult
from postcards.plugins.base_impl import PluginBase
from postcards.plugins.errors import PluginConfigError, PluginRenderError
from postcards.plugins.registry import register

#: Default per-request timeout, in seconds. Long enough for
#: slow CDNs, short enough that ``postcards send`` does not
#: appear to hang when an endpoint is unreachable.
_DEFAULT_TIMEOUT: float = 30.0

#: Schemes accepted as picture sources. ``file://`` is
#: explicitly NOT in the list — use the ``folder`` or
#: ``local`` plugin for filesystem sources.
_ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})


class UrlPlugin(PluginBase):
    """Fetch a postcard picture from a user-supplied URL."""

    name: ClassVar[str] = "url"
    description: ClassVar[str] = "fetch a postcard picture from a user-supplied URL"

    def configure(self, payload: Mapping[str, Any]) -> None:
        url = payload.get("url")
        if not url or not isinstance(url, str):
            raise PluginConfigError(self.name, "'url' (str) is required in the payload")
        scheme = url.split(":", 1)[0].lower()
        if scheme not in _ALLOWED_SCHEMES:
            raise PluginConfigError(
                self.name,
                f"'url' scheme must be one of {sorted(_ALLOWED_SCHEMES)}; got {scheme!r}",
            )

        message = payload.get("message")
        if message is not None and not isinstance(message, str):
            raise PluginConfigError(self.name, "'message' must be a string when present")

        headers = payload.get("headers", {})
        if not isinstance(headers, Mapping):
            raise PluginConfigError(self.name, "'headers' must be a mapping when present")
        for header_name, header_value in headers.items():
            if not isinstance(header_name, str) or not isinstance(header_value, str):
                raise PluginConfigError(
                    self.name,
                    "'headers' keys and values must both be strings",
                )

        timeout = payload.get("timeout", _DEFAULT_TIMEOUT)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
            raise PluginConfigError(self.name, "'timeout' must be a number when present")
        if float(timeout) <= 0:
            raise PluginConfigError(self.name, "'timeout' must be > 0")

        super().configure(payload)

    def render(self) -> PluginResult:
        url = str(self._payload["url"])
        headers_raw = self._payload.get("headers", {})
        # ``Mapping[str, str]`` enforced in ``configure``; cast
        # so mypy is happy on the ``requests.get`` call.
        headers: dict[str, str] = {str(k): str(v) for k, v in headers_raw.items()}
        timeout = float(self._payload.get("timeout", _DEFAULT_TIMEOUT))

        self.logger.info("fetching picture from %s", url)

        try:
            response = requests.get(url, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            raise PluginRenderError(self.name, f"network error fetching {url}: {exc}") from exc

        if response.status_code >= 400:
            raise PluginRenderError(
                self.name,
                f"HTTP {response.status_code} fetching {url}",
            )

        body: bytes = response.content
        if not body:
            raise PluginRenderError(self.name, f"empty response body from {url}")

        handle: BinaryIO = BytesIO(body)

        message_raw = self._payload.get("message")
        message: str | None = str(message_raw) if isinstance(message_raw, str) else None

        self.logger.debug("fetched %d bytes from %s", len(body), url)
        return PluginResult(image=handle, message=message)


register(UrlPlugin.name, UrlPlugin, description=UrlPlugin.description)


__all__ = ["UrlPlugin"]
