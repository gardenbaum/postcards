"""Tests for the ``pexels`` M3 plugin.

The pexels plugin fetches a random picture from picsum.photos via
``urllib``. Tests patch ``urllib.request.urlopen`` to return a
fake binary response so no real network traffic is generated.
"""

from __future__ import annotations

import io
from typing import Any

import pytest

from postcards.plugins import PluginResult
from postcards.plugins.builtin.pexels import PexelsPlugin
from postcards.plugins.errors import PluginConfigError, PluginRenderError
from postcards.plugins.loader import load_plugin


class _FakeResponse:
    """Drop-in replacement for ``urllib.request.urlopen`` returns."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self, *_args: Any, **_kwargs: Any) -> bytes:
        return self._payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: Any) -> None:
        pass


@pytest.fixture
def fake_urlopen(monkeypatch: pytest.MonkeyPatch) -> bytes:
    """Patch ``urllib.request.urlopen`` to return a canned response.

    Returns the canned bytes so tests can assert the plugin
    forwarded them verbatim.
    """
    payload = b"\xff\xd8\xff\xe0FAKE-JPEG-BYTES"
    calls: list[str] = []

    def _fake_urlopen(request: Any, *_args: Any, **_kwargs: Any) -> _FakeResponse:
        # ``request`` may be a Request object or a URL string
        # depending on the caller. Pull the URL out for assertion.
        url = getattr(request, "full_url", None) or request
        calls.append(url)
        return _FakeResponse(payload)

    monkeypatch.setattr("postcards.plugin_pexels.util.pexels.urllib.request.urlopen", _fake_urlopen)
    return payload


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------


def test_pexels_plugin_accepts_empty_payload() -> None:
    """An empty payload is valid — both fields are optional."""
    plugin = load_plugin("pexels", {}, registry=None)
    assert plugin is not None


def test_pexels_plugin_rejects_non_string_keyword() -> None:
    with pytest.raises(PluginConfigError, match="keyword"):
        load_plugin("pexels", {"keyword": 42}, registry=None)


def test_pexels_plugin_rejects_non_bool_safe_search() -> None:
    with pytest.raises(PluginConfigError, match="safe_search"):
        load_plugin("pexels", {"safe_search": "yes"}, registry=None)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_pexels_plugin_fetches_url(fake_urlopen: bytes) -> None:
    plugin = load_plugin("pexels", {}, registry=None)
    result = plugin.render()
    assert isinstance(result, PluginResult)
    assert result.image.read() == fake_urlplayload_if_urlopen_mocked(fake_urlopen)


def test_pexels_plugin_message_is_none(fake_urlopen: bytes) -> None:
    plugin = load_plugin("pexels", {}, registry=None)
    result = plugin.render()
    assert result.message is None


def test_pexels_plugin_forwards_keyword_to_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A keyword should change the picsum seed."""
    seen_urls: list[str] = []

    def _fake_urlopen(request: Any, *_args: Any, **_kwargs: Any) -> _FakeResponse:
        url = getattr(request, "full_url", None) or request
        seen_urls.append(url)
        return _FakeResponse(b"jpeg-bytes")

    monkeypatch.setattr("postcards.plugin_pexels.util.pexels.urllib.request.urlopen", _fake_urlopen)

    plugin_no_kw = load_plugin("pexels", {}, registry=None)
    plugin_no_kw.render()

    plugin_alps = load_plugin("pexels", {"keyword": "alps"}, registry=None)
    plugin_alps.render()

    plugin_cats = load_plugin("pexels", {"keyword": "cats"}, registry=None)
    plugin_cats.render()

    assert len(seen_urls) == 3
    # Different keywords should produce different URLs (different seeds).
    assert seen_urls[0] != seen_urls[1]
    assert seen_urls[1] != seen_urls[2]


def fake_urlplayload_if_urlopen_mocked(payload: bytes) -> bytes:
    return payload  # pragma: no cover - kept for symmetry with the assertion above


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_pexels_plugin_network_error_becomes_render_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _failing_urlopen(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("simulated network failure")

    monkeypatch.setattr(
        "postcards.plugin_pexels.util.pexels.urllib.request.urlopen", _failing_urlopen
    )

    plugin = load_plugin("pexels", {}, registry=None)
    with pytest.raises(PluginRenderError, match="cannot read"):
        plugin.render()


# ---------------------------------------------------------------------------
# Plugin metadata
# ---------------------------------------------------------------------------


def test_pexels_plugin_class_metadata() -> None:
    assert PexelsPlugin.name == "pexels"
    assert (
        "picsum" in PexelsPlugin.description.lower() or "pexels" in PexelsPlugin.description.lower()
    )


def test_pexels_plugin_is_registered_in_default_registry() -> None:
    from postcards.plugins.registry import Registry

    assert Registry.default.has("pexels")
    assert Registry.default.get("pexels") is PexelsPlugin


# Quiet the unused-import warning for ``io`` (kept for symmetry with
# other plugin test modules).
_ = io
