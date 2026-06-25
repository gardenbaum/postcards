"""CLI wrapper around :mod:`postcards.backend.messages`.

The translator that turns backend-level exceptions into
user-facing messages lives in :mod:`postcards.backend.messages`
so the schedule runner can use it too (it would be a layering
violation for the runner to depend on the CLI module).
This module re-exports the translator under the CLI's naming
convention and adds the convenience
:func:`raise_for_backend_error` that wraps
:func:`postcards.cli.errors.raise_cli_error`.

Why the indirection
-------------------

``postcards.cli.backend_errors`` predates
:mod:`postcards.backend.messages`. Keeping the CLI-facing
symbol (``render_cli_error``) and the convenience wrapper
(``raise_for_backend_error``) here means existing callers
that imported from the CLI module keep working — they now
delegate to the shared translator.
"""

from __future__ import annotations

from typing import NoReturn

from postcards.backend.messages import translate
from postcards.cli.errors import raise_cli_error


def render_cli_error(exc: BaseException) -> tuple[str, int]:
    """Translate ``exc`` into ``(message, exit_code)``.

    Thin alias for :func:`postcards.backend.messages.translate`
    kept under the CLI naming so existing callers continue to
    work and the CLI surface is discoverable from
    ``postcards.cli.*``.
    """
    return translate(exc)


def raise_for_backend_error(exc: BaseException) -> NoReturn:
    """Translate ``exc`` and exit the CLI with the matching message + code.

    Convenience wrapper around :func:`render_cli_error` +
    :func:`postcards.cli.errors.raise_cli_error`. Use this at
    every command boundary that touches the backend so the user
    gets a consistent hint and exit code.
    """
    message, exit_code = translate(exc)
    raise_cli_error(message, exit_code=exit_code)


__all__ = ["raise_for_backend_error", "render_cli_error"]
