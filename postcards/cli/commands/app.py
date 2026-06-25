"""``postcards app`` — launch the interactive WYSIWYG web app.

The app is the primary way to compose a postcard with a full live
preview (Front + Back, exactly as Swiss Post prints it) and then send
it. It requires the optional ``app`` extra; the command surfaces a
clear ``pip install 'postcards[app]'`` message when NiceGUI is missing,
so the core CLI stays importable on minimal installs.
"""

from __future__ import annotations

import typer

from postcards.cli.app import app
from postcards.cli.errors import raise_cli_error


@app.command(
    name="app",
    help=(
        "Launch the interactive WYSIWYG web app (live Front/Back preview, "
        "then send). Requires the optional 'app' extra: "
        "pip install 'postcards[app]'."
    ),
)
def app_cmd(
    host: str = typer.Option("127.0.0.1", "--host", help="Interface to bind."),
    port: int = typer.Option(8080, "--port", help="Port to listen on."),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Do not open the browser automatically."
    ),
) -> None:
    """Start the NiceGUI server and (by default) open the browser."""
    try:
        from postcards.web.app import run_app
    except ImportError as exc:  # pragma: no cover — guarded by package install
        raise raise_cli_error(
            "the web app requires the 'app' extra; install with \"pip install 'postcards[app]'\"",
            exit_code=2,
        ) from exc

    run_app(host=host, port=port, show=not no_browser, reload=False)


__all__ = ["app_cmd"]
