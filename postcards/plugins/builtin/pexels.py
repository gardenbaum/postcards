"""``pexels`` — fetch a random picture from picsum.photos.

This is the M3 port of the legacy
``postcards.plugin_pexels.postcards_pexels.PostcardsPexel`` plugin.
The legacy plugin used the third-party ``pypexels`` package to
query pexels.com; the M0 modernization dropped ``pypexels``
(because the upstream package is in maintenance mode) and
replaced it with a tiny ``picsum.photos`` wrapper that returns a
random image given an optional keyword seed.

The M3 port uses the same wrapper and exposes it as a plugin
named ``pexels`` to preserve backward compatibility with
existing ``config.json`` files (the plugin name is just a key
in the registry, not a network endpoint).

Configuration payload
---------------------

``payload.keyword`` (optional)
    Free-form string used as the picsum seed. Same keyword →
    same image within a process. When omitted, a stable default
    seed is used and the same image is returned for every
    invocation.

``payload.safe_search`` (optional, default ``False``)
    Accepted for compatibility with the legacy plugin; ignored
    by the picsum-backed implementation (picsum.photos does not
    expose a safe-search toggle).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, BinaryIO, ClassVar
from urllib.error import URLError

from postcards.plugins.base import PluginResult
from postcards.plugins.base_impl import PluginBase
from postcards.plugins.errors import PluginConfigError, PluginRenderError
from postcards.plugins.registry import register


class PexelsPlugin(PluginBase):
    """Fetch a random picture from picsum.photos."""

    name: ClassVar[str] = "pexels"
    description: ClassVar[str] = "fetch a random picture from picsum.photos"

    def configure(self, payload: Mapping[str, Any]) -> None:
        # The payload is optional for pexels: both ``keyword`` and
        # ``safe_search`` are optional. We only validate types when
        # they are present so legacy configs with extra fields do
        # not break.
        keyword = payload.get("keyword")
        if keyword is not None and not isinstance(keyword, str):
            raise PluginConfigError(self.name, "'keyword' must be a string when present")
        safe_search = payload.get("safe_search", False)
        if not isinstance(safe_search, bool):
            raise PluginConfigError(self.name, "'safe_search' must be a boolean")
        super().configure(payload)

    def render(self) -> PluginResult:
        # Reuse the legacy helper so the URL shape and headers
        # stay in lock-step with what ``postcards.plugin_pexels``
        # sends. That keeps fixture-based tests in the legacy
        # package compatible with the new plugin.
        from postcards.plugin_pexels.util.pexels import (
            get_random_image_url,
            read_from_url,
        )

        keyword = self._payload.get("keyword")
        url = get_random_image_url(keyword=keyword if isinstance(keyword, str) else None)
        self.logger.info("using pexels picture: %s", url)

        try:
            handle: BinaryIO = read_from_url(url)
        except URLError as exc:
            raise PluginRenderError(self.name, f"network error fetching {url}: {exc}") from exc
        except OSError as exc:
            raise PluginRenderError(self.name, f"cannot read {url}: {exc}") from exc

        return PluginResult(image=handle)


register(PexelsPlugin.name, PexelsPlugin, description=PexelsPlugin.description)


__all__ = ["PexelsPlugin"]
