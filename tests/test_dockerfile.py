"""Static checks for the M6 Dockerfile.

These tests parse the ``Dockerfile`` line by line and assert the
shape that the M6 docs (``docs/DOCKER.md``) and the README install
section promise. They do NOT build the image — that needs Docker,
and the CI matrix does not have a daemon. The intent is to catch
the "someone edited the Dockerfile and removed the entry point"
class of regression at PR time, before the image gets rebuilt on
the next release.

Why we test the Dockerfile from Python
--------------------------------------

- A typo in ``CMD`` only surfaces when the user runs the image.
- A missing ``USER`` directive is invisible until the image is
  shipped to a hardened runtime (k8s, rootless podman, etc.).
- A dropped ``ENTRYPOINT`` turns the image into a one-off bash
  shell rather than a CLI runner, breaking every docs example
  that assumes ``docker run postcards:dev send ...`` works.

The parser is a minimal line scanner, not a full Dockerfile AST —
good enough for these assertions, and trivial to extend.
"""

from __future__ import annotations

import re
from pathlib import Path


def _read_dockerfile() -> list[str]:
    """Return the Dockerfile lines with comments and blanks preserved.

    Blank lines and ``#`` comments are kept (not stripped) so the
    line numbers used in assertions are stable against cosmetic
    edits.
    """
    dockerfile = Path(__file__).resolve().parent.parent / "Dockerfile"
    return dockerfile.read_text().splitlines()


def _directive(directive: str, lines: list[str]) -> list[tuple[int, str]]:
    """Return ``(line_number, instruction_value)`` for every directive.

    ``directive`` is matched case-insensitively (Dockerfile
    instructions are case-insensitive in spec but we always write
    them uppercase). The instruction value is the remainder of the
    line after the directive name.

    Line continuations (``\\`` at end of line) are joined so a
    single multi-line instruction is reported as one match anchored
    at the line number where the directive first appears.
    """
    pattern = re.compile(rf"^\s*{re.escape(directive)}\s+(.+?)\s*$", re.IGNORECASE)
    found: list[tuple[int, str]] = []
    line_no = 0
    while line_no < len(lines):
        line_no += 1
        line = lines[line_no - 1]
        match = pattern.match(line)
        if match:
            value = match.group(1)
            directive_line = line_no
            # Join line continuations. Dockerfile's line-continuation
            # rule is a trailing backslash; the joined value is what
            # the parser actually sees. The directive stays anchored
            # at the first physical line for stable error messages.
            while value.endswith("\\") and line_no < len(lines):
                line_no += 1
                next_line = lines[line_no - 1].strip()
                value = value[:-1].rstrip() + " " + next_line
            found.append((directive_line, value))
    return found


# ---------------------------------------------------------------------------
# Image shape
# ---------------------------------------------------------------------------


def test_dockerfile_exists() -> None:
    """The repo root has a ``Dockerfile``."""
    dockerfile = Path(__file__).resolve().parent.parent / "Dockerfile"
    assert dockerfile.is_file(), "Dockerfile is missing at the repo root"


def test_dockerfile_multi_stage() -> None:
    """The Dockerfile uses at least two stages (``build`` + ``runtime``)."""
    from_stages = _directive("FROM", _read_dockerfile())
    assert len(from_stages) >= 2, (
        f"Dockerfile must be multi-stage (build + runtime); "
        f"found {len(from_stages)} FROM instructions"
    )


def test_dockerfile_base_image_is_python_313_slim() -> None:
    """The build stage uses ``python:3.13-slim``.

    The constitution (§3) mandates Python 3.12 / 3.13 in CI; 3.13
    is the latest of those and ships the smallest pre-built image
    on Docker Hub. Pinning a specific Python minor version
    prevents a silent bump from breaking the ``>=3.12`` constraint.
    """
    froms = _directive("FROM", _read_dockerfile())
    assert froms, "Dockerfile has no FROM instructions"
    # The build stage is the first FROM.
    first_image = froms[0][1].split(" AS ")[0].strip()
    assert first_image == "python:3.13-slim", (
        f"build stage must be python:3.13-slim, got {first_image!r}"
    )


def test_dockerfile_runtime_uses_same_python_image() -> None:
    """The runtime stage is also ``python:3.13-slim``.

    A common mistake is to build on 3.13 and run on 3.12 (or
    vice-versa). Both stages must pin the same minor to keep the
    ``pyproject.toml`` ``requires-python`` guarantee honest.
    """
    froms = _directive("FROM", _read_dockerfile())
    runtime_froms = [(idx, val) for idx, val in froms if " AS runtime" in val]
    assert runtime_froms, "Dockerfile is missing a runtime stage"
    image = runtime_froms[0][1].split(" AS ")[0].strip()
    assert image == "python:3.13-slim", f"runtime stage must be python:3.13-slim, got {image!r}"


def test_dockerfile_installs_from_wheel() -> None:
    """The runtime stage installs the wheel built in the build stage.

    The build stage uses ``pip wheel`` to produce
    ``/wheels/*.whl``; the runtime stage copies that directory
    and runs ``pip install --no-index --find-links /wheels``.
    Asserting both ends of the wire prevents one half of the
    pipeline from being silently dropped.
    """
    lines = _read_dockerfile()
    assert any("pip wheel" in line for line in lines), (
        "build stage must use 'pip wheel' to produce the wheel"
    )
    assert any("--find-links /wheels" in line for line in lines), (
        "runtime stage must install the wheel from /wheels"
    )


