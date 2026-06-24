"""Tests for :mod:`postcards.plugins.base` and :mod:`postcards.plugins.base_impl`."""

from __future__ import annotations

import io
from typing import Any, ClassVar

import pytest

from postcards.plugins import Plugin, PluginResult
from postcards.plugins.base_impl import PluginBase, is_plugin

# ---------------------------------------------------------------------------
# PluginResult
# ---------------------------------------------------------------------------


def test_plugin_result_requires_image() -> None:
    result = PluginResult(image=io.BytesIO(b"jpeg-bytes"))
    assert result.image.read() == b"jpeg-bytes"
    assert result.message is None
    assert result.metadata == {}


def test_plugin_result_optional_message_and_metadata() -> None:
    result = PluginResult(
        image=io.BytesIO(b"\xff\xd8\xff"),
        message="hi from zurich",
        metadata={"source": "pexels", "keyword": "alps"},
    )
    assert result.message == "hi from zurich"
    assert result.metadata["source"] == "pexels"


def test_plugin_result_is_frozen() -> None:
    result = PluginResult(image=io.BytesIO(b"x"))
    with pytest.raises((AttributeError, Exception)):
        result.message = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# PluginBase
# ---------------------------------------------------------------------------


class _SamplePlugin(PluginBase):
    name: ClassVar[str] = "sample"
    description: ClassVar[str] = "a sample plugin for tests"

    def render(self) -> PluginResult:
        return PluginResult(image=io.BytesIO(b"sample"))


def test_plugin_base_stores_payload_via_configure() -> None:
    plugin = _SamplePlugin()
    plugin.configure({"folder": "/tmp/pics", "move": True})
    assert plugin.payload == {"folder": "/tmp/pics", "move": True}


def test_plugin_base_payload_is_isolated_from_caller() -> None:
    """Plugins must not mutate the user's config dict."""
    plugin = _SamplePlugin()
    original: dict[str, Any] = {"folder": "/tmp/pics"}
    plugin.configure(original)
    original["folder"] = "/tmp/MUTATED"
    # The plugin still sees the value it was configured with.
    assert plugin.payload["folder"] == "/tmp/pics"


def test_plugin_base_logger_scoped_to_name() -> None:
    plugin = _SamplePlugin()
    assert plugin.logger.name == "postcards.plugins.sample"


def test_plugin_base_cli_help_defaults_to_description() -> None:
    assert _SamplePlugin().cli_help() == "a sample plugin for tests"


def test_plugin_base_render_is_abstract_for_subclass_without_render() -> None:
    class _NoRender(PluginBase):
        name: ClassVar[str] = "no-render"

    with pytest.raises(NotImplementedError, match=r"must implement Plugin\.render"):
        _NoRender().render()


def test_plugin_base_subclass_requires_name() -> None:
    with pytest.raises(TypeError, match=r"must define a non-empty 'name'"):

        class _NoName(PluginBase):
            description: ClassVar[str] = "missing name"

            def render(self) -> PluginResult:  # pragma: no cover - never reached
                return PluginResult(image=io.BytesIO(b"x"))


def test_plugin_base_is_plugin() -> None:
    """``is_plugin`` returns True for PluginBase subclasses."""
    assert is_plugin(_SamplePlugin())


def test_plugin_protocol_isinstance_check() -> None:
    """The Protocol's @runtime_checkable works for plain objects."""
    assert isinstance(_SamplePlugin(), Plugin)


def test_render_returns_plugin_result() -> None:
    result = _SamplePlugin().render()
    assert isinstance(result, PluginResult)
    assert result.image.read() == b"sample"
