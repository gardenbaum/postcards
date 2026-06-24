"""Modern plugin system for ``postcards``.

M3 replaces the inheritance-based plugin model (each plugin
subclassed :class:`postcards.postcards.Postcards` and overrode
``get_img_and_text`` / ``build_plugin_subparser``) with a small,
typed, registry-based API.

Public surface
--------------

* :class:`Plugin`        — the protocol every plugin implements
* :class:`PluginResult`  — the value plugins return from :meth:`Plugin.render`
* :class:`PluginContext` — per-invocation context (config + CLI args + logger)
* :class:`Registry`      — name → plugin-class lookup, also handles
                           ``importlib.metadata`` entry-point discovery
* :func:`load_plugin`    — build, configure, and return a ready-to-render
                           plugin instance from a config payload
* :class:`PluginError`   and subclasses — typed exceptions

The in-tree plugins live under :mod:`postcards.plugins.builtin` and
register themselves with the default :data:`REGISTRY` at import time.
External packages can register new plugins via the
``postcards.plugins`` entry-point group (see ``pyproject.toml``).

Backward compatibility
----------------------

The legacy ``postcards.plugin_*`` packages and their console-script
entry points (``postcards-folder``, ``postcards-yaml``, ...) remain
importable. The M3 plugin system is config-driven: when
``config.json`` carries a ``payload.plugin`` field, the new code path
is taken; otherwise the legacy ``_is_plugin()`` branch in
``postcards.postcards`` is preserved.

The Bing-image-scraper plugin (``postcards.plugin_random``) is
removed entirely; see the M3 changelog entry for the justification.
"""

from __future__ import annotations

from postcards.plugins.base import Plugin, PluginResult
from postcards.plugins.context import PluginContext
from postcards.plugins.errors import (
    PluginConfigError,
    PluginError,
    PluginNotFoundError,
    PluginRenderError,
)
from postcards.plugins.loader import load_plugin
from postcards.plugins.registry import Registry

#: Backwards-compatible alias for the package-wide default
#: registry. The canonical name is ``Registry.default``; the
#: ``REGISTRY`` alias is kept for shorter call sites.
REGISTRY: Registry = Registry.default

__all__ = [
    "REGISTRY",
    "Plugin",
    "PluginConfigError",
    "PluginContext",
    "PluginError",
    "PluginNotFoundError",
    "PluginRenderError",
    "PluginResult",
    "Registry",
    "load_plugin",
]
