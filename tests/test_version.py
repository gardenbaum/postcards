"""Trivial smoke test for the M0 toolchain.

This is the test that the M0 gate must keep green. It is intentionally
minimal: it only exercises the package's ``__init__`` (which is just the
version string), so it does not require the legacy runtime dependencies
(postcard-creator, Js2Py, nltk, ...) to be installed.

In M1+ this file will grow into the real test suite: unit tests for each
plugin and an integration test that exercises a MOCKED Swiss Post backend
(see docs/CONSTITUTION.md invariant 1).
"""

from __future__ import annotations

import importlib.metadata

import postcards

EXPECTED_VERSION = "2.2"


def test_package_version_constant() -> None:
    """The package __version__ string is the same as the project metadata."""
    assert postcards.__version__ == EXPECTED_VERSION


def test_metadata_matches() -> None:
    """pyproject.toml and postcards/__init__.py agree on the version."""
    assert importlib.metadata.version("postcards") == postcards.__version__


def test_package_importable() -> None:
    """The postcards package import is side-effect free beyond __version__."""
    # Re-importing must not raise. importlib.reload is enough to prove the
    # module loads cleanly.
    import importlib

    importlib.reload(postcards)
    assert hasattr(postcards, "__version__")