def test_dockerfile_no_cache_dir() -> None:
    """Every ``pip install`` uses ``--no-cache-dir``.

    Without it the image is ~30 MB larger (the pip cache) and the
    Dockerfile's reproducibility story gets weaker. The CI image
    is small enough that this matters; the test makes it a
    contract.
    """
    lines = _read_dockerfile()
    pip_lines = [
        line
        for line in lines
        if ("pip install" in line or "pip wheel" in line)
        # Comments frequently mention ``pip install`` in prose;
        # only real instructions matter.
        and not line.lstrip().startswith("#")
    ]
    assert pip_lines, "Dockerfile has no pip install / wheel lines"
    missing = [line for line in pip_lines if "--no-cache-dir" not in line]
    assert not missing, f"every pip invocation must pass --no-cache-dir; missing on: {missing}"


# ---------------------------------------------------------------------------
# Runtime configuration
# ---------------------------------------------------------------------------


def test_dockerfile_runs_as_non_root_user() -> None:
    """The runtime stage ends with a ``USER`` directive.

    Running the CLI as root is a security smell; the docs and the
    image both assume a non-root UID for bind mounts. The test
    asserts the directive is present — it does not check the UID
    (kept at 1000 by convention, but a future change should be
    intentional).
    """
    user_lines = _directive("USER", _read_dockerfile())
    assert user_lines, "Dockerfile must declare a USER directive (no root)"
    # The last USER directive in the file is what actually applies
    # at runtime; assert it's not "root" / "0".
    last_user = user_lines[-1][1]
    assert last_user.lower() not in {"root", "0"}, (
        f"Dockerfile runs as {last_user!r}; non-root is required"
    )


def test_dockerfile_entrypoint_is_postcards() -> None:
    """``ENTRYPOINT`` is set so the container is a CLI runner.

    With ``ENTRYPOINT ["postcards"]``, ``docker run postcards:dev
    send ...`` works exactly like the README snippet promises. A
    missing or wrong entry point turns the image into a generic
    Python container.
    """
    entrypoints = _directive("ENTRYPOINT", _read_dockerfile())
    assert entrypoints, "Dockerfile must declare ENTRYPOINT"
    last = entrypoints[-1][1]
    # JSON-array form, e.g. ["postcards"]. Exec form is required for
    # the signal-handling story; assert no shell form.
    assert last.startswith("["), f"ENTRYPOINT must be exec form (JSON array); got {last!r}"
    assert "postcards" in last, f"ENTRYPOINT must reference the postcards binary; got {last!r}"


def test_dockerfile_default_cmd_is_help() -> None:
    """The default ``CMD`` is ``["--help"]`` (or equivalent).

    A bare ``docker run postcards:dev`` that does nothing
    silently is a UX trap; showing help on the no-args path is
    the same idea as ``postcards --help`` from the user's shell.
    """
    cmds = _directive("CMD", _read_dockerfile())
    assert cmds, "Dockerfile must declare a default CMD"
    last = cmds[-1][1]
    assert "--help" in last, f"default CMD should show help; got {last!r}"


def test_dockerfile_xdg_vars_set() -> None:
    """The runtime stage sets the ``XDG_*`` env vars.

    The address book + scheduler write to
    ``$XDG_DATA_HOME/postcards/``. Without an explicit
    ``XDG_DATA_HOME``, the address-book commands land in
    ``/home/postcards/.local/share`` (the XDG default) which is
    what we want, but pinning the env vars in the Dockerfile
    makes the path predictable for bind mounts.
    """
    envs = _directive("ENV", _read_dockerfile())
    # A single ``ENV`` directive may set multiple vars (``ENV
    # XDG_DATA_HOME=... XDG_CONFIG_HOME=...``). Walk each token
    # that looks like ``NAME=...`` and collect the names.
    env_names: set[str] = set()
    for _, value in envs:
        for token in value.split():
            if "=" in token:
                env_names.add(token.split("=", 1)[0])
    for required in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        assert required in env_names, (
            f"Dockerfile must set {required} so address-book data has "
            f"a stable mount path; env names found: {sorted(env_names)}"
        )


# ---------------------------------------------------------------------------
# Hygiene
# ---------------------------------------------------------------------------


def test_dockerfile_no_secrets_baked_in() -> None:
    """The Dockerfile does not carry a literal SwissID credential.

    The constitution (§2) forbids plain credentials in tracked
    files. A regex over the Dockerfile catches the easy mistakes:
    someone types ``ENV POSTCARDS_PASSWORD=...`` instead of
    documenting ``docker run -e POSTCARDS_PASSWORD=...``; someone
    copies a config.json into the image.

    The match is intentionally coarse — anything that looks like a
    ``PASSWORD=`` or ``SECRET=`` assignment is flagged. False
    positives are cheap; the goal is to make accidental cred
    commits noisy.
    """
    pattern = re.compile(
        r"(?i)\b(password|secret|token|api[_-]?key)\s*=\s*\S",
    )
    offenders: list[str] = []
    for idx, line in enumerate(_read_dockerfile(), start=1):
        # Comments are fine; only flag real instructions.
        if line.lstrip().startswith("#"):
            continue
        if pattern.search(line):
            offenders.append(f"line {idx}: {line}")
    assert not offenders, "Dockerfile appears to bake a credential into a layer:\n" + "\n".join(
        offenders
    )


def test_dockerignore_present() -> None:
    """A ``.dockerignore`` exists at the repo root.

    The build context would otherwise include ``.git``, ``.venv``,
    ``.mypy_cache``, etc. — wastes bandwidth and changes layer
    hashes between identical-looking builds.
    """
    dockerignore = Path(__file__).resolve().parent.parent / ".dockerignore"
    assert dockerignore.is_file(), (
        ".dockerignore is missing; the build context will include .git, .venv, and tooling caches"
    )
    contents = dockerignore.read_text()
    for required in (".git", ".venv", ".mypy_cache", "tests"):
        assert required in contents, (
            f".dockerignore must exclude {required!r} from the build context"
        )
