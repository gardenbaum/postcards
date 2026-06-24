"""Plugin loader — turn a config payload into a ready-to-render plugin.

The loader is the bridge between the user's ``config.json`` and
the :class:`Plugin` API. It reads ``payload.plugin`` (the plugin
name), looks the plugin class up in the :class:`Registry`, builds
an instance, and runs :meth:`Plugin.configure` with the rest of
the payload.

The function does *not* call :meth:`Plugin.render` — that is the
caller's responsibility, so the caller can interpose logging or
retry logic. The result of :func:`load_plugin` is an instance the
caller can call ``.render()`` on directly.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from postcards.plugins.base import Plugin
from postcards.plugins.errors import PluginConfigError, PluginNotFoundError
from postcards.plugins.registry import Registry


def load_plugin(
    name: str,
    payload: Mapping[str, Any] | None = None,
    *,
    registry: Registry | None = None,
) -> Plugin:
    """Build, configure, and return a ready-to-render plugin instance.

    Parameters
    ----------
    name:
        The plugin's registered name (``payload.plugin`` from the
        user's ``config.json``).
    payload:
        The plugin's configuration block. Passed to
        :meth:`Plugin.configure` verbatim. ``None`` is treated as
        an empty mapping.
    registry:
        Registry to look the plugin up in. Defaults to the
        package-wide :data:`Registry.default`. Tests inject a
        custom registry to keep state isolated.

    Returns
    -------
    Plugin
        An instance of the registered plugin class, with
        ``configure`` already called.

    Raises
    ------
    PluginNotFoundError
        When ``name`` is not in the registry.
    PluginConfigError
        When ``configure`` raises (the plugin's own validation
        failed).
    """
    reg = registry if registry is not None else Registry.default
    if not name:
        raise PluginNotFoundError(name)

    try:
        plugin_cls = reg.get(name)
    except Exception as exc:
        # ``Registry.get`` raises ``PluginError`` for unknown names;
        # re-raise as the more specific ``PluginNotFoundError`` so
        # callers can match on a single exception type.
        raise PluginNotFoundError(name) from exc

    plugin = plugin_cls()
    cfg = payload if payload is not None else {}
    try:
        plugin.configure(cfg)
    except PluginConfigError:
        raise
    except Exception as exc:
        # Plugins should raise ``PluginConfigError`` directly; this
        # branch catches the "I forgot to wrap my error" case so
        # the user still sees a clean message.
        raise PluginConfigError(name, str(exc)) from exc
    return plugin


__all__ = ["load_plugin"]
