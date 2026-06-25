"""Unit tests for :mod:`postcards.log`.

The module is the M5 source of truth for the project's log
format, the TRACE custom level, and the ``-v`` count mapping.
The tests cover the helper functions directly and pin the
public surface so a future refactor that breaks the contract
fails loudly.
"""

from __future__ import annotations

import io
import logging

import pytest

from postcards.log import (
    LOG_LEVEL_TRACE,
    LOG_LEVEL_TRACE_NAME,
    configure,
    make_record_capture,
    verbosity_to_level,
)

# ---------------------------------------------------------------------------
# Verbosity mapping
# ---------------------------------------------------------------------------


class TestVerbosityMapping:
    @pytest.mark.parametrize(
        ("count", "expected"),
        [
            (0, logging.WARNING),
            (1, logging.INFO),
            (2, logging.DEBUG),
            (3, LOG_LEVEL_TRACE),
            (4, LOG_LEVEL_TRACE),  # clamps to the deepest level
        ],
    )
    def test_default_mapping(self, count: int, expected: int) -> None:
        assert verbosity_to_level(count) == expected

    def test_negative_count_clamped_to_zero(self) -> None:
        assert verbosity_to_level(-5) == logging.WARNING

    def test_custom_levels_override(self) -> None:
        levels = (logging.ERROR, logging.WARNING, logging.INFO)
        # The default levels are 4 long; ``levels`` is 3 long
        # so ``-v`` should map to the third entry (INFO).
        assert verbosity_to_level(2, levels=levels) == logging.INFO

    def test_empty_levels_falls_back_to_warning(self) -> None:
        assert verbosity_to_level(2, levels=()) == logging.WARNING


# ---------------------------------------------------------------------------
# Trace level
# ---------------------------------------------------------------------------


class TestTraceLevel:
    def test_trace_level_constant_is_five(self) -> None:
        # Numeric level must remain 5 so ``-vvv`` consistently
        # emits below DEBUG (10).
        assert LOG_LEVEL_TRACE == 5
        assert LOG_LEVEL_TRACE < logging.DEBUG

    def test_trace_level_name_registered(self) -> None:
        # ``logging.getLevelName`` is the canonical way to
        # confirm the registration took effect.
        assert logging.getLevelName(LOG_LEVEL_TRACE) == LOG_LEVEL_TRACE_NAME


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestConfigure:
    def test_installs_stream_handler_on_root(self) -> None:
        configure(level=logging.DEBUG)
        root = logging.getLogger()
        # At least one handler tagged as ours must be installed.
        assert any(getattr(h, "_postcards_owned", False) for h in root.handlers)
        assert root.level == logging.DEBUG

    def test_repeated_configure_does_not_stack_handlers(self) -> None:
        configure(level=logging.INFO)
        configure(level=logging.INFO)
        owned = [h for h in logging.getLogger().handlers if getattr(h, "_postcards_owned", False)]
        assert len(owned) == 1

    def test_writes_records_to_stream(self) -> None:
        stream = io.StringIO()
        configure(level=logging.INFO, stream=stream)
        logging.getLogger("postcards.test").info("hello world")
        output = stream.getvalue()
        assert "hello world" in output
        assert "postcards.test" in output

    def test_warning_uses_brief_format(self) -> None:
        stream = io.StringIO()
        configure(level=logging.INFO, stream=stream)
        logging.getLogger("postcards.test").warning("careful")
        output = stream.getvalue()
        # Brief format drops the levelname; default includes it.
        assert "WARNING" not in output
        assert "careful" in output

    def test_info_uses_default_format(self) -> None:
        stream = io.StringIO()
        configure(level=logging.INFO, stream=stream)
        logging.getLogger("postcards.test").info("normal")
        output = stream.getvalue()
        assert "INFO" in output
        assert "normal" in output

    def test_pinned_loggers_unaffected_by_root(self) -> None:
        configure(level=logging.INFO)
        # ``postcard_creator`` is pinned in the module — when
        # the root logger is at INFO, the pin logic doesn't
        # raise the pinned logger above the root level.
        api_logger = logging.getLogger("postcard_creator")
        # The pinned logger's effective level must not exceed
        # DEBUG (the pin's ceiling).
        assert api_logger.level <= logging.DEBUG

    def test_formats_overrides(self) -> None:
        stream = io.StringIO()
        configure(
            level=logging.INFO,
            stream=stream,
            fmt="FMT %(message)s",
            brief_fmt="BRIEF %(message)s",
        )
        logging.getLogger("postcards.test").info("hi")
        assert "FMT hi" in stream.getvalue()
        stream.truncate(0)
        stream.seek(0)
        logging.getLogger("postcards.test").warning("oops")
        assert "BRIEF oops" in stream.getvalue()


# ---------------------------------------------------------------------------
# Capture helper
# ---------------------------------------------------------------------------


class TestRecordCapture:
    def test_capture_returns_handler_and_records(self) -> None:
        handler, records = make_record_capture()
        assert isinstance(handler, logging.Handler)
        assert records == []

    def test_captures_emitted_records_when_attached(self) -> None:
        handler, records = make_record_capture()
        logger = logging.getLogger("postcards.test.capture")
        logger.setLevel(LOG_LEVEL_TRACE)
        previous_level = logger.level
        try:
            logger.addHandler(handler)
            logger.debug("dbg message")
            logger.info("info message")
            assert len(records) == 2
            assert records[0].levelno == logging.DEBUG
            assert records[1].levelno == logging.INFO
            assert records[0].getMessage() == "dbg message"
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous_level)
