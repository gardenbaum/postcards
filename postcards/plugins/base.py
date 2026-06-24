"""The M3 plugin protocol and its return type.

A plugin is a small object that turns a per-send ``config.json``
``payload`` block into an image (and optionally a message) for the
postcard. The API is deliberately tiny — three methods on the
:class:`Plugin` protocol and one dataclass for the return value — so
writing a new plugin does not require understanding the rest of the
codebase.

Plugin lifecycle
----------------

1. **Construction** — instantiating the plugin must not do any I/O.
   ``MyPlugin()`` is cheap. The plugin stores its configuration in
   ``__init__`` only when the configuration is required for the
   instance to exist at all (which is rare); normally configuration
   is held in :meth:`Plugin.configure`.
2. **Configuration** — :meth:`Plugin.configure` receives the
   ``payload`` dict from ``config.json`` and any CLI-supplied
   options. Implementations validate the payload here and raise
   :class:`postcards.plugins.PluginConfigError` on malformed input.
3. **Render** — :meth:`Plugin.render` produces the
   :class:`PluginResult`. Implementations raise
   :class:`postcards.plugins.PluginRenderError` when the network is
   unreachable, the picture folder is empty, etc.

Plugins are stateless across renders: a single instance may be used
to render one postcard at a time. If a plugin needs to retain
state between renders (e.g. a "don't repeat" exclusion list), it
should manage that state internally.

Thread-safety
-------------

The plugin API makes no thread-safety guarantees. ``postcards send``
invokes plugins from a single thread, so plugins do not need locks.
Plugins that spawn helper threads must serialise their own state.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, BinaryIO, ClassVar, Protocol, runtime_checkable


@dataclass(frozen=True)
class PluginResult:
    """The value a plugin's :meth:`Plugin.render` returns.

    ``image`` is required: the postcard backend always consumes a
    picture stream. ``message`` is optional; when ``None``, the
    caller is expected to supply its own message (via the CLI's
    ``-m``/``--message`` option). ``metadata`` is an open-ended
    bag for plugin-specific debugging data the caller may surface
    in logs (the postcard backend ignores it).

    The dataclass is ``frozen`` so a result cannot be mutated after
    it is built; that mirrors the convention established by
    :class:`postcards.models.Postcard`.
    """

    image: BinaryIO
    message: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class Plugin(Protocol):
    """Protocol every M3 plugin implements.

    Three methods, two class variables, no inheritance required.
    The protocol is ``runtime_checkable`` so tests can use
    :func:`isinstance` against it (the build_plugin check is
    duck-typed at runtime, not statically enforced).
    """

    #: Stable, lower-snake-case name of the plugin. Used in the
    #: ``payload.plugin`` field of ``config.json`` and as the
    #: entry-point name in the ``postcards.plugins`` group.
    name: ClassVar[str]

    #: One-line human-readable description; surfaced in
    #: ``postcards plugins list``.
    description: ClassVar[str]

    def configure(self, payload: Mapping[str, Any]) -> None:
        """Validate and store the configuration for one send.

        Parameters
        ----------
        payload:
            The ``payload`` block of the user's ``config.json``,
            augmented with CLI overrides where applicable. Plugins
            MUST NOT mutate the dict they receive.

        Raises
        ------
        postcards.plugins.PluginConfigError
            When ``payload`` is missing a required field or
            contains a value of the wrong type.
        """
        ...

    def render(self) -> PluginResult:
        """Produce the image (+ optional message) for the postcard.

        Implementations should be cheap to call multiple times for
        the same configuration: ``render()`` should not have
        side-effects beyond what is strictly necessary to fetch a
        fresh image. Plugins that move files or update an
        exclusion list should perform the side-effect *after*
        producing the :class:`PluginResult` so a failure mid-call
        does not leave state in an inconsistent place.

        Raises
        ------
        postcards.plugins.PluginRenderError
            When the plugin cannot produce a result (network
            failure, no matching images, ...).
        """
        ...


__all__ = ["Plugin", "PluginResult"]
