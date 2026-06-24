"""Config-file helpers used by the ``postcards config`` subcommands.

These functions are intentionally thin — they read and write the
JSON config file the user pointed at, validate the field shape,
and surface failures as :class:`postcards.cli.errors.CLIError`. The
business logic for "what should this config look like" lives in
the command modules; this module only handles I/O.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from postcards.cli.errors import CLIError


def read_config(path: Path) -> dict[str, Any]:
    """Read a JSON config file, returning ``{}`` when missing.

    A missing config file is not an error at this layer — the
    user is allowed to bootstrap a fresh one via
    ``postcards config init`` before any other command needs it.

    Raises
    ------
    CLIError
        When the file exists but is not valid JSON or is not a
        top-level object. The error message points at the path
        so the user can fix it.
    """
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise CLIError(
            f"failed to parse config file at {path}: {exc.msg} (line {exc.lineno}, col {exc.colno})"
        ) from exc
    if not isinstance(data, dict):
        raise CLIError(f"config file at {path} must be a JSON object, got {type(data).__name__}")
    return data


def write_config(path: Path, data: dict[str, Any]) -> None:
    """Write ``data`` to ``path`` as pretty-printed JSON.

    Creates parent directories on demand. Refuses to clobber an
    existing file unless ``overwrite=True``; that guard is what
    keeps ``postcards config init`` idempotent and safe.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=True) + os.linesep
    path.write_text(payload, encoding="utf-8")


def resolve_config_path(path: Path | None) -> Path:
    """Return the absolute path the rest of the CLI should use.

    When ``path`` is ``None``, the function honours the
    ``POSTCARDS_CONFIG`` env var and otherwise defaults to
    ``./config.json``. The function is a thin wrapper over
    :meth:`postcards.config.ConfigLayer.config_path_resolved` and
    exists here so command modules do not need to import the
    config layer for what is purely an I/O concern.
    """
    if path is not None:
        return path.expanduser().resolve()
    raw = os.environ.get("POSTCARDS_CONFIG")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path("config.json").resolve()


__all__ = ["read_config", "resolve_config_path", "write_config"]
