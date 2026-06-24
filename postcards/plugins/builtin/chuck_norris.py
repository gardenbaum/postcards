"""``chuck_norris`` — pick a random Chuck Norris joke and a matching picture.

This is the M3 port of the legacy
``postcards.plugin_chuck_norris.postcards_chuck_norris.PostcardsChuckNorris``
plugin. The legacy plugin used ``nltk`` for keyword extraction;
the M0 modernization replaced ``nltk`` with a regex-based keyword
extractor (still in the legacy module). The M3 plugin keeps the
regex extractor and exposes it as a plugin named ``chuck_norris``.

Configuration payload
---------------------

``payload.category`` (optional)
    One of ``"nerdy"``, ``"explicit"``. When set, only jokes in
    that category are considered.
``payload.duplicate_file`` (optional)
    Path to a file listing joke ids that have already been sent
    (one id per line). The plugin will not pick a joke whose id
    appears in this file; the id of the picked joke is appended
    to the file after the send.
"""

from __future__ import annotations

import json
import os
import random
import re
from collections.abc import Mapping
from typing import Any, ClassVar

from postcards.plugins.base import PluginResult
from postcards.plugins.base_impl import PluginBase
from postcards.plugins.builtin._helpers import make_absolute
from postcards.plugins.errors import PluginConfigError, PluginRenderError
from postcards.plugins.registry import register

# Default path to the bundled jokes file. Lives next to this
# module so ``postcards.plugins.builtin.chuck_norris`` is a
# self-contained install.
_JOKES_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)), "chuck_norris_jokes.json")

_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "being",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "doing",
        "don",
        "for",
        "from",
        "had",
        "has",
        "have",
        "having",
        "he",
        "her",
        "here",
        "him",
        "his",
        "how",
        "if",
        "in",
        "is",
        "it",
        "its",
        "just",
        "me",
        "my",
        "no",
        "not",
        "of",
        "on",
        "once",
        "only",
        "or",
        "other",
        "our",
        "out",
        "over",
        "own",
        "quot",
        "she",
        "should",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "until",
        "up",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "chuck",
        "norris",
    }
)
_WORD_RE = re.compile(r"[A-Za-z']+")


def _extract_keywords(joke: str, limit: int = 3) -> list[str]:
    """Pick up to ``limit`` keyword tokens out of ``joke``.

    Heuristic: prefer capitalized words (proper nouns) and fall
    back to longer common-noun-ish words; drop stopwords and very
    short tokens. The order is preserved so the first non-stopword
    wins.
    """
    words = _WORD_RE.findall(joke)
    keywords: list[str] = []
    seen_lower: set[str] = set()
    for word in words:
        normalized = word.lower()
        if normalized in _STOPWORDS:
            continue
        if len(word) <= 2 and not word[0].isupper():
            continue
        if normalized in seen_lower:
            continue
        seen_lower.add(normalized)
        keywords.append(word)
        if len(keywords) >= limit:
            break
    return keywords


class ChuckNorrisPlugin(PluginBase):
    """Pick a random Chuck Norris joke and a matching picture."""

    name: ClassVar[str] = "chuck_norris"
    description: ClassVar[str] = "pick a Chuck Norris joke and a matching picture"

    def configure(self, payload: Mapping[str, Any]) -> None:
        category = payload.get("category")
        if category is not None and not isinstance(category, str):
            raise PluginConfigError(self.name, "'category' must be a string when present")
        dup = payload.get("duplicate_file")
        if dup is not None and not isinstance(dup, str):
            raise PluginConfigError(
                self.name, "'duplicate_file' must be a string path when present"
            )
        super().configure(payload)

    def render(self) -> PluginResult:
        jokes = self._read_jokes()

        category = self._payload.get("category")
        if isinstance(category, str):
            jokes = [j for j in jokes if category in (j.get("categories") or [])]

        if not jokes:
            raise PluginRenderError(self.name, f"no jokes found for category: {category!r}")

        dup_path_raw = self._payload.get("duplicate_file")
        dup_path = make_absolute(str(dup_path_raw)) if isinstance(dup_path_raw, str) else None
        exclude_ids = self._read_exclude_file(dup_path)

        jokes = [j for j in jokes if str(j.get("id")) not in exclude_ids]
        if not jokes:
            raise PluginRenderError(self.name, "no more jokes to choose from (all excluded)")

        joke = random.choice(jokes)
        text = str(joke.get("joke", ""))
        keywords = _extract_keywords(text, limit=3)
        keyword = " ".join(keywords)

        # Lazy import: the helper reaches for ``urllib``, which is
        # heavy enough that we only want to pay the cost when the
        # plugin is actually rendering.
        from postcards.plugin_pexels.util.pexels import (
            get_random_image_url,
            read_from_url,
        )

        try:
            url = get_random_image_url(keyword=keyword) if keyword else get_random_image_url()
        except Exception:
            url = get_random_image_url()

        try:
            handle = read_from_url(url)
        except Exception as exc:
            raise PluginRenderError(self.name, f"network error: {exc}") from exc

        if dup_path is not None:
            self._append_to_exclude_file(dup_path, str(joke.get("id")))

        self.logger.debug("keyword=%r text=%r url=%s", keyword, text, url)
        return PluginResult(image=handle, message=text)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _read_jokes(self) -> list[dict[str, Any]]:
        with open(_JOKES_PATH, encoding="utf-8") as f:
            data = json.load(f)
        result = data.get("value") if isinstance(data, dict) else None
        return result if isinstance(result, list) else []

    @staticmethod
    def _read_exclude_file(path: str | None) -> set[str]:
        if path is None or not os.path.isfile(path):
            return set()
        with open(path, encoding="utf-8") as f:
            return {line.strip() for line in f if line.strip()}

    @staticmethod
    def _append_to_exclude_file(path: str, joke_id: str) -> None:
        with open(path, "a", encoding="utf-8") as f:
            f.write(joke_id + "\n")


register(ChuckNorrisPlugin.name, ChuckNorrisPlugin, description=ChuckNorrisPlugin.description)


__all__ = ["ChuckNorrisPlugin", "_extract_keywords"]
