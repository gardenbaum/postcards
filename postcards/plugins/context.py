"""Per-invocation context passed to M3 plugins.

:class:`PluginContext` is the bundle a plugin receives at render
time. It carries the data the plugin needs beyond the ``payload``
block: a logger (so plugins can log under their own name without
reaching for :mod:`logging`) and an opaque dict of CLI options
(``--keyword``, ``--category``, ...) that the user passed on the
command line.

Why a separate type
-------------------

The legacy plugin API passed an :class:`argparse.Namespace` to
each plugin, which coupled plugins to the legacy CLI's argparse
machinery. M3 plugins see a plain :class:`Mapping`, so they work
with Typer, Click, or any future CLI framework. The mapping is
populated by the CLI layer (:mod:`postcards.cli.commands.send`)
from whatever options the user supplied.

The dataclass is ``frozen`` so a plugin cannot accidentally mutate
the context and affect downstream code.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from logging import Logger, getLogger
from typing import Any


@dataclass(frozen=True)
class PluginContext:
    """Per-render context handed to a plugin's :meth:`Plugin.render`.

    Attributes
    ----------
    options:
        Mapping of CLI option names to values, e.g.
        ``{"keyword": "cats", "safe_search": True}``. Keys are the
        long-option name without the leading ``--``; values mirror
        the Typer option types (``str``, ``bool``, ``int``, ...).
        Plugins should treat unknown keys as a no-op (forward
        compatibility: a new CLI flag should not break old
        plugins).
    logger:
        Logger scoped to the plugin name. ``postcards send``
        builds this before constructing the plugin and passes it
        in so plugin log lines have a stable name regardless of
        the call site.
    """

    options: Mapping[str, Any] = field(default_factory=dict)
    logger: Logger = field(default_factory=lambda: getLogger("postcards.plugins"))


__all__ = ["PluginContext"]
