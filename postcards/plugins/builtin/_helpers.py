"""Common helpers for in-tree M3 plugins.

The M3 plugins share a handful of trivial utilities (resolve a
relative path against the cwd, etc.). Centralising them here
keeps each plugin's ``render`` method short and lets the tests
share fixtures.
"""

from __future__ import annotations

import os


def make_absolute(path: str) -> str:
    """Return ``path`` resolved against the cwd when not absolute.

    Mirrors the legacy :meth:`PostcardsFolder._make_absolute_path`
    helper so plugin configs written for the legacy CLI keep
    working.
    """
    if not os.path.isabs(path):
        return os.path.join(os.getcwd(), path)
    return path


__all__ = ["make_absolute"]
