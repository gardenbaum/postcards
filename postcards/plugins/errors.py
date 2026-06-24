"""Typed exceptions raised by the M3 plugin system.

The exception hierarchy mirrors the failure modes a plugin can
trigger during the three lifecycle stages documented in
:mod:`postcards.plugins.base`:

* :class:`PluginNotFoundError` — the requested plugin name is not
  registered.
* :class:`PluginConfigError`   — the plugin is registered but the
  supplied configuration payload is malformed (missing required
  field, wrong type, ...).
* :class:`PluginRenderError`   — the plugin's :meth:`Plugin.render`
  method raised (network failure, no images found, ...).

The base :class:`PluginError` exists so callers can ``except
PluginError`` to catch every plugin-specific failure without having
to enumerate the subclasses.
"""

from __future__ import annotations


class PluginError(RuntimeError):
    """Base class for every plugin-specific failure.

    Catch this in user-facing code (the CLI's ``send`` command)
    to convert plugin failures into a clean error message without
    leaking the plugin's internal traceback to the end user.
    """


class PluginNotFoundError(PluginError):
    """Raised when the registry cannot find a plugin by name.

    Parameters
    ----------
    name:
        The plugin name the caller asked for.
    """

    def __init__(self, name: str) -> None:
        super().__init__(f"plugin {name!r} is not registered")
        self.name = name


class PluginConfigError(PluginError):
    """Raised when the plugin's configuration payload is invalid.

    The plugin is registered but the ``payload`` block from the
    user's ``config.json`` is missing a required field, has the
    wrong type, or otherwise cannot be interpreted by the plugin's
    :meth:`Plugin.configure` method.
    """

    def __init__(self, plugin_name: str, message: str) -> None:
        super().__init__(f"plugin {plugin_name!r}: {message}")
        self.plugin_name = plugin_name
        self.message = message


class PluginRenderError(PluginError):
    """Raised when :meth:`Plugin.render` fails.

    The plugin is registered and configured correctly, but
    rendering the image/text pair failed (network error, no
    matching image found, ...). The original exception is
    available as :attr:`__cause__`.
    """

    def __init__(self, plugin_name: str, message: str) -> None:
        super().__init__(f"plugin {plugin_name!r}: {message}")
        self.plugin_name = plugin_name
        self.message = message


__all__ = [
    "PluginConfigError",
    "PluginError",
    "PluginNotFoundError",
    "PluginRenderError",
]
