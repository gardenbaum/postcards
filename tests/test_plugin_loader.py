"""Tests for :mod:`postcards.plugins.loader`."""

from __future__ import annotations

import io
from typing import Any, ClassVar, cast

import pytest

from postcards.plugins import PluginResult, Registry
from postcards.plugins.errors import PluginConfigError, PluginNotFoundError
from postcards.plugins.loader import load_plugin


class _RecorderPlugin:
    """A plugin that records the payload it was configured with."""

    name: ClassVar[str] = "recorder"
    description: ClassVar[str] = "records its payload"

    def __init__(self) -> None:
        self.configured_with: dict[str, Any] | None = None

    def configure(self, payload: Any) -> None:
        # Stash the payload verbatim for the test to inspect.
        self.configured_with = dict(payload) if payload else {}

    def render(self) -> PluginResult:
        return PluginResult(image=io.BytesIO(b"recorder"))


class _StrictPlugin:
    """A plugin that validates its payload in ``configure``."""

    name: ClassVar[str] = "strict"
    description: ClassVar[str] = "requires 'keyword'"

    def configure(self, payload: dict[str, Any]) -> None:
        if "keyword" not in payload:
            raise PluginConfigError("strict", "missing 'keyword' in payload")

    def render(self) -> PluginResult:
        return PluginResult(image=io.BytesIO(b"strict"))


class _MisbehavingPlugin:
    """A plugin whose ``configure`` raises an unrelated exception."""

    name: ClassVar[str] = "bad"
    description: ClassVar[str] = "raises ValueError"

    def configure(self, payload: dict[str, Any]) -> None:
        raise ValueError("boom")

    def render(self) -> PluginResult:
        return PluginResult(image=io.BytesIO(b"bad"))


@pytest.fixture
def registry() -> Registry:
    reg = Registry()
    reg.register("recorder", _RecorderPlugin)
    reg.register("strict", _StrictPlugin)
    reg.register("bad", _MisbehavingPlugin)
    return reg


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_load_plugin_returns_configured_instance(registry: Registry) -> None:
    plugin = load_plugin("recorder", {"folder": "/tmp/pics"}, registry=registry)
    recorder = cast(_RecorderPlugin, plugin)
    assert isinstance(recorder, _RecorderPlugin)
    assert recorder.configured_with == {"folder": "/tmp/pics"}


def test_load_plugin_with_none_payload_uses_empty_dict(registry: Registry) -> None:
    plugin = load_plugin("recorder", None, registry=registry)
    recorder = cast(_RecorderPlugin, plugin)
    assert recorder.configured_with == {}


def test_load_plugin_without_payload_arg_uses_empty_dict(registry: Registry) -> None:
    plugin = load_plugin("recorder", registry=registry)
    recorder = cast(_RecorderPlugin, plugin)
    assert recorder.configured_with == {}


def test_load_plugin_runs_configure_before_return(registry: Registry) -> None:
    """The returned plugin must already be configured — render should work."""
    plugin = load_plugin("recorder", {"x": 1}, registry=registry)
    result = plugin.render()
    assert result.image.read() == b"recorder"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_load_plugin_unknown_raises_not_found(registry: Registry) -> None:
    with pytest.raises(PluginNotFoundError) as exc_info:
        load_plugin("nope", {}, registry=registry)
    assert "nope" in str(exc_info.value)


def test_load_plugin_empty_name_raises_not_found(registry: Registry) -> None:
    with pytest.raises(PluginNotFoundError):
        load_plugin("", {}, registry=registry)


def test_load_plugin_propagates_plugin_config_error(registry: Registry) -> None:
    with pytest.raises(PluginConfigError, match="missing 'keyword'"):
        load_plugin("strict", {}, registry=registry)


def test_load_plugin_wraps_unrelated_configure_exception(registry: Registry) -> None:
    with pytest.raises(PluginConfigError) as exc_info:
        load_plugin("bad", {}, registry=registry)
    assert exc_info.value.plugin_name == "bad"
    assert "boom" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, ValueError)
