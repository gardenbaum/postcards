"""Tests for ``postcards.plugin_chuck_norris._extract_keywords``.

The chuck_norris plugin used to use ``nltk`` to part-of-speech tag
jokes and pull out nouns to feed into the image-search keyword. M1
drops ``nltk`` and replaces it with a regex tokenizer + stoplist
(see ``_extract_keywords`` in
``postcards.plugin_chuck_norris.postcards_chuck_norris``). These
tests pin the new contract.
"""

from __future__ import annotations

from postcards.plugin_chuck_norris.postcards_chuck_norris import _extract_keywords


def test_extract_keywords_picks_proper_nouns() -> None:
    """Capitalized tokens that are not in the stoplist are picked first.

    The input also has ``Chuck`` and ``Norris`` which are explicitly
    in the plugin's stoplist (the original nltk-based code dropped
    these too — they would otherwise dominate the keyword slot for
    every joke).
    """
    keywords = _extract_keywords("Eiffel Tower collapse")
    assert "Eiffel" in keywords
    assert "Tower" in keywords
    assert "Chuck" not in keywords
    assert "Norris" not in keywords


def test_extract_keywords_skips_stopwords() -> None:
    """Common English words never make it into the keyword slot."""
    keywords = _extract_keywords("the and a of to in is are")
    assert keywords == []


def test_extract_keywords_skips_short_lowercase_words() -> None:
    """Words of length <= 2 that are not capitalized are skipped."""
    keywords = _extract_keywords("a an to of by be go do")
    assert keywords == []


def test_extract_keywords_caps_at_limit() -> None:
    """The keyword list is capped at ``limit`` entries."""
    keywords = _extract_keywords("Alpha Beta Gamma Delta Epsilon Zeta", limit=3)
    assert len(keywords) == 3
    assert keywords == ["Alpha", "Beta", "Gamma"]


def test_extract_keywords_default_limit_is_three() -> None:
    """The default limit mirrors the upstream nltk-based implementation."""
    keywords = _extract_keywords("Alpha Beta Gamma Delta Epsilon")
    assert len(keywords) == 3


def test_extract_keywords_dedupes_case_insensitively() -> None:
    """Two words with the same lowercase form do not both appear."""
    keywords = _extract_keywords("Apple apple APPLE banana Banana")
    # First "Apple" wins; "banana" and "Banana" share lowercase too —
    # the first occurrence of each unique lowercase wins.
    assert keywords == ["Apple", "banana"]


def test_extract_keywords_handles_empty_input() -> None:
    """Empty / whitespace-only input yields no keywords."""
    assert _extract_keywords("") == []
    assert _extract_keywords("   \t\n  ") == []


def test_extract_keywords_handles_punctuation() -> None:
    """Punctuation is stripped — words adjacent to ``.`` / ``,`` / ``!`` survive.

    Uses ``limit=10`` so all four expected keywords come back; the
    default limit is 3.
    """
    keywords = _extract_keywords("Hello, World! Foo. Bar?", limit=10)
    assert "Hello" in keywords
    assert "World" in keywords
    assert "Foo" in keywords
    assert "Bar" in keywords
