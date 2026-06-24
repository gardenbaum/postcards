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
    """``pyproject.toml`` declares at least the six known entry points.

    We assert >= 6 rather than an exact match so adding a new plugin
    does not require updating this test; the per-entry smoke tests
    below iterate the actual list and will catch a broken entry point
    automatically.
    """
    assert len(postcards_entry_points) >= 6
    expected = {
        "postcards",
        "postcards-folder",
        "postcards-yaml",
        "postcards-pexels",
        "postcards-random",
        "postcards-chuck-norris",
    }
    missing = expected - set(postcards_entry_points)
    assert not missing, f"missing console scripts: {sorted(missing)}"


@pytest.mark.parametrize(
    "script_name",
    [
        "postcards",
        "postcards-folder",
        "postcards-yaml",
        "postcards-pexels",
        "postcards-random",
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
        "postcards-random",
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

    # Both the base ``postcards`` entry point and the plugin entry
    # points end up calling ``argparse.ArgumentParser.parse_args``
    # (which reads ``sys.argv``). The base entry point's ``main(argv=)``
    # parameter is currently a no-op in practice — see
    # ``Postcards._build_root_parser`` and ``Postcards.main``: they
    # forward ``argv`` for the ``trace`` log but ``parse_args`` reads
    # from ``sys.argv``. We therefore mock ``sys.argv`` for every
    # entry point, which matches what the console-script wrapper
    # would have done at startup.
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
        pytest.fail(f"{script_name} --help did not raise SystemExit as expected")
    finally:
        sys.argv = old_argv


def test_postcards_entry_point_help_lists_subcommands() -> None:
    """The base ``postcards`` entry point lists the ``generate`` / ``send`` / ``encrypt`` / ``decrypt`` subcommands.

    This is a stronger assertion than just exit-0: it pins the user-facing
    surface so a future refactor that drops a subcommand (e.g. moving
    ``send`` into a plugin) is caught loudly rather than silently.
    """
    main_fn = _find_entry_point("postcards").load()

    import contextlib
    import io

    # ``argparse.parse_args`` reads ``sys.argv`` (see note in
    # ``test_console_script_help_exits_zero``); mock it so the test
    # does not depend on pytest's argv.
    old_argv = sys.argv
    sys.argv = ["postcards", "--help"]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), pytest.raises(SystemExit) as excinfo:
            main_fn(["postcards", "--help"])
    finally:
        sys.argv = old_argv
    assert excinfo.value.code == 0
    output = buf.getvalue().lower()
    for subcommand in ("generate", "send", "encrypt", "decrypt"):
        assert subcommand in output, (
            f"{subcommand!r} subcommand missing from postcards --help output:\n{buf.getvalue()}"
        )
