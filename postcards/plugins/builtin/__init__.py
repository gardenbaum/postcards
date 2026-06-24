"""In-tree M3 plugins for ``postcards``.

Importing this package registers the bundled plugins (folder,
folder_yaml, pexels, chuck_norris) into :data:`Registry.default`.
The registration is idempotent: importing the module twice has
the same effect as importing it once.

External packages can ship additional plugins by adding a
``postcards.plugins`` entry point to their ``pyproject.toml``; see
:mod:`postcards.plugins.registry` for the discovery protocol.
"""

from __future__ import annotations

from postcards.plugins import registry as _registry

# Importing each module registers its plugin into ``Registry.default``
# via the module-level ``register(...)`` call at the bottom of the
# module. We import them here so the registration happens at
# package import time.
from postcards.plugins.builtin import (
    chuck_norris,
    folder,
    folder_yaml,
    local,
    pexels,
    unsplash,
    url,
)

__all__ = ["chuck_norris", "folder", "folder_yaml", "local", "pexels", "unsplash", "url"]


# Re-export the register/discover helpers so callers can
# ``from postcards.plugins.builtin import register, discover``.
register = _registry.register
discover = _registry.discover
