"""Text normalization for the FTS index and for queries.

The FTS5 table holds pre-normalized text: Cyrillic tokens are replaced by their `pymorphy3` normal form,
Latin tokens are left as they are for the FTS5 porter stemmer, digits are kept.
The same `normalize` runs on a query, so an inflected Russian query finds a note written in another inflection.

`pymorphy3` takes a noticeable time to load its dictionary, so the analyzer is created lazily,
only when a token containing Cyrillic letters is seen, and once per process; lemmas are memoized per token.
`recall` runs on every prompt and `tick` every minute, and neither pays for the dictionary when nothing is Cyrillic.

Exact matching of tags and issue IDs works on raw tokens (`exact_terms`), where `ATLAS-27` stays one term.
"""

import functools
import re
from typing import TYPE_CHECKING

from notes.stopwords import STOPWORDS

if TYPE_CHECKING:
    from pymorphy3 import MorphAnalyzer

MIN_TOKEN_LENGTH = 2

_WORD_RE = re.compile(r"\w+")
_EXACT_TERM_RE = re.compile(r"[\w-]+")
_HAS_WORD_CHAR_RE = re.compile(r"\w")
# The Cyrillic block (U+0400 to U+04FF) and the Cyrillic Supplement (U+0500 to U+052F).
_CYRILLIC_RE = re.compile(f"[{chr(0x0400)}-{chr(0x052F)}]")
_ISSUE_ID_RE = re.compile(r"[a-z][a-z0-9]+-\d+")


def tokens(text: str) -> list[str]:
    """Lowercase `\\w+` tokens of at least two characters, in order of appearance, duplicates kept.

    Digits are word characters, so `9092` and `v2` are tokens; single characters such as `a`, `в`, or `2` are not.
    """
    return [token for token in _WORD_RE.findall(text.lower()) if len(token) >= MIN_TOKEN_LENGTH]


def has_cyrillic(token: str) -> bool:
    """Whether the token contains a letter from the Cyrillic or Cyrillic Supplement blocks."""
    return _CYRILLIC_RE.search(token) is not None


@functools.cache
def analyzer() -> "MorphAnalyzer":
    """The process-wide `pymorphy3` analyzer, created on first use.

    The import lives here so that importing this module, and running `normalize` on Latin-only text, never loads the
    dictionary.
    """
    import pymorphy3

    return pymorphy3.MorphAnalyzer()


@functools.cache
def lemma(token: str) -> str:
    """The `pymorphy3` normal form of a Cyrillic token: `задачами` becomes `задача`.

    The most probable parse wins; a token the dictionary does not know comes back unchanged.
    Results are memoized for the life of the process, so a note body full of repeated words costs one parse per word.
    """
    return analyzer().parse(token)[0].normal_form


def normalize(text: str) -> str:
    """The FTS representation of a text: `tokens` with every Cyrillic token replaced by its lemma, joined by spaces.

    `Обновления задач через Kafka` becomes `обновление задача через kafka`;
    Latin tokens and digits pass through unchanged apart from lowercasing.
    """
    return " ".join(lemma(token) if has_cyrillic(token) else token for token in tokens(text))


def fts_query(text: str) -> str:
    """An FTS5 MATCH expression for a free-text query: the normalized tokens minus stopwords, quoted, joined by `OR`.

    Every token is double-quoted, so `NEAR`, `NOT`, an underscore, or anything else with meaning to the FTS5 parser
    is searched as a plain term. Duplicate tokens are dropped so a repeated word does not count twice in `bm25`.
    An empty string means there is nothing to search for: every token was a stopword or too short.
    """
    seen: set[str] = set()
    parts: list[str] = []
    for token in normalize(text).split():
        if token in STOPWORDS or token in seen:
            continue
        seen.add(token)
        parts.append('"' + token.replace('"', '""') + '"')
    return " OR ".join(parts)


def exact_terms(text: str) -> list[str]:
    """Raw lowercase `[\\w-]+` tokens for tag and issue-ID matching, deduplicated, in order of appearance.

    Hyphens are part of a term, so `ATLAS-27` becomes `atlas-27` and `sync-worker` stays whole.
    Runs of hyphens alone, such as the ` - ` dash in prose, contain no word character and are skipped.
    """
    seen: set[str] = set()
    terms: list[str] = []
    for term in _EXACT_TERM_RE.findall(text.lower()):
        if term in seen or _HAS_WORD_CHAR_RE.search(term) is None:
            continue
        seen.add(term)
        terms.append(term)
    return terms


def is_issue_id(term: str) -> bool:
    """Whether a term looks like a tracker issue ID: `atlas-27`, `proj1-4`; letters first, then digits after a hyphen.

    The check is case-insensitive, so it accepts both the raw `ATLAS-27` and the lowercase form `exact_terms` produces.
    """
    return _ISSUE_ID_RE.fullmatch(term.lower()) is not None
