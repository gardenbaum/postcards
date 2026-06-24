"""Send a postcard with a random Chuck Norris joke and a matching image.

The plugin picks a joke from a bundled JSON dataset, extracts a few
"keyword" words from it, and asks the pexels utility for a matching
image. If keyword extraction yields nothing usable, it falls back to
a default image.

History
-------

The previous implementation used ``nltk`` for part-of-speech tagging
so the plugin could pick "noun" tokens out of a joke. ``nltk`` was a
heavy dependency (it pulls punkt + averaged_perceptron_tagger from
the network at import time) and the noun extraction only fed into
the pexels image-search keyword, which itself was a no-op (the
upstream ``pexels`` API ignored keywords and returned a curated
random photo).

This module replaces the nltk-based noun extractor with a simple
heuristic: words that are either capitalized (proper-noun-ish) or
not in a small stoplist of very common English words. The output is
fed into the pexels utility as a seed for ``picsum.photos`` — the
seed only affects which photo is returned; no network NLP is involved.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import sys

from postcards.plugin_pexels.util.pexels import get_random_image_url, read_from_url
from postcards.postcards import Postcards

_JOKES_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)), "chuck_norris_jokes.json")

# Words to drop before they ever reach the image keyword slot. The
# original nltk-based filter dropped "chuck", "norris" and "quot"
# (the latter from ``"quot"-style quote tokens); we expand the list
# with a handful of very common English words that would not help a
# random-photo search.
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
        # The original filter explicitly dropped these.
        "chuck",
        "norris",
    }
)

# Match words; a "word" is one or more ASCII letters or apostrophes.
_WORD_RE = re.compile(r"[A-Za-z']+")


def _extract_keywords(joke: str, limit: int = 3) -> list[str]:
    """Pick up to ``limit`` keyword tokens out of ``joke``.

    Heuristic: prefer capitalized words (proper nouns) and fall back
    to longer common-noun-ish words; drop stopwords and very short
    tokens. The order is preserved so the first non-stopword wins.
    """
    words = _WORD_RE.findall(joke)
    keywords: list[str] = []
    for word in words:
        normalized = word.lower()
        if normalized in _STOPWORDS:
            continue
        if len(word) <= 2 and not word[0].isupper():
            continue
        if normalized in {k.lower() for k in keywords}:
            continue
        keywords.append(word)
        if len(keywords) >= limit:
            break
    return keywords


class PostcardsChuckNorris(Postcards):
    """``postcards-chuck-norris`` — Chuck Norris jokes + matching photo."""

    def enhance_send_subparser(self, parser: argparse.ArgumentParser) -> None:  # type: ignore[name-defined]  # noqa: F821
        parser.add_argument(
            "--category",
            default=None,
            type=str,
            help="choose a custom joke category: nerdy, explicit",
        )
        parser.add_argument(
            "--duplicate-file",
            default=None,
            type=str,
            help="avoid sending the same joke twice by setting a file which stores already sent jokes",
        )

    def get_img_and_text(self, plugin_payload: dict, cli_args: argparse.Namespace) -> dict:  # type: ignore[name-defined]  # noqa: F821
        jokes = self._read_jokes()

        if cli_args.category:
            jokes = self._filter_by_category(jokes, cli_args.category)

        if not jokes:
            self.logger.error("No jokes found for category: {}".format(cli_args.category))
            sys.exit(1)

        exclude_file: str | None = None
        exclude_jokes: list[str] = []
        if cli_args.duplicate_file:
            exclude_file = self._make_absolute_path(cli_args.duplicate_file)
            if os.path.isfile(exclude_file):
                with open(exclude_file, encoding="utf-8") as f:
                    content = f.readlines()
                    exclude_jokes = [x.strip() for x in content]

        jokes = self._filter_by_exclude_id(jokes, exclude_jokes)

        if not jokes:
            self.logger.error(
                "No more jokes to choose from. everything excluded by {}".format(cli_args.duplicate_file)
            )
            sys.exit(1)

        joke = random.choice(jokes)
        postcard_text = joke.get("joke", "")
        keywords = _extract_keywords(postcard_text, limit=3)

        keyword = " ".join(keywords)
        if keyword:
            try:
                url = get_random_image_url(keyword=keyword)
            except Exception:
                url = get_random_image_url()
        else:
            url = get_random_image_url()

        self.logger.debug("keyword: {}, text: {}".format(keyword, postcard_text))
        self.logger.debug("url: {}".format(url))

        if cli_args.duplicate_file and exclude_file is not None:
            with open(exclude_file, "a", encoding="utf-8") as excludes:
                excludes.write(str(joke.get("id")) + "\n")

        return {
            "img": read_from_url(url),
            "text": postcard_text,
        }

    def _read_jokes(self) -> list[dict]:
        with open(_JOKES_PATH, encoding="utf-8") as f:
            data = json.loads(f.read())
            result = data.get("value")
            return result if isinstance(result, list) else []

    @staticmethod
    def _filter_by_category(jokes: list[dict], category: str) -> list[dict]:
        return [val for val in jokes if category in (val.get("categories") or [])]

    @staticmethod
    def _filter_by_exclude_id(jokes: list[dict], exclude_id_list: list[str]) -> list[dict]:
        return [val for val in jokes if val.get("id") not in exclude_id_list]


def main() -> None:
    PostcardsChuckNorris().main(sys.argv[1:])


if __name__ == "__main__":
    logging.basicConfig(
        stream=sys.stdout,
        level=logging.INFO,
        format="%(name)s (%(levelname)s): %(message)s",
    )
    main()