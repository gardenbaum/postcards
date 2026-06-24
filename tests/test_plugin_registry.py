"""Tests for :mod:`postcards.plugins.registry`."""

from __future__ import annotations

import io
from typing import ClassVar

import pytest

from postcards.plugins import PluginResult, Registry
from postcards.plugins.errors import PluginError


class _AlphaPlugin:
    name: ClassVar[str] = "alpha"
    description: ClassVar[str] = "alpha plugin"

    def configure(self, payload: object) -> None:
        pass

    def render(self) -> PluginResult:
        return PluginResult(image=io.BytesIO(b"alpha"))


class _BetaPlugin:
    name: ClassVar[str] = "beta"
    description: ClassVar[str] = "beta plugin"

    def configure(self, payload: object) -> None:
        pass

    def render(self) -> PluginResult:
        return PluginResult(image=io.BytesIO(b"beta"))


# ---------------------------------------------------------------------------
# Registration / unregistration
# ---------------------------------------------------------------------------


def test_registry_starts_empty() -> None:
    reg = Registry()
    assert reg.names() == []
    assert reg.items() == []


def test_register_adds_plugin() -> None:
    reg = Registry()
    reg.register("alpha", _AlphaPlugin)
    assert "alpha" in reg.names()
    assert reg.get("alpha") is _AlphaPlugin


def test_register_replaces_existing_plugin() -> None:
    reg = Registry()
    reg.register("alpha", _AlphaPlugin)
    reg.register("alpha", _BetaPlugin)
    assert reg.get("alpha") is _BetaPlugin


def test_unregister_removes_plugin() -> None:
    reg = Registry()
    reg.register("alpha", _AlphaPlugin)
    reg.unregister("alpha")
    assert "alpha" not in reg.names()


def test_unregister_is_silent_when_absent() -> None:
    reg = Registry()
    reg.unregister("never-registered")  # must not raise


def test_clear_empties_registry() -> None:
    reg = Registry()
    reg.register("alpha", _AlphaPlugin)
    reg.register("beta", _BetaPlugin)
    reg.clear()
    assert reg.names() == []


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_register_rejects_empty_name() -> None:
    reg = Registry()
    with pytest.raises(ValueError, match="non-empty"):
        reg.register("", _AlphaPlugin)


def test_register_rejects_non_class() -> None:
    reg = Registry()
    with pytest.raises(ValueError, match="must be a class"):
        reg.register("alpha", _AlphaPlugin())  # type: ignore[arg-type]


def test_register_picks_up_class_level_description() -> None:
    reg = Registry()
    reg.register("alpha", _AlphaPlugin)
    assert reg.description_for("alpha") == "alpha plugin"


def test_register_explicit_description_overrides_class_default() -> None:
    reg = Registry()
    reg.register("alpha", _AlphaPlugin, description="override")
    assert reg.description_for("alpha") == "override"


def test_description_for_unknown_plugin_is_empty() -> None:
    reg = Registry()
    assert reg.description_for("nope") == ""


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def test_get_unknown_raises_plugin_error_listing_available() -> None:
    reg = Registry()
    reg.register("alpha", _AlphaPlugin)
    reg.register("beta", _BetaPlugin)
    with pytest.raises(PluginError) as exc_info:
        reg.get("gamma")
    msg = str(exc_info.value)
    assert "gamma" in msg
    assert "alpha" in msg
    assert "beta" in msg


def test_has_returns_true_when_registered() -> None:
    reg = Registry()
    reg.register("alpha", _AlphaPlugin)
    assert reg.has("alpha") is True
    assert reg.has("missing") is False


def test_names_is_sorted() -> None:
    reg = Registry()
    reg.register("zebra", _AlphaPlugin)
    reg.register("alpha", _AlphaPlugin)
    reg.register("middle", _AlphaPlugin)
    assert reg.names() == ["alpha", "middle", "zebra"]


def test_items_returns_name_class_pairs_sorted() -> None:
    reg = Registry()
    reg.register("alpha", _AlphaPlugin)
    reg.register("beta", _BetaPlugin)
    items = reg.items()
    assert items == [("alpha", _AlphaPlugin), ("beta", _BetaPlugin)]


# ---------------------------------------------------------------------------
# Default registry alias
# ---------------------------------------------------------------------------


def test_default_registry_is_a_registry_instance() -> None:
    from postcards.plugins.registry import Registry

    assert isinstance(Registry.default, Registry)
