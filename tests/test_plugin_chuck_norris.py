"""Tests for the ``chuck_norris`` M3 plugin.

The plugin picks a random joke from a bundled JSON file and
fetches a matching picture from picsum.photos via the legacy
pexels helper. Tests patch both the URL opener (to avoid real
network traffic) and the joke-loading helper (to drive specific
fixture data).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from postcards.plugins.builtin.chuck_norris import ChuckNorrisPlugin, _extract_keywords
from postcards.plugins.errors import PluginConfigError, PluginRenderError
from postcards.plugins.loader import load_plugin

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self, *_args: Any, **_kwargs: Any) -> bytes:
        return self._payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: Any) -> None:
        pass


SAMPLE_JOKES: list[dict[str, Any]] = [
    {"id": 1, "joke": "Chuck Norris counted to infinity. Twice.", "categories": []},
    {"id": 2, "joke": "Chuck Norris can divide by zero.", "categories": ["nerdy"]},
    {"id": 3, "joke": "Explicit joke about Chuck Norris.", "categories": ["explicit"]},
    {
        "id": 4,
        "joke": "When Chuck Norris does a push-up, he isn't lifting himself up, "
        "he's pushing the Earth down.",
        "categories": [],
    },
]


@pytest.fixture
def fake_jokes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Write a fake jokes JSON file and point the plugin at it."""
    import json

    jokes_file = tmp_path / "jokes.json"
    jokes_file.write_text(json.dumps({"type": "success", "value": SAMPLE_JOKES}), encoding="utf-8")
    monkeypatch.setattr("postcards.plugins.builtin.chuck_norris._JOKES_PATH", str(jokes_file))
    return jokes_file


@pytest.fixture
def fake_urlopen(monkeypatch: pytest.MonkeyPatch) -> bytes:
    payload = b"FAKE-CHUCK-PICTURE"

    def _fake_urlopen(request: Any, *_args: Any, **_kwargs: Any) -> _FakeResponse:
        return _FakeResponse(payload)

    monkeypatch.setattr("postcards.plugin_pexels.util.pexels.urllib.request.urlopen", _fake_urlopen)
    return payload


# ---------------------------------------------------------------------------
# Keyword extractor (unit)
# ---------------------------------------------------------------------------


def test_extract_keywords_drops_stopwords() -> None:
    keywords = _extract_keywords("the and is it to for", limit=3)
    # All stopwords; result is empty.
    assert keywords == []


def test_extract_keywords_keeps_content_words() -> None:
    keywords = _extract_keywords("the cat sat on a mat", limit=3)
    # ``the`` and ``a`` are stopwords; ``cat``, ``sat``, ``mat`` survive.
    assert keywords == ["cat", "sat", "mat"]


def test_extract_keywords_keeps_capitalized() -> None:
    keywords = _extract_keywords("Chuck Norris ate the cake", limit=3)
    # ``Chuck`` and ``Norris`` are stopwords; ``ate`` and ``cake`` survive.
    assert "ate" in keywords
    assert "cake" in keywords


def test_extract_keywords_limit_is_respected() -> None:
    keywords = _extract_keywords("alpha beta gamma delta epsilon zeta", limit=2)
    assert len(keywords) == 2


def test_extract_keywords_drops_duplicates_case_insensitively() -> None:
    keywords = _extract_keywords("Alpha alpha ALPHA", limit=3)
    assert len(keywords) == 1
    assert keywords[0].lower() == "alpha"


def test_extract_keywords_empty_input() -> None:
    assert _extract_keywords("", limit=3) == []


def test_extract_keywords_only_punctuation() -> None:
    assert _extract_keywords("!!! ???", limit=3) == []


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------


def test_chuck_norris_plugin_accepts_empty_payload() -> None:
    plugin = load_plugin("chuck_norris", {}, registry=None)
    assert plugin is not None


