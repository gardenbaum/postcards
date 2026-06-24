"""``postcards generate`` — write a starter config file.

Migrated from the legacy ``argparse`` ``generate`` subcommand.
The Typer command writes the bundled ``postcards/template_config.json``
to the path the user asks for (default: ``./config.json``) and
refuses to clobber an existing file unless ``--force`` is given.
"""

from __future__ import annotations

import argparse
import sys
from importlib import resources
from pathlib import Path

import typer

from postcards.cli.app import app
from postcards.cli.errors import CLIError


@app.command(
    name="generate",
    help="Generate a starter config file at the given path.",
    no_args_is_help=True,
)
def generate_cmd(
    path: Path = typer.Option(
        Path("config.json"),
        "-o",
        "--output",
        help="Destination path. Defaults to ./config.json.",
        show_default=True,
    ),
    force: bool = typer.Option(
        False,
        "-f",
        "--force",
        help="Overwrite an existing config file at the destination.",
    ),
    advanced: bool = typer.Option(
        False,
        "--advanced",
        help="Use the advanced template (more fields, comments).",
    ),
) -> None:
    """Write the bundled template to ``--output``.

    The starter template is the same JSON the legacy ``generate``
    subcommand wrote (``postcards/template_config.json``). M2
    adds an ``--advanced`` flag that points at
    ``postcards/template_config_advanced.json`` so power users
    can opt in to the full set of fields without having to hand-
    edit the basic template.
    """
    target = path.expanduser().resolve()
    if target.exists() and not force:
        raise CLIError(
            f"refusing to overwrite existing file at {target} (pass --force to override)",
            exit_code=2,
        )

    template_name = "template_config_advanced.json" if advanced else "template_config.json"
    try:
        content = resources.files("postcards").joinpath(template_name).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        # The ``resources.files`` call can fail in odd ways when
        # the package is not properly installed (e.g. running
        # from a source checkout without ``pip install -e .``).
        # Surface the failure as a clean CLI error rather than
        # an obscure ``ImportError`` or ``FileNotFoundError``.
        raise CLIError(
            f"could not load bundled template {template_name!r}: {exc}",
        ) from exc

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    typer.echo(f"wrote starter config to {target}")


# Re-export the legacy shim so the existing M1 test
# ``tests/test_send_integration.py`` and any other legacy call
# sites keep working. The function signature is unchanged.
def _legacy_generate(args: argparse.Namespace) -> None:
    target = Path(args.path).expanduser().resolve()
    if target.exists() and not getattr(args, "force", False):
        sys.stderr.write(f"error: refusing to overwrite existing file at {target}\n")
        sys.exit(1)
    content = (
        resources.files("postcards").joinpath("template_config.json").read_text(encoding="utf-8")
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


__all__ = ["generate_cmd"]
