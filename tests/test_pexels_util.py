"""Tests for ``postcards.plugin_pexels.util.pexels``.

The pexels utility fetches random images from ``picsum.photos``. These
tests pin the URL contract — they do not actually hit the network.

The legacy implementation used ``pypexels`` (an unofficial Pexels.com
client) with a hard-coded API key. ``pypexels`` is unmaintained; M1
drops it for ``picsum.photos``, which requires no API key.
"""

from __future__ import annotations

from postcards.plugin_pexels.util.pexels import (
    _seed_for,
    get_random_image_url,
)


def test_get_random_image_url_no_keyword_uses_default_seed() -> None:
    """A ``None`` or empty keyword produces a stable default URL."""
    assert get_random_image_url() == get_random_image_url()
    assert get_random_image_url(None) == get_random_image_url("")
    # The default seed encodes the literal ``"postcards"`` string.
    assert "postcards" in get_random_image_url()


def test_get_random_image_url_keyword_changes_seed() -> None:
    """Different keywords produce different URLs."""
    url_a = get_random_image_url(keyword="alpha")
    url_b = get_random_image_url(keyword="beta")
    assert url_a != url_b


def test_get_random_image_url_same_keyword_is_stable() -> None:
    """The same keyword always maps to the same URL within a process."""
    assert get_random_image_url(keyword="alpine") == get_random_image_url(keyword="alpine")


def test_get_random_image_url_uses_picsum() -> None:
    """The URL points at picsum.photos, not the legacy pypexels API."""
    url = get_random_image_url(keyword="test")
    assert url.startswith("https://picsum.photos/seed/")
    # Standard postcard dimensions are encoded in the URL.
    assert url.endswith("/800/600")


def test_seed_for_empty_returns_postcards_default() -> None:
    """``_seed_for`` short-circuits on empty input to a stable default."""
    assert _seed_for(None) == "postcards"
    assert _seed_for("") == "postcards"


def test_seed_for_keyword_returns_deterministic_hash() -> None:
    """``_seed_for`` hashes the keyword to a 16-char ASCII-safe seed."""
    seed_a = _seed_for("hello")
    seed_b = _seed_for("hello")
    seed_c = _seed_for("world")
    assert seed_a == seed_b
    assert seed_a != seed_c
    assert len(seed_a) == 16
    assert seed_a.isascii()
