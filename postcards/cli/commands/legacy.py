"""``postcards legacy`` — escape hatch for the plugin subcommands.

The legacy ``postcards`` console script (pre-M2) accepted
plugin subcommands directly: ``postcards slice ...``,
``postcards generate-random ...``, and so on. Those subcommands
are owned by the plugin console scripts (``postcards-folder``,
``postcards-random``, ...) and M2 does not migrate them to
Typer.

To keep the user-facing surface stable, ``postcards legacy``
is a thin escape hatch: it forwards the rest of the argv to
the legacy :func:`postcards.postcards.main` parser. Example::

    postcards legacy slice -p image.jpg -W 200 -H 300

is equivalent to invoking the legacy ``postcards`` binary with
the same arguments. Power users who relied on
``postcards <plugin-subcommand>`` can keep using it under
``postcards legacy <plugin-subcommand>``; new users should use
the dedicated plugin entry points instead.

Why a subcommand, not a top-level fallback
------------------------------------------

Typer does not have a clean "dispatch unknown subcommand to a
fallback" hook — a top-level ``@app.command`` would shadow the
named subcommands (``send``, ``preview``, ...). The
``postcards legacy`` prefix is explicit, easy to discover via
``--help``, and matches the convention other CLIs use for
backward-compat fallbacks (``pip legacy-resolver``,
``kubectl alpha``, ...).
"""

from __future__ import annotations

import typer

from postcards.cli.app import app
from postcards.cli.errors import raise_cli_error

legacy_app = typer.Typer(
    name="legacy",
    help=(
        "Run the pre-M2 argparse-based CLI (for plugin subcommands like 'slice'). "
        "Prefer the dedicated plugin entry points (postcards-folder, postcards-random, "
        "...) for new workflows."
    ),
    no_args_is_help=True,
    rich_markup_mode=None,
    add_completion=False,
)
app.add_typer(legacy_app)


@legacy_app.command(
    name="run",
    help="Forward the rest of the argv to the legacy postcards.postcards.main() parser.",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
def legacy_run(
    ctx: typer.Context,
    args: list[str] | None = typer.Argument(
        None,
        help="Arguments to forward to the legacy parser.",
    ),
) -> None:
    """Invoke :func:`postcards.postcards.main` with the supplied args.

    The function exits with the same code the legacy main would
    have used (it does not catch ``SystemExit``). A
    :class:`postcards.cli.errors.CLIError` is raised only when
    the legacy parser itself is unavailable (e.g. the
    ``postcards.postcards`` module was deleted by a future
    refactor).
    """
    try:
        from postcards.postcards import main as legacy_main
    except ImportError:  # pragma: no cover — defensive
        raise_cli_error(
            "the legacy postcards.postcards module is unavailable; "
            "use the dedicated plugin entry points (postcards-folder, ...)"
        )

    argv: list[str] = ["postcards", *(args or [])]
    legacy_main(argv)


__all__ = ["legacy_app", "legacy_run"]
