"""Top-level entry point for the ``postcards`` console script.

``pyproject.toml`` declares::

    postcards = "postcards.cli.main:main"

This module exists so the entry-point string resolves to a
``main`` function. The actual work lives in
:mod:`postcards.cli.runner`; this module is a one-line
forwarder that re-exports :func:`postcards.cli.runner.main`.

Why a separate module
---------------------

Splitting the entry-point target out of :mod:`runner` lets the
tests import the runner module without pulling the entry-point
name into the import graph. The runner is the testable
seam; the entry-point is a wiring concern.
"""

from __future__ import annotations

from postcards.cli.runner import main

__all__ = ["main"]
