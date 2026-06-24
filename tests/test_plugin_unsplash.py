"""Tests for the ``unsplash`` M3 plugin.

The Unsplash plugin makes two HTTP calls per :meth:`render`:

1. ``GET /photos/random`` to look up a picture URL.
2. ``GET <picture_url>`` to download the JPEG bytes.

Tests patch ``requests.get`` (where the plugin imports it from)
so no real network traffic is generated. The fake response
shape mirrors ``requests.Response`` for the bits the plugin
reads (``status_code``, ``content``, ``json()``).
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from postcards.plugins import PluginResult
from postcards.plugins.builtin.unsplash import ENV_VAR, UnsplashPlugin
from postcards.plugins.errors import PluginConfigError, PluginRenderError
from postcards.plugins.loader import load_plugin


class _FakeResponse:
    """Mimics the small slice of ``requests.Response`` the plugin uses."""

    def __init__(
        self,
        *,
        body: bytes = b"\xff\xd8\xff\xe0FAKE-UNSPLASH-JPEG",
        status_code: int = 200,
        json_body: object | None = None,
        text_body: str | None = None,
    ) -> None:
        self.content = body
        self.status_code = status_code
        self.headers: dict[str, str] = {}
        self._json = json_body
        self.text = text_body if text_body is not None else ""

    def json(self) -> object:
        if self._json is None:
            raise ValueError("no json body")
        return self._json

    def __repr__(self) -> str:  # pragma: no cover - debug only
        return f"_FakeResponse(status={self.status_code}, len={len(self.content)})"


@dataclass
class _FakeGet:
    """Stand-in for the ``requests.get`` callable.

    Holds the queued responses and a call log so tests can
    inspect both the request shape and the response sequence.
    The plugin issues two ``requests.get`` calls per render
    (API + download), so the queue should hold two responses
    by default.
    """

    responses: list[_FakeResponse] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.responses:
            # Default sequence: API returns one photo, download returns JPEG.
            self.responses.append(
                _FakeResponse(
                    json_body={
                        "id": "abc123",
                        "urls": {"regular": "https://images.unsplash.com/abc.jpg"},
                        "user": {"name": "Photographer"},
                    }
                )
            )
            self.responses.append(_FakeResponse())

    def __call__(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if not self.responses:
            raise AssertionError(f"unexpected requests.get call for {url!r}")
        return self.responses.pop(0)


@pytest.fixture
def fake_get(monkeypatch: pytest.MonkeyPatch) -> _FakeGet:
    """Patch ``requests.get`` to return a queued list of fake responses."""
    fake = _FakeGet()
    monkeypatch.setattr("postcards.plugins.builtin.unsplash.requests.get", fake)
    return fake


@pytest.fixture
def access_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """Set the ``POSTCARDS_UNSPLASH_ACCESS_KEY`` env var for one test."""
    key = "test-access-key"
    monkeypatch.setenv(ENV_VAR, key)
    return key


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------


def test_unsplash_plugin_accepts_empty_payload(access_key: str) -> None:
    """An empty payload is valid — every field is optional."""
    plugin = load_plugin("unsplash", {}, registry=None)
    assert plugin is not None


def test_unsplash_plugin_rejects_non_string_query() -> None:
    with pytest.raises(PluginConfigError, match="query"):
        load_plugin("unsplash", {"query": 7}, registry=None)


def test_unsplash_plugin_rejects_unknown_orientation() -> None:
    with pytest.raises(PluginConfigError, match="orientation"):
        load_plugin("unsplash", {"orientation": "diagonal"}, registry=None)


def test_unsplash_plugin_accepts_known_orientations() -> None:
    for ok in ("landscape", "portrait", "squarish"):
        load_plugin("unsplash", {"orientation": ok}, registry=None)


def test_unsplash_plugin_rejects_non_int_count() -> None:
    with pytest.raises(PluginConfigError, match="count"):
        load_plugin("unsplash", {"count": "5"}, registry=None)


def test_unsplash_plugin_rejects_bool_count() -> None:
    """``bool`` is a subclass of ``int`` — explicit guard required."""
    with pytest.raises(PluginConfigError, match="count"):
        load_plugin("unsplash", {"count": True}, registry=None)


def test_unsplash_plugin_rejects_zero_count() -> None:
    with pytest.raises(PluginConfigError, match="count"):
        load_plugin("unsplash", {"count": 0}, registry=None)


def test_unsplash_plugin_rejects_negative_count() -> None:
    with pytest.raises(PluginConfigError, match="count"):
        load_plugin("unsplash", {"count": -3}, registry=None)


def test_unsplash_plugin_rejects_count_above_cap() -> None:
    with pytest.raises(PluginConfigError, match="count"):
        load_plugin("unsplash", {"count": 31}, registry=None)


def test_unsplash_plugin_accepts_count_at_cap() -> None:
    """30 is the documented Unsplash hard cap — accepted at the boundary."""
    load_plugin("unsplash", {"count": 30}, registry=None)


def test_unsplash_plugin_rejects_non_string_message() -> None:
    with pytest.raises(PluginConfigError, match="message"):
        load_plugin("unsplash", {"message": 7}, registry=None)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_unsplash_plugin_renders_with_query(access_key: str, fake_get: _FakeGet) -> None:
    plugin = load_plugin("unsplash", {"query": "alps"}, registry=None)
    result = plugin.render()
    assert isinstance(result, PluginResult)
    assert result.image.read() == b"\xff\xd8\xff\xe0FAKE-UNSPLASH-JPEG"
    # The API call should carry the query as a query-string param.
    api_call = fake_get.calls[0]
    assert api_call["url"].endswith("/photos/random")
    assert api_call["params"]["query"] == "alps"
    assert api_call["headers"]["Authorization"] == "Client-ID test-access-key"


def test_unsplash_plugin_default_orientation_is_landscape(
    access_key: str, fake_get: _FakeGet
) -> None:
    plugin = load_plugin("unsplash", {}, registry=None)
    plugin.render()
    assert fake_get.calls[0]["params"]["orientation"] == "landscape"


def test_unsplash_plugin_respects_orientation_override(access_key: str, fake_get: _FakeGet) -> None:
    plugin = load_plugin("unsplash", {"orientation": "portrait"}, registry=None)
    plugin.render()
    assert fake_get.calls[0]["params"]["orientation"] == "portrait"


def test_unsplash_plugin_default_count_is_one(access_key: str, fake_get: _FakeGet) -> None:
    plugin = load_plugin("unsplash", {}, registry=None)
    plugin.render()
    assert fake_get.calls[0]["params"]["count"] == "1"


def test_unsplash_plugin_message_is_none_by_default(access_key: str, fake_get: _FakeGet) -> None:
    plugin = load_plugin("unsplash", {}, registry=None)
    result = plugin.render()
    assert result.message is None


def test_unsplash_plugin_forwards_message(access_key: str, fake_get: _FakeGet) -> None:
    plugin = load_plugin("unsplash", {"message": "greetings from zermatt"}, registry=None)
    result = plugin.render()
    assert result.message == "greetings from zermatt"


def test_unsplash_plugin_downloads_photo_url(access_key: str, fake_get: _FakeGet) -> None:
    plugin = load_plugin("unsplash", {}, registry=None)
    plugin.render()
    # The download call should hit the ``urls.regular`` from the API response.
    download = fake_get.calls[1]
    assert download["url"] == "https://images.unsplash.com/abc.jpg"


def test_unsplash_plugin_count_request_returns_multiple(
    access_key: str, monkeypatch: pytest.MonkeyPatch, fake_get: _FakeGet
) -> None:
    """When ``count > 1`` the API returns a list; one is picked at random."""
    # Replace the default single-photo response with a list of three.
    monkeypatch.setattr(
        fake_get,
        "responses",
        [
            _FakeResponse(
                json_body=[
                    {"id": "p1", "urls": {"regular": "https://x/1.jpg"}},
                    {"id": "p2", "urls": {"regular": "https://x/2.jpg"}},
                    {"id": "p3", "urls": {"regular": "https://x/3.jpg"}},
                ]
            ),
            _FakeResponse(),
            _FakeResponse(),
        ],
    )
    plugin = load_plugin("unsplash", {"count": 3}, registry=None)
    plugin.render()
    # The API was asked for count=3; the download hit one of the three URLs.
    api_call, download = fake_get.calls
    assert api_call["params"]["count"] == "3"
    assert download["url"] in {"https://x/1.jpg", "https://x/2.jpg", "https://x/3.jpg"}


def test_unsplash_plugin_api_passes_timeout(access_key: str, fake_get: _FakeGet) -> None:
    plugin = load_plugin("unsplash", {}, registry=None)
    plugin.render()
    assert fake_get.calls[0]["timeout"] == 30.0
    assert fake_get.calls[1]["timeout"] == 30.0


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_unsplash_plugin_missing_access_key(
    monkeypatch: pytest.MonkeyPatch, fake_get: _FakeGet
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    plugin = load_plugin("unsplash", {}, registry=None)
    with pytest.raises(PluginRenderError, match="POSTCARDS_UNSPLASH_ACCESS_KEY"):
        plugin.render()


def test_unsplash_plugin_empty_access_key(
    monkeypatch: pytest.MonkeyPatch, fake_get: _FakeGet
) -> None:
    monkeypatch.setenv(ENV_VAR, "   ")
    plugin = load_plugin("unsplash", {}, registry=None)
    with pytest.raises(PluginRenderError, match="POSTCARDS_UNSPLASH_ACCESS_KEY"):
        plugin.render()


def test_unsplash_plugin_unauthorized(access_key: str, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeGet(
        responses=[_FakeResponse(status_code=401, text_body="Unauthorized")],
    )
    monkeypatch.setattr("postcards.plugins.builtin.unsplash.requests.get", fake)

    plugin = load_plugin("unsplash", {}, registry=None)
    with pytest.raises(PluginRenderError, match="401"):
        plugin.render()


def test_unsplash_plugin_api_error_status(access_key: str, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeGet(responses=[_FakeResponse(status_code=500, text_body="server boom")])
    monkeypatch.setattr("postcards.plugins.builtin.unsplash.requests.get", fake)

    plugin = load_plugin("unsplash", {}, registry=None)
    with pytest.raises(PluginRenderError, match="500"):
        plugin.render()


def test_unsplash_plugin_network_error_on_api(
    access_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import requests

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise requests.ConnectionError("simulated")

    monkeypatch.setattr("postcards.plugins.builtin.unsplash.requests.get", _boom)
    plugin = load_plugin("unsplash", {}, registry=None)
    with pytest.raises(PluginRenderError, match="network error"):
        plugin.render()


def test_unsplash_plugin_invalid_json(access_key: str, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeGet(responses=[_FakeResponse(text_body="not json at all")])
    monkeypatch.setattr("postcards.plugins.builtin.unsplash.requests.get", fake)
    plugin = load_plugin("unsplash", {}, registry=None)
    with pytest.raises(PluginRenderError, match="not valid JSON"):
        plugin.render()


def test_unsplash_plugin_empty_photo_list(access_key: str, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeGet(responses=[_FakeResponse(json_body=[])])
    monkeypatch.setattr("postcards.plugins.builtin.unsplash.requests.get", fake)
    plugin = load_plugin("unsplash", {}, registry=None)
    with pytest.raises(PluginRenderError, match="no photos"):
        plugin.render()


def test_unsplash_plugin_missing_urls_block(
    access_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeGet(responses=[_FakeResponse(json_body={"id": "x", "user": "no urls here"})])
    monkeypatch.setattr("postcards.plugins.builtin.unsplash.requests.get", fake)
    plugin = load_plugin("unsplash", {}, registry=None)
    with pytest.raises(PluginRenderError, match="urls"):
        plugin.render()


def test_unsplash_plugin_missing_regular_url(
    access_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeGet(responses=[_FakeResponse(json_body={"urls": {"thumb": "x"}})])
    monkeypatch.setattr("postcards.plugins.builtin.unsplash.requests.get", fake)
    plugin = load_plugin("unsplash", {}, registry=None)
    with pytest.raises(PluginRenderError, match=r"urls\.regular"):
        plugin.render()


def test_unsplash_plugin_download_error(access_key: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # API succeeds, download returns 404.
    fake = _FakeGet(
        responses=[
            _FakeResponse(json_body={"urls": {"regular": "https://x/y.jpg"}}),
            _FakeResponse(status_code=404),
        ],
    )
    monkeypatch.setattr("postcards.plugins.builtin.unsplash.requests.get", fake)
    plugin = load_plugin("unsplash", {}, registry=None)
    with pytest.raises(PluginRenderError, match="404"):
        plugin.render()


def test_unsplash_plugin_empty_download_body(
    access_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeGet(
        responses=[
            _FakeResponse(json_body={"urls": {"regular": "https://x/y.jpg"}}),
            _FakeResponse(body=b""),
        ],
    )
    monkeypatch.setattr("postcards.plugins.builtin.unsplash.requests.get", fake)
    plugin = load_plugin("unsplash", {}, registry=None)
    with pytest.raises(PluginRenderError, match="empty response"):
        plugin.render()


# ---------------------------------------------------------------------------
# Plugin metadata
# ---------------------------------------------------------------------------


def test_unsplash_plugin_class_metadata() -> None:
    assert UnsplashPlugin.name == "unsplash"
    assert "unsplash" in UnsplashPlugin.description.lower()


def test_unsplash_plugin_is_registered_in_default_registry() -> None:
    from postcards.plugins.registry import Registry

    assert Registry.default.has("unsplash")
    assert Registry.default.get("unsplash") is UnsplashPlugin


def test_unsplash_plugin_env_var_constant() -> None:
    """``ENV_VAR`` is part of the plugin's documented contract."""
    assert ENV_VAR == "POSTCARDS_UNSPLASH_ACCESS_KEY"


