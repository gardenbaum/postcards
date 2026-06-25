"""Packaging tests for the M6 distribution surface.

These tests guard the parts of ``pyproject.toml`` and the runtime
package metadata that downstream tools (PyPI, ``pipx``, the
Dockerfile build stage, the README install snippet) rely on. They
do NOT install the package; they read ``pyproject.toml`` as data
and query ``importlib.metadata`` against the currently-installed
package.

Why this file exists
--------------------

Before M6, the wheel metadata was hand-edited and drifted from the
runtime ``__version__`` at least once. M6 introduces a single source
of truth (``postcards/__init__.py``) plus hatchling's
``[tool.hatch.version]`` wiring; this test suite exists to keep
that promise from regressing.

Each test is a single ``assert`` so a regression names the exact
field that broke.
"""

from __future__ import annotations

import importlib.metadata
import re
import tomllib
from pathlib import Path
from typing import Any

import postcards


def _read_pyproject() -> dict[str, Any]:
    """Read and parse the repo's ``pyproject.toml``."""
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    with pyproject.open("rb") as fh:
        return tomllib.load(fh)


# ---------------------------------------------------------------------------
# pyproject.toml surface
# ---------------------------------------------------------------------------


def test_pyproject_dynamic_version() -> None:
    """The ``[project]`` table marks ``version`` as dynamic.

    The single source of truth is ``postcards/__init__.py``; hatchling
    reads it via ``[tool.hatch.version]`` below. Marking it dynamic
    in ``[project]`` is the hatchling contract for that wiring.
    """
    project = _read_pyproject()["project"]
    assert isinstance(project, dict)
    dynamic = project.get("dynamic")
    assert isinstance(dynamic, list)
    assert "version" in dynamic


def test_pyproject_requires_python() -> None:
    """The package pins ``requires-python = ">=3.12"``.

    The CI matrix (§3 of the constitution) tests 3.12 + 3.13; the
    wheel metadata must refuse installs on older interpreters so
    the user gets a clear error before any code runs.
    """
    project = _read_pyproject()["project"]
    assert isinstance(project, dict)
    assert project.get("requires-python") == ">=3.12"


def test_pyproject_classifiers_include_required_axes() -> None:
    """The classifiers cover the four axes PyPI filters on.

    License, Python version, audience, and OS. Adding classifiers
    is the easiest way to make the package discoverable; this test
    is a checklist so a future edit cannot silently drop one.
    """
    project = _read_pyproject()["project"]
    assert isinstance(project, dict)
    classifiers = project.get("classifiers")
    assert isinstance(classifiers, list)
    required = {
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Operating System :: OS Independent",
    }
    missing = required - set(classifiers)
    assert not missing, f"pyproject.toml classifiers missing: {sorted(missing)}"


def test_pyproject_urls() -> None:
    """The project URLs point at the active fork (``gardenbaum``).

    The original upstream (``abertschi/postcards``) is preserved as
    ``Upstream`` so the original author is still discoverable from
    the PyPI sidebar.
    """
    project = _read_pyproject()["project"]
    assert isinstance(project, dict)
    urls = project.get("urls")
    assert isinstance(urls, dict)
    assert urls.get("Homepage") == "https://github.com/gardenbaum/postcards"
    assert urls.get("Repository") == "https://github.com/gardenbaum/postcards"
    assert urls.get("Issues", "").startswith("https://github.com/gardenbaum/postcards")
    assert urls.get("Upstream") == "https://github.com/abertschi/postcards"


def test_pyproject_console_scripts() -> None:
    """``[project.scripts]`` declares the ``postcards`` console entry point.

    Without this entry point, ``pipx install .`` would produce a
    package with no CLI to run. The legacy plugin scripts
    (``postcards-folder`` etc.) are checked separately — the goal
    here is to assert the canonical ``postcards`` script.
    """
    project = _read_pyproject()["project"]
    assert isinstance(project, dict)
    scripts = project.get("scripts")
    assert isinstance(scripts, dict)
    assert scripts.get("postcards") == "postcards.cli.main:main"


def test_pyproject_hatch_version_path() -> None:
    """``[tool.hatch.version]`` points at ``postcards/__init__.py``.

    This is the wiring that lets ``[project]`` declare ``version``
    as dynamic. If the path is wrong, ``python -m build`` raises a
    clear error — but only at build time. The test catches the
    case where someone edits the path and forgets to run a build.
    """
    config = _read_pyproject()
    hatch_obj = config.get("tool", {})
    if not isinstance(hatch_obj, dict):
        hatch_obj = {}
    hatch = hatch_obj.get("hatch", {})
    assert isinstance(hatch, dict)
    version = hatch.get("version")
    assert isinstance(version, dict)
    assert version.get("path") == "postcards/__init__.py"


