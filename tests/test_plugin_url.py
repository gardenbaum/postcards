"""Tests for the ``url`` M3 plugin.

The URL plugin issues a single ``requests.get`` per
:meth:`render` call. Tests build a tiny in-memory fake
response object and patch ``requests.get`` (where the plugin
imports it from) so no real network traffic is generated.

The fake response shape mirrors ``requests.Response`` for
the bits the plugin reads (``status_code``, ``content``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from postcards.plugins import PluginResult
from postcards.plugins.builtin.url import UrlPlugin
from postcards.plugins.errors import PluginConfigError, PluginRenderError
from postcards.plugins.loader import load_plugin


class _FakeResponse:
    """Mimics the small slice of ``requests.Response`` the plugin uses."""

    def __init__(self, body: bytes, status_code: int = 200) -> None:
        self.content = body
        self.status_code = status_code
        self.headers: dict[str, str] = {}

    def __repr__(self) -> str:  # pragma: no cover - debug only
        return f"_FakeResponse(status={self.status_code}, len={len(self.content)})"


@dataclass
class _FakeGet:
    """Stand-in for the ``requests.get`` callable.

    Holds the queued responses and a call log so tests can
    inspect both the request shape and the response sequence.
    """

    responses: list[_FakeResponse] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.responses:
            self.responses.append(_FakeResponse(b"\xff\xd8\xff\xe0FAKE-JPEG-BYTES"))

    def __call__(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if not self.responses:
            raise AssertionError(f"unexpected requests.get call for {url!r}")
        return self.responses.pop(0)


@pytest.fixture
def fake_get(monkeypatch: pytest.MonkeyPatch) -> _FakeGet:
    """Patch ``requests.get`` to return a queued list of fake responses."""
    fake = _FakeGet()
    monkeypatch.setattr("postcards.plugins.builtin.url.requests.get", fake)
    return fake


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------


def test_url_plugin_requires_url_in_payload() -> None:
    with pytest.raises(PluginConfigError, match="url"):
        load_plugin("url", {}, registry=None)


def test_url_plugin_rejects_non_string_url() -> None:
    with pytest.raises(PluginConfigError):
        load_plugin("url", {"url": 42}, registry=None)


def test_url_plugin_rejects_non_http_scheme() -> None:
    for bad in ("ftp://example.com/img.jpg", "file:///etc/passwd", "data:image/png;base64,AAAA"):
        with pytest.raises(PluginConfigError, match="scheme"):
            load_plugin("url", {"url": bad}, registry=None)


def test_url_plugin_accepts_http_and_https() -> None:
    for good in ("http://example.com/img.jpg", "https://example.com/img.jpg"):
        # Should not raise.
        load_plugin("url", {"url": good}, registry=None)


def test_url_plugin_rejects_non_string_message() -> None:
    with pytest.raises(PluginConfigError, match="message"):
        load_plugin("url", {"url": "https://example.com/x.jpg", "message": 7}, registry=None)


def test_url_plugin_rejects_non_mapping_headers() -> None:
    with pytest.raises(PluginConfigError, match="headers"):
        load_plugin(
            "url",
            {"url": "https://example.com/x.jpg", "headers": "Authorization"},
            registry=None,
        )


def test_url_plugin_rejects_non_string_header_values() -> None:
    with pytest.raises(PluginConfigError, match="strings"):
        load_plugin(
            "url",
            {"url": "https://example.com/x.jpg", "headers": {"X-Trace": 1234}},
            registry=None,
        )


def test_url_plugin_rejects_non_positive_timeout() -> None:
    with pytest.raises(PluginConfigError, match="timeout"):
        load_plugin("url", {"url": "https://example.com/x.jpg", "timeout": 0}, registry=None)


def test_url_plugin_rejects_non_numeric_timeout() -> None:
    with pytest.raises(PluginConfigError, match="timeout"):
        load_plugin("url", {"url": "https://example.com/x.jpg", "timeout": "30"}, registry=None)


def test_url_plugin_rejects_bool_timeout() -> None:
    """``bool`` is a subclass of ``int`` — explicit guard required."""
    with pytest.raises(PluginConfigError, match="timeout"):
        load_plugin("url", {"url": "https://example.com/x.jpg", "timeout": True}, registry=None)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_url_plugin_fetches_and_returns_bytes(fake_get: _FakeGet) -> None:
    plugin = load_plugin("url", {"url": "https://example.com/pic.jpg"}, registry=None)
    result = plugin.render()
    assert isinstance(result, PluginResult)
    assert result.image.read() == b"\xff\xd8\xff\xe0FAKE-JPEG-BYTES"
    assert result.message is None
    assert fake_get.calls[0]["url"] == "https://example.com/pic.jpg"


def test_url_plugin_forwards_custom_message(fake_get: _FakeGet) -> None:
    plugin = load_plugin(
        "url",
        {"url": "https://example.com/pic.jpg", "message": "hi from zurich"},
        registry=None,
    )
    result = plugin.render()
    assert result.message == "hi from zurich"


def test_url_plugin_forwards_headers(fake_get: _FakeGet) -> None:
    """Custom headers should land in the ``requests.get`` call."""
    headers = {"Authorization": "Bearer secret-token", "User-Agent": "postcards-test/1.0"}
    plugin = load_plugin(
        "url",
        {"url": "https://example.com/pic.jpg", "headers": headers},
        registry=None,
    )
    plugin.render()
    assert fake_get.calls[0]["headers"] == headers


def test_url_plugin_uses_custom_timeout(fake_get: _FakeGet) -> None:
    plugin = load_plugin(
        "url",
        {"url": "https://example.com/pic.jpg", "timeout": 5},
        registry=None,
    )
    result = plugin.render()
    assert result.image.read()
    assert fake_get.calls[0]["timeout"] == 5


def test_url_plugin_default_timeout_when_unset(fake_get: _FakeGet) -> None:
    plugin = load_plugin("url", {"url": "https://example.com/pic.jpg"}, registry=None)
    plugin.render()
    assert fake_get.calls[0]["timeout"] == 30.0


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_url_plugin_network_error_becomes_render_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_args: Any, **_kwargs: Any) -> None:
        import requests

        raise requests.ConnectionError("simulated network failure")

    monkeypatch.setattr("postcards.plugins.builtin.url.requests.get", _boom)

    plugin = load_plugin("url", {"url": "https://example.com/pic.jpg"}, registry=None)
    with pytest.raises(PluginRenderError, match="network error"):
        plugin.render()


def test_url_plugin_http_error_becomes_render_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _not_found(url: str, **_kwargs: Any) -> _FakeResponse:
        return _FakeResponse(b"", status_code=404)

    monkeypatch.setattr("postcards.plugins.builtin.url.requests.get", _not_found)

    plugin = load_plugin("url", {"url": "https://example.com/missing.jpg"}, registry=None)
    with pytest.raises(PluginRenderError, match="404"):
        plugin.render()


def test_url_plugin_empty_body_becomes_render_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _empty(url: str, **_kwargs: Any) -> _FakeResponse:
        return _FakeResponse(b"", status_code=200)

    monkeypatch.setattr("postcards.plugins.builtin.url.requests.get", _empty)

    plugin = load_plugin("url", {"url": "https://example.com/empty.jpg"}, registry=None)
    with pytest.raises(PluginRenderError, match="empty response"):
        plugin.render()


# ---------------------------------------------------------------------------
# Plugin metadata
# ---------------------------------------------------------------------------


def test_url_plugin_class_metadata() -> None:
    assert UrlPlugin.name == "url"
    assert "url" in UrlPlugin.description.lower()


def test_url_plugin_is_registered_in_default_registry() -> None:
    from postcards.plugins.registry import Registry

    assert Registry.default.has("url")
    assert Registry.default.get("url") is UrlPlugin


def test_url_plugin_appears_in_plugins_list(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The ``url`` plugin should be discoverable via ``plugins list``."""
    import sys

    from postcards.cli import main as entry_main

    monkeypatch.setattr(sys, "argv", ["postcards", "plugins", "list"])
    try:
        entry_main.main()
    except SystemExit as exc:
        assert exc.code == 0, f"unexpected SystemExit code: {exc.code!r}"
    captured = capsys.readouterr()
    assert "url" in captured.out
