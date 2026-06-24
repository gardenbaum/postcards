"""``postcards templates {add,list,show,update,render,remove}`` — manage message templates.

This command group is the user-facing surface for the
:class:`postcards.addressbook.models.TemplateBook` data. The
book lives next to the address book under the per-user data
directory (``$XDG_DATA_HOME/postcards/templates.json`` by
default, overridable via :data:`POSTCARDS_DATA_DIR`).

CLI surface
-----------

* ``templates add NAME [--description ...] [--body ...] [--file PATH]``
  — create a new template. The body is read from ``--file``
  when ``--file`` is given, otherwise from ``--body`` (or
  stdin when ``--body`` is ``-``).
* ``templates list`` — tabular summary.
* ``templates show NAME`` — print a single template's body and
  description.
* ``templates update NAME [--description ...] [--body ...] [--file PATH]``
  — patch the description and/or body. ``--body`` and ``--file``
  are mutually exclusive (one must be supplied to update).
* ``templates render NAME [--var key=value]...`` — render the
  template with the supplied variables. ``$name`` and
  ``${name}`` placeholders are substituted; missing variables
  fail with a clear error.
* ``templates remove NAME`` — delete a template.

Persistence is delegated to :mod:`postcards.addressbook.storage`,
so the CLI body stays focused on argument parsing and
user-facing error messages.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from postcards.addressbook.models import (
    MessageTemplate,
    TemplateError,
)
from postcards.addressbook.storage import (
    load_template_book,
    save_template_book,
)
from postcards.addressbook.variables import (
    TemplateRenderError,
)
from postcards.cli.app import app
from postcards.cli.errors import raise_cli_error

templates_app = typer.Typer(
    name="templates",
    help="Manage message templates under $XDG_DATA_HOME/postcards.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app.add_typer(templates_app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_body(body: str | None, file: Path | None) -> str:
    """Return the template body from ``--body`` or ``--file``.

    Resolution rules:

    * When ``file`` is given, the body is read from disk. The
      file is read as UTF-8 with ``errors="strict"`` so a
      non-UTF-8 file surfaces a clear encoding error.
    * When ``body == "-"``, the body is read from stdin. This
      is the form users reach for when the message is long
      (the Swiss Postcard Creator caps the body at 500 chars
      but a template can be longer as long as the rendered
      output is under the cap).
    * Otherwise ``body`` is returned verbatim (an empty string
      is allowed — empty templates are useful as "no body" and
      make the CLI forgiving).

    ``body`` and ``file`` are mutually exclusive; the caller
    enforces that.
    """
    if file is not None:
        try:
            return file.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise_cli_error(
                f"failed to read template body from {file}: {exc.reason}",
                exit_code=2,
            )
        except OSError as exc:
            raise_cli_error(
                f"failed to read template body from {file}: {exc.strerror or exc}",
                exit_code=2,
            )
    if body == "-":
        return typer.get_text_stream("stdin").read()
    return body or ""


def _check_body_file_exclusive(body: str | None, file: Path | None) -> None:
    """Reject ``--body`` / ``--file`` being supplied together."""
    if body is not None and file is not None:
        raise_cli_error(
            "--body and --file are mutually exclusive; pass one or the other",
            exit_code=2,
        )


def _parse_var(arg: str) -> tuple[str, str]:
    """Parse a ``key=value`` pair supplied to ``--var``.

    Raises a CLI error when the pair is malformed (no ``=``,
    empty key, empty value). Empty *values* are allowed via
    ``--var name=`` so users can render templates that have
    an explicit empty placeholder.
    """
    if "=" not in arg:
        raise_cli_error(
            f"--var {arg!r} is malformed; expected KEY=VALUE",
            exit_code=2,
        )
    key, value = arg.split("=", 1)
    key = key.strip()
    if not key:
        raise_cli_error(f"--var {arg!r} has an empty key", exit_code=2)
    return key, value


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


@templates_app.command(
    name="add",
    help="Add a new message template.",
)
def templates_add(
    name: Annotated[str, typer.Argument(help="Unique identifier for the template.")],
    description: Annotated[
        str | None,
        typer.Option(
            "--description",
            "-d",
            help="Human-readable description (free-form).",
        ),
    ] = None,
    body: Annotated[
        str | None,
        typer.Option(
            "--body",
            "-b",
            help=("Template body. Use {name} / $name placeholders. Pass '-' to read from stdin."),
        ),
    ] = None,
    file: Annotated[
        Path | None,
        typer.Option(
            "--file",
            "-f",
            help="Read the template body from this file.",
            exists=True,
            readable=True,
            dir_okay=False,
        ),
    ] = None,
) -> None:
    """Create a new template and append it to the book.

    The body is required; pass it via ``--body BODY`` or
    ``--file PATH``. ``--body -`` reads from stdin. ``--body``
    and ``--file`` are mutually exclusive.
    """
    if body is None and file is None:
        raise_cli_error(
            "either --body or --file is required (use --body - to read from stdin)",
            exit_code=2,
        )
    _check_body_file_exclusive(body, file)
    resolved_body = _read_body(body, file)

    try:
        template = MessageTemplate(
            name=name,
            body=resolved_body,
            description=description or "",
        )
    except TemplateError as exc:
        raise_cli_error(str(exc), exit_code=2)
    book = load_template_book()
    try:
        new_book = book.add(template)
    except TemplateError as exc:
        raise_cli_error(str(exc), exit_code=2)
    save_template_book(new_book)
    typer.echo(f"added template {name!r}")


@templates_app.command(
    name="list",
    help="List message templates.",
)
def templates_list() -> None:
    """Print a tabular summary of the template book."""
    book = load_template_book()
    if book.is_empty():
        typer.echo("no templates yet; add one with 'postcards templates add NAME --body ...'")
        return
    typer.echo(f"{'NAME':<24} {'DESCRIPTION':<40} BODY")
    for template in book:
        description = template.description or ""
        snippet = template.body.splitlines()[0] if template.body else ""
        typer.echo(f"{template.name:<24} {description:<40} {snippet}")


@templates_app.command(
    name="show",
    help="Show a single template.",
)
def templates_show(
    name: Annotated[str, typer.Argument(help="Name of the template to display.")],
) -> None:
    """Print a single template's body and description."""
    book = load_template_book()
    template = book.find(name)
    if template is None:
        raise_cli_error(f"no template named {name!r}", exit_code=2)
    typer.echo(f"name        : {template.name}")
    typer.echo(f"description : {template.description}")
    typer.echo("body        :")
    for line in template.body.splitlines() or [""]:
        typer.echo(f"  {line}")


