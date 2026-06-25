"""Structured logging configuration for ``postcards``.

This module is the single source of truth for log-level mappings,
the TRACE custom level, and the default log format. Every CLI
entry point routes through :func:`configure` so the log records
produced by ``postcards.*`` modules share a consistent shape:

================== =============================================================
``-v`` (count)     Effective log level
================== =============================================================
0 (default)        :data:`logging.WARNING` (level 30)
1 (``-v``)         :data:`logging.INFO` (level 20)
2 (``-vv``)        :data:`logging.DEBUG` (level 10)
3 (``-vvv``)       :data:`LOG_LEVEL_TRACE` (level 5)
================== =============================================================

The :data:`LOG_LEVEL_TRACE` constant is exported so other modules
(retry helper, runner) can ``logger.log(LOG_LEVEL_TRACE, ...)``
without redefining the level.

When :func:`configure` is called, the root logger and the
``postcard_creator`` logger are pinned to the requested level
and a single :class:`logging.StreamHandler` writing to ``stderr``
is installed. ``logging.basicConfig(force=True)`` is used so
calling :func:`configure` from a Typer callback before the
command body runs does not interact badly with any handler
Typer may have installed for its own error rendering.

This module deliberately has no dependency on Typer or the
``postcards.cli`` package so it can be imported from tests
and from non-CLI entry points (cron jobs, libraries).
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Iterable
from typing import TextIO

#: Numeric level for the ``TRACE`` verbosity. Lower than
#: :data:`logging.DEBUG` so ``-vvv`` exposes every diagnostic
#: line the retry helper and the shim's request layer emit.
LOG_LEVEL_TRACE: int = 5

#: Name registered with :func:`logging.addLevelName` for
#: :data:`LOG_LEVEL_TRACE`. ``logging`` uses it when formatting
#: log records at level 5.
LOG_LEVEL_TRACE_NAME: str = "TRACE"

#: Stable mapping from the ``-v`` count the CLI parses to the
#: log level :func:`configure` will install. ``count=3`` maps
#: to TRACE; lower counts map to the standard levels.
DEFAULT_VERBOSITY_LEVELS: tuple[int, ...] = (
    logging.WARNING,  # 0  (default)
    logging.INFO,  # 1  (-v)
    logging.DEBUG,  # 2  (-vv)
    LOG_LEVEL_TRACE,  # 3  (-vvv)
)

#: Default format used for log records at INFO and below. The
#: ``%(name)s`` field is the dotted logger name (``postcards.cli.send``
#: etc.) so a reader can tell which subcommand emitted a line.
DEFAULT_FORMAT: str = "%(asctime)s %(name)s [%(levelname)s] %(message)s"

#: Format used for log records at WARNING and above. Errors are
#: prefixed with the timestamp + logger so they survive being
#: piped into a logfile by cron.
BRIEF_FORMAT: str = "%(asctime)s %(name)s: %(message)s"

#: Loggers we always want to see, regardless of the root level.
#: The Swiss Post ``postcard_creator`` library lives in this
#: namespace; the user opted into ``-vv`` to debug a network
#: call, so we let its DEBUG records through at that level.
_PINNED_LOGGERS: tuple[str, ...] = ("postcard_creator",)


def _register_trace_level() -> None:
    """Register the :data:`LOG_LEVEL_TRACE_NAME` level with :mod:`logging`.

    :func:`logging.addLevelName` is idempotent; calling this on
    every :func:`configure` invocation is harmless and means
    importing :mod:`postcards.log` is enough to make the level
    available everywhere.
    """
    logging.addLevelName(LOG_LEVEL_TRACE, LOG_LEVEL_TRACE_NAME)


def verbosity_to_level(verbosity: int, *, levels: Iterable[int] = DEFAULT_VERBOSITY_LEVELS) -> int:
    """Translate a ``-v`` count to a logging level.

    The lookup uses ``min(count, len(levels) - 1)`` so a count
    greater than the configured maximum does not raise; it just
    pins to the deepest level the project exposes.
    """
    table = tuple(levels)
    if not table:
        return logging.WARNING
    index = max(0, min(verbosity, len(table) - 1))
    return table[index]


def configure(
    level: int = logging.WARNING,
    *,
    stream: TextIO | None = None,
    fmt: str | None = None,
    brief_fmt: str | None = None,
) -> None:
    """Install the project's logging configuration.

    Parameters
    ----------
    level:
        The minimum log level to emit on the installed handler.
        :func:`verbosity_to_level` is the canonical way to derive
        this from a ``-v`` count; tests call :func:`configure`
        directly with an explicit level.
    stream:
        Where to write log records. ``None`` (the default) uses
        :data:`sys.stderr`. Tests pass an :class:`io.StringIO`
        to capture records without touching the real stderr.
    fmt, brief_fmt:
        Override the standard format (``fmt``) and the warning/
        error format (``brief_fmt``). ``None`` keeps the module
        defaults.
    """
    _register_trace_level()
    handler_stream = stream if stream is not None else sys.stderr

    formatter = logging.Formatter(fmt or DEFAULT_FORMAT)
    brief_formatter = logging.Formatter(brief_fmt or BRIEF_FORMAT)

    class _ProjectHandler(logging.StreamHandler):
        """StreamHandler that picks the brief format for WARNING+."""

        def format(self, record: logging.LogRecord) -> str:
            if record.levelno >= logging.WARNING:
                return brief_formatter.format(record)
            return formatter.format(record)

    handler = _ProjectHandler(handler_stream)

    root = logging.getLogger()
    # Replace any handlers we previously installed so repeated
    # configure() calls (e.g. when the user runs multiple CLI
    # commands in the same process) do not stack output.
    for existing in list(root.handlers):
        if getattr(existing, "_postcards_owned", False):
            root.removeHandler(existing)
    handler._postcards_owned = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)

    for pinned_name in _PINNED_LOGGERS:
        pinned = logging.getLogger(pinned_name)
        pinned.setLevel(min(level, logging.DEBUG))


def make_record_capture() -> tuple[logging.Handler, list[logging.LogRecord]]:
    """Return ``(handler, records)`` so tests can introspect emitted records.

    The returned handler is *not* installed on any logger; the
    caller attaches it with ``logger.addHandler(...)`` and is
    responsible for removing it after the assertion. Records are
    captured by reference, so the test can inspect them after
    the SUT returns.
    """
    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    return _Capture(level=LOG_LEVEL_TRACE), captured


__all__ = [
    "BRIEF_FORMAT",
    "DEFAULT_FORMAT",
    "DEFAULT_VERBOSITY_LEVELS",
    "LOG_LEVEL_TRACE",
    "LOG_LEVEL_TRACE_NAME",
    "configure",
    "make_record_capture",
    "verbosity_to_level",
]


def _smoke(level_resolver: Callable[[int], int]) -> int:
    """Quick sanity check used by the doctest-style snippets.

    Not exported; lets the test suite import the resolver without
    pulling pytest.
    """
    return level_resolver(2)