def test_pyproject_entry_point_group_enumerates_in_tree_plugins() -> None:
    """Every in-tree plugin is advertised via the entry-point group.

    The in-tree plugins register themselves at import time
    (``postcards.plugins.builtin``), but third-party tools that
    enumerate plugins via ``importlib.metadata`` see only the
    entry-point group. Keeping them in sync avoids a class of
    "plugin works in dev, missing for downstream" bugs.
    """
    project = _read_pyproject()["project"]
    assert isinstance(project, dict)
    entry_points = project.get("entry-points", {})
    assert isinstance(entry_points, dict)
    plugins = entry_points.get("postcards.plugins", {})
    assert isinstance(plugins, dict)

    expected = {
        "folder",
        "folder_yaml",
        "local",
        "pexels",
        "unsplash",
        "url",
        "chuck_norris",
    }
    missing = expected - set(plugins.keys())
    assert not missing, f"entry-point group missing plugins: {sorted(missing)}"


# ---------------------------------------------------------------------------
# Runtime metadata vs source-of-truth
# ---------------------------------------------------------------------------


def test_runtime_version_matches_pyproject_dynamic_resolution() -> None:
    """``importlib.metadata.version`` returns the same string as ``__version__``.

    This is the runtime check for the dynamic-version wiring. If
    hatchling stops reading from ``postcards/__init__.py`` (or the
    path is wrong), the wheel metadata will disagree with the
    runtime constant and this test fails.
    """
    assert importlib.metadata.version("postcards") == postcards.__version__


def test_runtime_version_is_a_valid_semver() -> None:
    """``__version__`` is a PEP 440 / SemVer string.

    PyPI refuses non-PEP-440 versions, so the gate must catch a
    typo (``3.0`` instead of ``3.0.0``, ``v3.0.0`` with a prefix)
    before the build runs.
    """
    pattern = re.compile(r"^\d+\.\d+\.\d+(?:[a-zA-Z0-9.\-+]*)?$")
    assert pattern.match(postcards.__version__), (
        f"__version__ {postcards.__version__!r} is not a PEP 440 version"
    )


def test_long_description_is_markdown() -> None:
    """``README.md`` is declared as the long description, markdown flavour.

    PyPI renders the long description on the project page; the
    ``text/markdown`` content type enables CommonMark rendering
    (vs the default reStructuredText).
    """
    project = _read_pyproject()["project"]
    assert isinstance(project, dict)
    assert project.get("readme") == "README.md"
    assert project.get("readme-content-type") == "text/markdown"


def test_no_pyproject_drift_field_removed() -> None:
    """``[project]`` does not carry a static ``version`` field.

    The whole point of the M6 refactor is that the version is
    dynamic; if someone re-adds a static ``version = "..."`` line
    the dynamic wiring becomes ambiguous and ``python -m build``
    either errors or silently wins the wrong one.
    """
    project = _read_pyproject()["project"]
    assert isinstance(project, dict)
    assert "version" not in project


def test_app_extra_declares_nicegui() -> None:
    """``postcards[app]`` extra is wired in ``pyproject.toml``.

    The WYSIWYG web app is opt-in: the ``app`` extra pulls in
    ``nicegui`` so users who only use the CLI do not have to
    install the app's (heavier) web deps. This test pins the
    contract so a future refactor that splits the extras cannot
    silently drop ``nicegui``.
    """
    project = _read_pyproject()["project"]
    optional = project.get("optional-dependencies", {})
    assert isinstance(optional, dict)
    assert "app" in optional, "missing [app] extra in optional-dependencies"
    app_deps = optional["app"]
    assert any(dep.startswith("nicegui") for dep in app_deps), (
        f"[app] extra must include nicegui; got {app_deps!r}"
    )


def test_web_service_imports_without_nicegui() -> None:
    """The service layer imports without the optional ``app`` extra.

    :mod:`postcards.web.service` is network- and UI-framework-free, so
    it must import on a core install (the UI lives in
    :mod:`postcards.web.app`, which is the only consumer of NiceGUI).
    """
    from postcards.web import service

    assert hasattr(service, "render_preview")
    assert hasattr(service, "send_draft")


def test_app_subcommand_is_registered_in_app() -> None:
    """``postcards app`` is registered as a Typer subcommand.

    Mirrors the runtime contract documented in the README: typing
    ``postcards app`` launches the web app (or prints a clear
    install-prompt if the ``app`` extra is missing).
    """
    from postcards.cli.app import app as typer_app

    names = {c.name for c in typer_app.registered_commands}
    assert "app" in names