@templates_app.command(
    name="update",
    help="Patch a template's description and/or body.",
)
def templates_update(
    name: Annotated[str, typer.Argument(help="Name of the template to update.")],
    description: Annotated[
        str | None,
        typer.Option(
            "--description",
            "-d",
            help="New description. Pass '' to clear.",
        ),
    ] = None,
    body: Annotated[
        str | None,
        typer.Option(
            "--body",
            "-b",
            help="New body. Pass '-' to read from stdin.",
        ),
    ] = None,
    file: Annotated[
        Path | None,
        typer.Option(
            "--file",
            "-f",
            help="Read the new body from this file.",
            exists=True,
            readable=True,
            dir_okay=False,
        ),
    ] = None,
) -> None:
    """Update ``name``'s description and/or body.

    At least one of ``--description``, ``--body``, or ``--file``
    must be supplied; the function refuses no-op updates. To
    explicitly clear the description, pass ``--description ""``.
    """
    _check_body_file_exclusive(body, file)
    book = load_template_book()
    existing = book.find(name)
    if existing is None:
        raise_cli_error(
            f"no template named {name!r}; create it first with 'postcards templates add'",
            exit_code=2,
        )
    if description is None and body is None and file is None:
        raise_cli_error(
            "no fields to update; pass --description, --body, or --file",
            exit_code=2,
        )

    new_description = existing.description if description is None else description
    new_body = existing.body if body is None and file is None else _read_body(body, file)
    try:
        updated = MessageTemplate(
            name=existing.name,
            body=new_body,
            description=new_description,
        )
    except TemplateError as exc:
        raise_cli_error(str(exc), exit_code=2)
    try:
        new_book = book.update(updated)
    except TemplateError as exc:
        raise_cli_error(str(exc), exit_code=2)
    save_template_book(new_book)
    typer.echo(f"updated template {name!r}")


@templates_app.command(
    name="render",
    help="Render a template with the supplied variables.",
)
def templates_render(
    name: Annotated[str, typer.Argument(help="Name of the template to render.")],
    var: Annotated[
        list[str] | None,
        typer.Option(
            "--var",
            "-V",
            help=(
                "Template variable in KEY=VALUE form. Repeat to pass multiple "
                "(e.g. --var name=Alice --var city=Zurich)."
            ),
        ),
    ] = None,
) -> None:
    """Print the rendered template to stdout.

    The substitution rules are delegated to
    :func:`postcards.addressbook.variables.render_template` —
    a referenced but un-supplied variable surfaces as a
    :class:`TemplateRenderError` which the CLI converts into a
    user-facing error with exit code 2.
    """
    book = load_template_book()
    template = book.find(name)
    if template is None:
        raise_cli_error(f"no template named {name!r}", exit_code=2)
    variables: dict[str, str] = {}
    for raw in var or []:
        key, value = _parse_var(raw)
        variables[key] = value
    try:
        rendered = template.render(variables)
    except TemplateRenderError as exc:
        raise_cli_error(str(exc), exit_code=2)
    typer.echo(rendered)


@templates_app.command(
    name="remove",
    help="Remove a template.",
)
def templates_remove(
    name: Annotated[str, typer.Argument(help="Name of the template to remove.")],
    yes: Annotated[
        bool,
        typer.Option(
            "-y",
            "--yes",
            help="Skip the confirmation prompt.",
        ),
    ] = False,
) -> None:
    """Delete the template named ``name``.

    ``--yes`` skips the confirmation prompt for scripting.
    """
    book = load_template_book()
    if book.find(name) is None:
        raise_cli_error(f"no template named {name!r}", exit_code=2)
    if not yes:
        confirmed = typer.confirm(f"remove template {name!r}?", default=False)
        if not confirmed:
            typer.echo("aborted")
            raise typer.Exit(code=1)
    try:
        new_book = book.remove(name)
    except TemplateError as exc:
        raise_cli_error(str(exc), exit_code=2)
    save_template_book(new_book)
    typer.echo(f"removed template {name!r}")


__all__ = ["templates_app"]
