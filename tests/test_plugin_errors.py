"""Tests for :mod:`postcards.plugins.errors`."""

from __future__ import annotations

from postcards.plugins.errors import (
    PluginConfigError,
    PluginError,
    PluginNotFoundError,
    PluginRenderError,
)


def test_plugin_error_is_runtime_error() -> None:
    assert issubclass(PluginError, RuntimeError)


def test_plugin_not_found_error_message_includes_name() -> None:
    err = PluginNotFoundError("missing")
    assert "missing" in str(err)
    assert err.name == "missing"


def test_plugin_config_error_message_includes_plugin_name() -> None:
    err = PluginConfigError("pexels", "missing 'keyword'")
    assert "pexels" in str(err)
    assert "missing 'keyword'" in str(err)
    assert err.plugin_name == "pexels"
    assert err.message == "missing 'keyword'"


def test_plugin_render_error_message_includes_plugin_name() -> None:
    err = PluginRenderError("folder", "no images in /tmp/empty")
    assert "folder" in str(err)
    assert "no images" in str(err)


def test_plugin_subclasses_catchable_as_base() -> None:
    """``except PluginError`` should catch every subclass."""
    cases: list[PluginError] = [
        PluginNotFoundError("x"),
        PluginConfigError("x", "y"),
        PluginRenderError("x", "y"),
    ]
    for exc in cases:
        try:
            raise exc
        except PluginError as caught:
            assert caught is exc