def test_chuck_norris_plugin_rejects_non_string_category() -> None:
    with pytest.raises(PluginConfigError, match="category"):
        load_plugin("chuck_norris", {"category": 42}, registry=None)


def test_chuck_norris_plugin_rejects_non_string_duplicate_file() -> None:
    with pytest.raises(PluginConfigError, match="duplicate_file"):
        load_plugin("chuck_norris", {"duplicate_file": 42}, registry=None)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_chuck_norris_plugin_picks_random_joke(fake_jokes: Path, fake_urlopen: bytes) -> None:
    plugin = load_plugin("chuck_norris", {}, registry=None)
    result = plugin.render()
    assert result.message in {j["joke"] for j in SAMPLE_JOKES}
    assert result.image.read() == fake_urlopen


def test_chuck_norris_plugin_respects_category_filter(
    fake_jokes: Path, fake_urlopen: bytes
) -> None:
    plugin = load_plugin("chuck_norris", {"category": "nerdy"}, registry=None)
    for _ in range(10):
        result = plugin.render()
        assert result.message == "Chuck Norris can divide by zero."


def test_chuck_norris_plugin_no_jokes_for_category_raises(
    fake_jokes: Path, fake_urlopen: bytes
) -> None:
    plugin = load_plugin("chuck_norris", {"category": "nonexistent"}, registry=None)
    with pytest.raises(PluginRenderError, match="no jokes"):
        plugin.render()


def test_chuck_norris_plugin_respects_duplicate_file(
    fake_jokes: Path, fake_urlopen: bytes, tmp_path: Path
) -> None:
    dup = tmp_path / "seen.txt"
    dup.write_text("1\n2\n3\n", encoding="utf-8")
    plugin = load_plugin("chuck_norris", {"duplicate_file": str(dup)}, registry=None)
    # Only joke id=4 should be available on the first call.
    # (Subsequent calls would fail because the picked joke id is
    # appended to the dup file — the plugin does not support
    # repeating the same joke by design.)
    result = plugin.render()
    assert result.message == SAMPLE_JOKES[3]["joke"]


def test_chuck_norris_plugin_appends_to_duplicate_file(
    fake_jokes: Path, fake_urlopen: bytes, tmp_path: Path
) -> None:
    dup = tmp_path / "seen.txt"
    plugin = load_plugin("chuck_norris", {"duplicate_file": str(dup)}, registry=None)
    plugin.render()
    content = dup.read_text(encoding="utf-8").strip()
    assert content in {"1", "2", "3", "4"}


def test_chuck_norris_plugin_all_excluded_raises(
    fake_jokes: Path, fake_urlopen: bytes, tmp_path: Path
) -> None:
    dup = tmp_path / "seen.txt"
    dup.write_text("1\n2\n3\n4\n", encoding="utf-8")
    plugin = load_plugin("chuck_norris", {"duplicate_file": str(dup)}, registry=None)
    with pytest.raises(PluginRenderError, match="no more jokes"):
        plugin.render()


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_chuck_norris_plugin_network_error_becomes_render_error(
    fake_jokes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _failing_urlopen(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("simulated network failure")

    monkeypatch.setattr(
        "postcards.plugin_pexels.util.pexels.urllib.request.urlopen", _failing_urlopen
    )

    plugin = load_plugin("chuck_norris", {}, registry=None)
    with pytest.raises(PluginRenderError):
        plugin.render()


# ---------------------------------------------------------------------------
# Plugin metadata
# ---------------------------------------------------------------------------


def test_chuck_norris_plugin_class_metadata() -> None:
    assert ChuckNorrisPlugin.name == "chuck_norris"
    assert "chuck" in ChuckNorrisPlugin.description.lower()


def test_chuck_norris_plugin_is_registered_in_default_registry() -> None:
    from postcards.plugins.registry import Registry

    assert Registry.default.has("chuck_norris")
    assert Registry.default.get("chuck_norris") is ChuckNorrisPlugin