def test_unsplash_plugin_result_is_pluginresult(access_key: str, fake_get: _FakeGet) -> None:
    plugin = load_plugin("unsplash", {}, registry=None)
    result = plugin.render()
    assert isinstance(result, PluginResult)


def test_unsplash_plugin_image_is_bytesio(access_key: str, fake_get: _FakeGet) -> None:
    plugin = load_plugin("unsplash", {}, registry=None)
    result = plugin.render()
    assert isinstance(result.image, io.BytesIO)


# ---------------------------------------------------------------------------
# JSON envelope edge cases
# ---------------------------------------------------------------------------


def test_unsplash_plugin_handles_json_dict_not_list(
    access_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``count=1`` Unsplash returns a single object, not a list."""
    fake = _FakeGet(
        responses=[
            _FakeResponse(json_body={"id": "abc", "urls": {"regular": "https://x/y.jpg"}}),
            _FakeResponse(),
        ],
    )
    monkeypatch.setattr("postcards.plugins.builtin.unsplash.requests.get", fake)
    plugin = load_plugin("unsplash", {}, registry=None)
    result = plugin.render()
    assert result.image.read() == b"\xff\xd8\xff\xe0FAKE-UNSPLASH-JPEG"


# Silence unused-import warning for ``json`` (kept for symmetry with
# other plugin test modules).
_ = json
