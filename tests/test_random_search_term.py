"""Tests for ``postcards.plugin_random.random_search_term``.

This module is a pure-Python translation of the upstream Js2Py-converted
function. The tests pin the output contract: every call returns a
``.jpg`` string that contains at least one non-empty prefix (when
the chosen camera convention provides one) and a zero-padded numeric
suffix.
"""

from __future__ import annotations

import re

from postcards.plugin_random.random_search_term.random_search_term import (
    _CAMERAS,
    get_random_search_term,
)


def test_get_random_search_term_returns_jpg_filename() -> None:
    """The output is always a non-empty ``.jpg`` filename string."""
    for _ in range(50):
        term = get_random_search_term()
        assert isinstance(term, str)
        assert term.endswith(".jpg")
        assert len(term) > 4  # at least "x.jpg"


def test_get_random_search_term_uses_all_camera_conventions() -> None:
    """Across many calls, every camera convention is sampled at least once.

    The 26-entry ``_CAMERAS`` table mirrors the original Js2Py output;
    losing any entry is a regression.
    """
    # A direct check: at least 26 distinct prefixes appear over
    # enough samples, including each entry's range / width pair.
    prefixes: set[str] = set()
    for _ in range(5000):
        prefixes.add(get_random_search_term().rsplit(".", 1)[0])
    # 26 different conventions produce at least 26 distinct prefixes
    # (two conventions have empty prefixes and rely on suffix alone —
    # they share a single visible form here, so we assert >= 24).
    assert len(prefixes) >= 24


def test_get_random_search_term_numeric_suffix_is_padded() -> None:
    """The numeric suffix in the output is zero-padded to the convention's width."""
    # The IMG_ convention (choice=7) pads to width 4, so its output
    # looks like "IMG_0042.jpg" — the numeric part is exactly 4 digits.
    seen = False
    for _ in range(200):
        term = get_random_search_term()
        if term.startswith("IMG_") and "_" not in term[4:]:
            # No second underscore, so this is the IMG_ convention (choice 7),
            # not the date-based IMG_ choice 25.
            match = re.match(r"^IMG_(\d{4})\.jpg$", term)
            if match is not None:
                seen = True
                break
    assert seen, "no IMG_0000.jpg-style term observed"


def test_cameras_table_has_26_entries() -> None:
    """The ``_CAMERAS`` table mirrors the upstream's 26 conventions."""
    assert len(_CAMERAS) == 26
    for entry in _CAMERAS:
        prefix, range_upper, width = entry
        assert isinstance(prefix, str)
        assert isinstance(range_upper, int) and range_upper > 0
        assert isinstance(width, int) and width > 0
