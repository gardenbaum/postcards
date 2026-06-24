"""A small helper base class for M3 plugins.

The :class:`Plugin` protocol is intentionally minimal, but every
concrete plugin still needs the same handful of plumbing: store
the payload, produce a sensible ``cli_help`` line, and provide a
logger scoped to the plugin name. :class:`PluginBase` centralises
that plumbing so each plugin only implements the plugin-specific
logic in :meth:`render`.

Plugins MAY inherit from :class:`PluginBase` (recommended for new
plugins) or implement the :class:`Plugin` protocol directly. The
runtime check in :func:`postcards.plugins.loader.load_plugin` uses
``isinstance(plugin, Plugin)`` which works for both shapes thanks
to ``@runtime_checkable``.
"""

from __future__ import annotations

from collections.abc import Mapping
from logging import Logger, getLogger
from typing import Any, ClassVar

from postcards.plugins.base import Plugin, PluginResult


class PluginBase:
    """Common base for M3 plugins.

    Stores the configured payload and exposes a logger scoped to
    the plugin's name. Subclasses are expected to declare
    :attr:`name` / :attr:`description` as :class:`ClassVar` and
    implement :meth:`render`.

    The class is intentionally not a dataclass: dataclass
    generation does not compose well with ``ClassVar`` fields
    (the dataclass machinery treats them as either instance
    attributes or ignored, both of which are wrong here), and a
    plain class lets subclasses override ``__init__`` freely
    without re-implementing dataclass ``__init__``.
    """

    #: Override in subclasses — the stable lower-snake-case name
    #: used in ``payload.plugin`` and entry-point lookups.
    name: ClassVar[str] = ""

    #: Override in subclasses — one-line human description shown
    #: in ``postcards plugins list``.
    description: ClassVar[str] = ""

    def __init__(self) -> None:
        self._payload: dict[str, Any] = {}

    @property
    def payload(self) -> Mapping[str, Any]:
        """Return the configured payload (read-only view)."""
        return self._payload

    @property
    def logger(self) -> Logger:
        """Return a logger scoped to the plugin's name.

        The logger name is ``postcards.plugins.<name>`` so log
        output can be filtered by plugin when several plugins are
        active in a test run.
        """
        return getLogger(f"postcards.plugins.{self.name}")

    def configure(self, payload: Mapping[str, Any]) -> None:
        """Store ``payload`` for later use by :meth:`render`.

        Subclasses that need to validate the payload should
        override this and call ``super().configure(payload)`` last
        (so validation errors do not leave the plugin half-
        configured).
        """
        # ``dict(payload)`` shallow-copies so plugins cannot
        # accidentally mutate the user's config.
        self._payload = dict(payload)

    def cli_help(self) -> str:
        """Default help text — :attr:`description`."""
        return self.description

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Validate at subclass-definition time so a plugin with a
        # missing ``name`` fails fast (at import) instead of at
        # the first render call.
        if not getattr(cls, "name", ""):
            raise TypeError(f"{cls.__name__} must define a non-empty 'name' class variable")

    def render(self) -> PluginResult:  # pragma: no cover - abstract
        raise NotImplementedError(f"{type(self).__name__} must implement Plugin.render()")


def is_plugin(obj: object) -> bool:
    """Return ``True`` when ``obj`` implements the :class:`Plugin` protocol.

    The runtime check uses :class:`Plugin`'s ``@runtime_checkable``
    decorator, so structural duck-typing is enough: any class that
    exposes ``name``, ``description``, ``configure`` and ``render``
    as the right shapes is considered a plugin.
    """
    return isinstance(obj, Plugin)


__all__ = ["PluginBase", "is_plugin"]
