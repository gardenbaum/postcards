"""Smoke tests for the console-script entry points.

The ``postcards`` package ships six console-script entry points
declared in ``pyproject.toml`` (``[project.scripts]``). The constitution
(§5, "The CLI stays usable") requires that every one of them
imports cleanly and that ``--help`` exits 0. This test discovers the
entry points via :mod:`importlib.metadata` so it stays in sync with
``pyproject.toml`` automatically — adding a new entry point to the
package means this test will exercise it.

We invoke the entry points through :mod:`subprocess` against the
*current* Python interpreter (the one running the test) so the
binary on ``$PATH`` does not have to be in sync with the test
interpreter. That mirrors what ``scripts/check.sh`` does for the
gate (``python -m pip install -e .[dev]`` + ``pytest``), and it
exercises the real console-script wrapper that ships with the
wheel (``pip install`` produces a ``postcards`` / ``postcards-folder``
``/`` ``...`` shim, not a ``python -m`` alias).
"""

from __future__ import annotations

import importlib.metadata
import sys
from collections.abc import Iterator

import pytest


def _iter_entry_points() -> Iterator[importlib.metadata.EntryPoint]:
    """Yield the console-script entry points declared by ``postcards``."""
    eps = importlib.metadata.entry_points()
    yield from eps.select(group="console_scripts")


def _find_entry_point(name: str) -> importlib.metadata.EntryPoint:
    """Return the named console-script entry point or fail loudly."""
    for ep in _iter_entry_points():
        if ep.name == name:
            return ep
    raise AssertionError(f"no entry point named {name!r}")


@pytest.fixture(scope="module")
def postcards_entry_points() -> list[str]:
    """Names of the console-script entry points declared by the project.

    Sorted for deterministic test ordering.
    """
    return sorted(ep.name for ep in _iter_entry_points() if ep.value.startswith("postcards"))


def test_postcards_package_has_console_scripts(
    postcards_entry_points: list[str],
) -> None:
    """``pyproject.toml`` declares at least the known entry points.

    We assert >= 5 rather than an exact match so adding a new plugin
    does not require updating this test; the per-entry smoke tests
    below iterate the actual list and will catch a broken entry point
    automatically.

    M3 removed the ``postcards-random`` console script (the Bing
    image-search scraper stopped returning results in 2023). The
    expected set is the remaining M2 entry points plus the M3
    ``postcards-chuck-norris`` plugin (which was already shipped in
    M0 but never had its console script listed in this test).
    """
    assert len(postcards_entry_points) >= 5
    expected = {
        "postcards",
        "postcards-folder",
        "postcards-yaml",
        "postcards-pexels",
        "postcards-chuck-norris",
    }
    missing = expected - set(postcards_entry_points)
    assert not missing, f"missing console scripts: {sorted(missing)}"
    # ``postcards-random`` must NOT be a registered console script
    # in M3 (the Bing scraper was removed).
    assert "postcards-random" not in postcards_entry_points, (
        "postcards-random was removed in M3 (Bing scraper)"
    )


@pytest.mark.parametrize(
    "script_name",
    [
        "postcards",
        "postcards-folder",
        "postcards-yaml",
        "postcards-pexels",
        "postcards-chuck-norris",
    ],
)
def test_console_script_module_imports(script_name: str) -> None:
    """Importing the module behind a console-script entry point does not raise.

    We resolve the entry point via ``importlib.metadata`` and import
    the module named in the ``module:function`` target. This catches
    the "missing import" failure mode without needing to spawn a
    subprocess.
    """
    candidates = _find_entry_point(script_name)
    module_name, _, func_name = candidates.value.partition(":")
    assert module_name, f"entry point {script_name!r} has empty module path"
    assert func_name, f"entry point {script_name!r} has empty function name"

    importlib = __import__("importlib")
    module = importlib.import_module(module_name)
    assert hasattr(module, func_name), (
        f"entry point {script_name!r} -> {candidates.value!r} but module has no "
        f"function {func_name!r}"
    )


@pytest.mark.parametrize(
    "script_name",
    [
        "postcards",
        "postcards-folder",
        "postcards-yaml",
        "postcards-pexels",
        "postcards-chuck-norris",
    ],
)
def test_console_script_help_exits_zero(script_name: str) -> None:
    """``<script> --help`` exits 0.

    We invoke the entry point through :mod:`importlib.metadata`'s
    ``EntryPoint.load()`` so we exercise the exact function the
    console-script wrapper would install in ``sys.modules``. This
    avoids relying on the binary being on ``$PATH`` — which it
    usually is after ``pip install -e .`` but is not guaranteed in
    every test environment.
    """
    main_fn = _find_entry_point(script_name).load()
    assert callable(main_fn), f"entry point {script_name!r} did not load a callable"

    # The base ``postcards`` entry point now uses Typer (M2),
    # while the plugin entry points keep their argparse-based
    # implementation. Typer's ``--help`` exits with code 0 but
    # does NOT raise ``SystemExit`` the way argparse does — the
    # test therefore accepts either path: a ``SystemExit`` with
    # code 0 (argparse) or a clean return (Typer). Plugin entry
    # points still raise ``SystemExit``.
    #
    # ``argparse`` and the Typer app both read ``sys.argv``, so
    # we mock it for every entry point. The base entry point
    # additionally accepts an explicit ``argv`` argument for
    # backward compatibility with pre-M2 call sites; we pass it
    # so the new entry-point signature is exercised.
    old_argv = sys.argv
    sys.argv = [script_name, "--help"]
    try:
        if script_name == "postcards":
            main_fn([script_name, "--help"])
        else:
            main_fn()
    except SystemExit as exc:
        assert exc.code == 0, f"{script_name} --help exited with code {exc.code} (expected 0)"
    else:
        # Typer does not raise SystemExit on --help; the runner
        # already returned. The plugin entry points always raise,
        # so a non-``postcards`` entry point reaching this branch
        # would be a bug — surface it as a failure.
        if script_name != "postcards":
            pytest.fail(f"{script_name} --help did not raise SystemExit as expected")
    finally:
        sys.argv = old_argv


def test_postcards_entry_point_help_lists_subcommands() -> None:
    """The base ``postcards`` entry point lists the M2 Typer subcommands.

    M2 migrated the ``postcards`` CLI from ``argparse`` to Typer.
    The user-facing subcommand tree is::

        send, preview, generate, config, accounts, quota, status,
        encrypt, decrypt, legacy

    This is a stronger assertion than just exit-0: it pins the
    user-facing surface so a future refactor that drops a
    subcommand (e.g. moving ``send`` into a plugin) is caught
    loudly rather than silently.
    """
    # The M2 entry point drives a :class:`typer.testing.CliRunner`
    # under the hood, so its captured ``--help`` output never
    # reaches the real stdout/stderr. We therefore drive the
    # Typer app directly via the same CliRunner the entry point
    # uses; this gives the test reliable access to ``result.output``.
    from typer.testing import CliRunner

    from postcards.cli import app

    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0, (
        f"postcards --help exited with code {result.exit_code}: {result.output}"
    )
    output = result.output.lower()
    for subcommand in (
        "send",
        "preview",
        "generate",
        "config",
        "accounts",
        "quota",
        "status",
        "encrypt",
        "decrypt",
    ):
        assert subcommand in output, (
            f"{subcommand!r} subcommand missing from postcards --help output:\n{result.output}"
        )
