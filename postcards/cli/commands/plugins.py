"""``postcards plugins`` — inspect the M3 plugin registry.

Subcommands
-----------

``postcards plugins list``
    Print every registered plugin name + description, sorted
    alphabetically. Includes both the in-tree plugins
    (``folder``, ``folder_yaml``, ``pexels``, ``chuck_norris``)
    and any third-party plugins installed via the
    ``postcards.plugins`` entry-point group.

Why this is a Typer subcommand
------------------------------

The legacy plugin subcommands (``postcards slice``,
``postcards generate-random``) were registered directly on the
``argparse`` root parser. M3 replaces that ad-hoc mechanism
with a single ``plugins`` subcommand tree under the Typer
``postcards`` app so the user can discover the available
plugins without reading source code.
"""

from __future__ import annotations

import typer

from postcards.cli.app import app
from postcards.cli.errors import raise_cli_error

plugins_app = typer.Typer(
    name="plugins",
    help=(
        "Inspect the M3 plugin registry. Use 'postcards plugins list' to see "
        "the in-tree and third-party plugins available in the current "
        "environment."
    ),
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(plugins_app)


@plugins_app.command(
    name="list",
    help="List every registered plugin (in-tree + entry-point discovered).",
)
def plugins_list() -> None:
    """Print ``<name>\\t<description>`` for every registered plugin.

    The output is intentionally tab-separated (not a table) so
    downstream tools can pipe it through ``cut`` or ``awk``. The
    ``postcards plugins list --json`` shape lives behind a
    follow-up card; this command is the human-readable form.
    """
    # Import inside the command body so ``postcards --help`` does
    # not pull the modern plugin stack (and its importlib.metadata
    # entry-point scan) for users who only want to read the help.
    from postcards.plugins.registry import Registry

    registry = Registry.default
    names = registry.names()
    if not names:
        raise_cli_error("no plugins are registered; this is a packaging bug")

    for name in names:
        description = registry.description_for(name) or "(no description)"
        typer.echo(f"{name}\t{description}")


__all__ = ["plugins_app", "plugins_list"]
