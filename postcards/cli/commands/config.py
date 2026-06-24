"""``postcards config {init,show,set}`` — manage the config file.

The config file is the JSON document the CLI reads accounts,
addresses, and the backend name from. The ``init`` subcommand
writes a starter file; ``show`` prints the resolved config;
``set`` updates a single field in place.

Design
------

All three subcommands operate on a *resolved* config path
(default: ``./config.json``, honouring ``POSTCARDS_CONFIG``).
That keeps the user-visible surface consistent: the same path
is used by every read and write. The actual I/O is delegated
to :mod:`postcards.cli.config_io` so the command bodies stay
focused on the user-facing semantics.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from postcards.cli.app import app
from postcards.cli.config_io import read_config, resolve_config_path, write_config
from postcards.cli.errors import CLIError

# ``config`` is a Typer sub-group; the @config_app.command(...)
# decorators below register the subcommands.
config_app = typer.Typer(
    name="config",
    help="Manage the postcards config file (init / show / set).",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app.add_typer(config_app)


@config_app.command(
    name="init", help="Write a starter config file (alias for 'postcards generate')."
)
def config_init(
    path: Path | None = typer.Option(
        None,
        "-o",
        "--output",
        help="Destination path. Defaults to ./config.json (or POSTCARDS_CONFIG).",
    ),
    force: bool = typer.Option(
        False,
        "-f",
        "--force",
        help="Overwrite an existing file at the destination.",
        is_flag=True,
    ),
) -> None:
    """Write a starter config file at the resolved path.

    ``config init`` is a thin wrapper over ``postcards generate``;
    both share the same template and the same ``--force`` flag.
    The duplication is intentional: ``generate`` predates the
    M2 command set and lives on for backward compatibility, but
    the canonical name going forward is ``config init``.
    """
    target = resolve_config_path(path)
    if target.exists() and not force:
        raise CLIError(
            f"refusing to overwrite existing file at {target} (pass --force to override)",
            exit_code=2,
        )
    # Delegate to the generate command body via Typer's invoke.
    from importlib import resources

    content = (
        resources.files("postcards").joinpath("template_config.json").read_text(encoding="utf-8")
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    typer.echo(f"wrote starter config to {target}")


@config_app.command(name="show", help="Print the resolved config file (after envvar substitution).")
def config_show(
    path: Path | None = typer.Option(
        None,
        "-c",
        "--config",
        help="Path to the config file. Defaults to ./config.json (or POSTCARDS_CONFIG).",
    ),
    redact: bool = typer.Option(
        True,
        "--redact/--no-redact",
        help="Mask passwords and the encryption key in the printed JSON.",
    ),
) -> None:
    """Pretty-print the config file as JSON.

    By default passwords are masked (``***``) so the output is
    safe to paste into a bug report. ``--no-redact`` disables
    the masking; that is useful when the user wants to verify
    the encrypted-password bytes the loader will see.
    """
    target = resolve_config_path(path)
    data = read_config(target)
    if redact and isinstance(data, dict):
        data = _redact_secrets(data)
    typer.echo(f"# {target}")
    typer.echo(json.dumps(data, indent=2, sort_keys=True))


@config_app.command(name="set", help="Update a single field in the config file.")
def config_set(
    key_path: str = typer.Argument(
        ...,
        help=(
            "Dotted path to the field to set, e.g. 'recipient.firstname' or 'accounts.0.username'."
        ),
    ),
    value: str = typer.Argument(..., help="The new value (always stored as a string)."),
    path: Path | None = typer.Option(
        None,
        "-c",
        "--config",
        help="Path to the config file. Defaults to ./config.json (or POSTCARDS_CONFIG).",
    ),
) -> None:
    """Set ``key_path`` to ``value`` in the config file.

    The key path uses dot notation:

    * ``recipient.firstname`` — scalar field.
    * ``accounts.0.username`` — first account's username.
    * ``backend`` — top-level field.

    Indices are zero-based. The function refuses to operate on
    a non-existent config file (call ``config init`` first).
    """
    target = resolve_config_path(path)
    if not target.is_file():
        raise CLIError(
            f"config file not found at {target}; run 'postcards config init' first",
            exit_code=2,
        )
    data = read_config(target)
    _set_by_dotted_path(data, key_path, value)
    write_config(target, data)
    typer.echo(f"set {key_path} = {value!r} in {target}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _redact_secrets(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``data`` with passwords and keys masked."""
    redacted: dict[str, Any] = {}
    for key, value in data.items():
        if key in {"password", "key", "POSTCARDS_KEY"}:
            redacted[key] = "***"
        elif key == "accounts" and isinstance(value, list):
            redacted[key] = [
                {**entry, "password": "***"} if isinstance(entry, dict) else entry
                for entry in value
            ]
        elif isinstance(value, dict):
            redacted[key] = _redact_secrets(value)
        else:
            redacted[key] = value
    return redacted


def _set_by_dotted_path(data: dict[str, Any], key_path: str, value: str) -> None:
    """Set ``key_path`` (dot notation) in ``data`` to ``value``.

    Intermediate objects are created on demand. Indices like
    ``accounts.0`` are coerced to integers when the parent key
    holds a list, so the function works against a freshly
    initialised config that has ``accounts: []``.
    """
    parts = key_path.split(".")
    if not parts:
        raise CLIError("key path must not be empty", exit_code=2)
    cursor: Any = data
    for index, part in enumerate(parts[:-1]):
        next_part = parts[index + 1]
        next_is_index = next_part.isdigit()
        if isinstance(cursor, list):
            try:
                part_idx = int(part)
            except ValueError as exc:
                raise CLIError(
                    f"cannot traverse list with non-integer key {part!r}",
                    exit_code=2,
                ) from exc
            while len(cursor) <= part_idx:
                cursor.append({} if not next_is_index else None)
            if not isinstance(cursor[part_idx], dict):
                cursor[part_idx] = {}
            cursor = cursor[part_idx]
        else:
            if part not in cursor or not isinstance(cursor[part], (dict, list)):
                cursor[part] = [] if next_is_index else {}
            cursor = cursor[part]
    leaf = parts[-1]
    if isinstance(cursor, list):
        try:
            leaf_idx = int(leaf)
        except ValueError as exc:
            raise CLIError(f"cannot assign to non-integer list key {leaf!r}", exit_code=2) from exc
        while len(cursor) <= leaf_idx:
            cursor.append(None)
        cursor[leaf_idx] = value
    else:
        cursor[leaf] = value


__all__ = ["config_app"]
