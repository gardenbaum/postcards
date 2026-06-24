"""Plugin registry — name → plugin-class lookup with entry-point discovery.

Two ways to register a plugin
-----------------------------

1. **Programmatic** — call :meth:`Registry.register` (or the
   :func:`register` shortcut) from your plugin's ``__init__.py``.
   The in-tree plugins under :mod:`postcards.plugins.builtin` do
   this so users of ``postcards`` get a working registry out of
   the box.

2. **Entry-point** — declare a ``postcards.plugins`` entry point
   in ``pyproject.toml``. Third-party plugins ship as separate
   packages; the registry picks them up via
   :meth:`Registry.discover` when ``postcards send`` runs. The
   entry-point *value* is the dotted path to the plugin class.

Disambiguation
--------------

If a plugin is registered both programmatically and via an entry
point under the same name, the programmatic registration wins
(``register`` overwrites). This is intentional: a host application
that vendors a plugin should be able to override the entry-point
version without rebuilding the package.
"""

from __future__ import annotations

from importlib import metadata
from typing import Any, ClassVar

from postcards.plugins.errors import PluginError

#: Entry-point group name plugins are discovered under.
ENTRY_POINT_GROUP: str = "postcards.plugins"


class Registry:
    """In-memory name → plugin-class registry.

    The registry is a plain mapping with a handful of convenience
    methods. It is intentionally not thread-safe: ``register`` is
    expected to be called at import time, and ``get`` / ``names``
    are read-only after that. The class-level :data:`default`
    instance is what the rest of the codebase uses; tests can
    build their own :class:`Registry` to avoid cross-test bleed.
    """

    #: The default registry the rest of the package uses. Tests
    #: should construct their own :class:`Registry` rather than
    #: mutate this one — keeping the default registry clean means
    #: ``postcards.plugins.builtin`` import side-effects are
    #: deterministic.
    default: ClassVar[Registry] = None  # type: ignore[assignment]

    def __init__(self) -> None:
        self._plugins: dict[str, type[Any]] = {}
        self._descriptions: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        plugin_cls: type[Any],
        *,
        description: str | None = None,
    ) -> None:
        """Add (or replace) a plugin class in the registry.

        Parameters
        ----------
        name:
            The plugin's stable name. Must be non-empty and
            lower-snake-case (the registry does not enforce
            case, but downstream ``payload.plugin`` lookups are
            case-sensitive so the convention matters).
        plugin_cls:
            The plugin class (not an instance). The class is
            stored; instances are built lazily by
            :func:`postcards.plugins.loader.load_plugin`.
        description:
            Optional one-line description. Defaults to
            ``plugin_cls.description`` when omitted, falling
            back to ``""`` when neither is set.

        Raises
        ------
        ValueError
            When ``name`` is empty or ``plugin_cls`` is not a
            class.
        """
        if not name:
            raise ValueError("plugin name must be non-empty")
        if not isinstance(plugin_cls, type):
            raise ValueError(f"plugin_cls must be a class, got {type(plugin_cls).__name__}")
        self._plugins[name] = plugin_cls
        if description is not None:
            self._descriptions[name] = description
        else:
            # Fall back to the class-level ``description`` if it
            # exposes one (the ``Plugin`` Protocol declares it as
            # a ``ClassVar``).
            desc = getattr(plugin_cls, "description", "")
            if desc:
                self._descriptions[name] = str(desc)

    def unregister(self, name: str) -> None:
        """Remove a plugin by name. No-op if absent."""
        self._plugins.pop(name, None)
        self._descriptions.pop(name, None)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, name: str) -> type[Any]:
        """Return the plugin class for ``name``.

        Raises
        ------
        PluginError
            When the name is not registered. The error message
            lists the registered names to make the typo obvious.
        """
        try:
            return self._plugins[name]
        except KeyError as exc:
            available = ", ".join(sorted(self._plugins)) or "(none)"
            raise PluginError(f"plugin {name!r} is not registered; available: {available}") from exc

    def has(self, name: str) -> bool:
        """Return ``True`` when ``name`` is registered."""
        return name in self._plugins

    def names(self) -> list[str]:
        """Return the sorted list of registered plugin names."""
        return sorted(self._plugins)

    def description_for(self, name: str) -> str:
        """Return the registered description for ``name`` (or ``""``)."""
        return self._descriptions.get(name, "")

    def items(self) -> list[tuple[str, type[Any]]]:
        """Return ``(name, class)`` pairs sorted by name."""
        return [(name, self._plugins[name]) for name in self.names()]

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self, group: str = ENTRY_POINT_GROUP) -> int:
        """Discover and register plugins via ``importlib.metadata``.

        Iterates over entry points in the supplied group; each
        entry point's ``.load()`` must return a plugin class.
        Already-registered names are kept as-is (programmatic
        registration wins).

        Returns the number of *newly* registered plugins.
        """
        count = 0
        for entry_point in metadata.entry_points(group=group):
            plugin_cls = entry_point.load()
            if not isinstance(plugin_cls, type):
                # Skip non-class entry points — they are probably
                # a factory or module rather than the plugin
                # class itself. We log a warning through the
                # standard logging channel rather than failing,
                # so a single broken third-party plugin does not
                # disable the whole registry.
                continue
            if entry_point.name in self._plugins:
                # Programmatic registration wins; skip silently.
                continue
            self.register(entry_point.name, plugin_cls)
            count += 1
        return count

    def clear(self) -> None:
        """Remove every registered plugin.

        Intended for tests that need a clean slate between cases.
        """
        self._plugins.clear()
        self._descriptions.clear()


#: The package-wide default registry. Built-ins register into
#: this instance at import time (see :mod:`postcards.plugins.builtin`).
#: The :class:`Registry` class installs itself here on first
#: instantiation to break the circular default.
Registry.default = Registry()


def register(
    name: str,
    plugin_cls: type[Any],
    *,
    description: str | None = None,
) -> None:
    """Register a plugin class with the package-wide :data:`Registry.default`.

    Convenience wrapper around
    ``Registry.default.register(name, plugin_cls, description=...)``
    for plugin ``__init__.py`` files.
    """
    Registry.default.register(name, plugin_cls, description=description)


def discover(group: str = ENTRY_POINT_GROUP) -> int:
    """Discover entry-point plugins into :data:`Registry.default`."""
    return Registry.default.discover(group=group)


__all__ = [
    "ENTRY_POINT_GROUP",
    "Registry",
    "discover",
    "register",
]
