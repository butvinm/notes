"""Tests for tokenization, lemmatization, FTS query building, and exact-term extraction."""

import sqlite3
from collections.abc import Iterator

import pytest

from notes import stopwords, textnorm

# tokens


def test_tokens_lowercases_and_splits_on_non_word_characters() -> None:
    assert textnorm.tokens("Why doesn't Project-Atlas talk?") == ["why", "doesn", "project", "atlas", "talk"]


def test_tokens_keeps_digits_and_drops_single_characters() -> None:
    expected = ["port", "9092", "v2", "of", "kafka", "in", "2026"]

    assert textnorm.tokens("port 9092, v2 of Kafka 3.9 in 2026, a b c") == expected


def test_tokens_keeps_underscore_inside_a_token() -> None:
    assert textnorm.tokens("snake_case_name") == ["snake_case_name"]


def test_tokens_keeps_duplicates_and_order() -> None:
    assert textnorm.tokens("kafka then kafka") == ["kafka", "then", "kafka"]


@pytest.mark.parametrize("text", ["", "   ", "!!! --- ...", "a b c 1 2 3"])
def test_tokens_of_text_without_words(text: str) -> None:
    assert textnorm.tokens(text) == []


# has_cyrillic


@pytest.mark.parametrize(("token", "expected"), [("задача", True), ("fiшный", True), ("kafka", False), ("2026", False)])
def test_has_cyrillic(token: str, expected: bool) -> None:
    assert textnorm.has_cyrillic(token) is expected


# normalize


@pytest.mark.parametrize("inflection", ["задача", "задачи", "задачами", "задач", "Задачу"])
def test_normalize_maps_russian_inflections_to_one_lemma(inflection: str) -> None:
    assert textnorm.normalize(inflection) == "задача"


def test_normalize_leaves_latin_tokens_unchanged_apart_from_case() -> None:
    assert textnorm.normalize("Kafka Task Updates over WebSocket") == "kafka task updates over websocket"


def test_normalize_keeps_digits() -> None:
    assert textnorm.normalize("Kafka 3.9 on port 9092 since 2026") == "kafka on port 9092 since 2026"


def test_normalize_mixed_text() -> None:
    text = "Обновления задач через Kafka в ATLAS-27"

    assert textnorm.normalize(text) == "обновление задача через kafka atlas 27"


def test_normalize_of_empty_text_is_empty() -> None:
    assert textnorm.normalize("") == ""
    assert textnorm.normalize("!!! ---") == ""


def test_normalize_does_not_load_the_analyzer_for_latin_only_text(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail() -> None:
        raise AssertionError("pymorphy3 must not be loaded for Latin-only text")

    monkeypatch.setattr(textnorm, "analyzer", fail)

    assert textnorm.normalize("Kafka task updates over WebSocket, ATLAS-27, 2026") == (
        "kafka task updates over websocket atlas 27 2026"
    )
    assert textnorm.fts_query("why doesn't the Project Atlas talk to the backend") == (
        '"project" OR "atlas" OR "talk" OR "backend"'
    )
    assert textnorm.exact_terms("ATLAS-27 sync-worker") == ["atlas-27", "sync-worker"]


class _Parse:
    def __init__(self, normal_form: str) -> None:
        self.normal_form = normal_form


class _FakeAnalyzer:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def parse(self, word: str) -> list[_Parse]:
        self.calls.append(word)
        return [_Parse(f"lemma-of-{word}")]


@pytest.fixture
def fake_analyzer(monkeypatch: pytest.MonkeyPatch) -> Iterator[_FakeAnalyzer]:
    """Replace the pymorphy3 analyzer with a recording fake and drop the lemmas it produced afterwards."""
    fake = _FakeAnalyzer()
    monkeypatch.setattr(textnorm, "analyzer", lambda: fake)
    try:
        yield fake
    finally:
        textnorm.lemma.cache_clear()


def test_normalize_uses_the_analyzer_for_cyrillic_tokens_only(fake_analyzer: _FakeAnalyzer) -> None:
    # Words no other test uses, so the memoized real lemmas cannot answer for the fake.
    assert textnorm.normalize("Kafka тестслово 2026") == "kafka lemma-of-тестслово 2026"
    assert fake_analyzer.calls == ["тестслово"]


def test_lemma_is_memoized_per_token(fake_analyzer: _FakeAnalyzer) -> None:
    textnorm.normalize("другоеслово другоеслово")
    textnorm.normalize("Другоеслово")

    assert fake_analyzer.calls == ["другоеслово"]


def test_lemma_leaves_unknown_words_unchanged() -> None:
    assert textnorm.lemma("fiшный") == "fiшный"
    assert textnorm.lemma("задача27") == "задача27"


# fts_query


def test_fts_query_quotes_every_normalized_token_and_joins_with_or() -> None:
    assert textnorm.fts_query("Обновления задач через Kafka") == '"обновление" OR "задача" OR "kafka"'


def test_fts_query_drops_english_stopwords() -> None:
    query = "why doesn't the Project Atlas communicate with the backend over WebSocket"

    assert textnorm.fts_query(query) == '"project" OR "atlas" OR "communicate" OR "backend" OR "websocket"'


def test_fts_query_drops_russian_stopwords_after_lemmatization() -> None:
    # `этого` and `было` are not in the list themselves; their lemmas `это` and `быть` are.
    assert textnorm.fts_query("почему этого не было в задачах через кафку") == '"задача" OR "кафка"'


def test_fts_query_drops_duplicate_tokens() -> None:
    assert textnorm.fts_query("kafka Kafka задачи задачами") == '"kafka" OR "задача"'


def test_fts_query_quotes_tokens_with_special_characters() -> None:
    assert textnorm.fts_query("snake_case_name") == '"snake_case_name"'


def test_fts_query_neutralizes_fts5_syntax() -> None:
    # NEAR is an FTS5 operator, `*` a prefix marker, parentheses grouping, `-` and NOT exclusion, `^` an anchor.
    query = 'kafka* NEAR (websocket) -"phrase" NOT ^anchor: OR AND'

    assert textnorm.fts_query(query) == '"kafka" OR "near" OR "websocket" OR "phrase" OR "anchor"'


@pytest.mark.parametrize("text", ["", "   ", "the of and", "почему не это", "a b 1", "!!! ---"])
def test_fts_query_is_empty_when_nothing_is_searchable(text: str) -> None:
    assert textnorm.fts_query(text) == ""


@pytest.fixture
def fts() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE VIRTUAL TABLE t USING fts5 (path UNINDEXED, body, tokenize = 'porter unicode61 remove_diacritics 2')"
    )
    conn.execute("INSERT INTO t VALUES ('kafka', ?)", (textnorm.normalize("Обновления задач через Kafka в ATLAS-27"),))
    conn.execute("INSERT INTO t VALUES ('ws', ?)", (textnorm.normalize("WebSocket snake_case for the backend"),))
    try:
        yield conn
    finally:
        conn.close()


def _matches(conn: sqlite3.Connection, text: str) -> list[str]:
    query = textnorm.fts_query(text)
    return [row[0] for row in conn.execute("SELECT path FROM t WHERE t MATCH ? ORDER BY path", (query,))]


def test_fts_query_runs_against_fts5_and_matches_another_inflection(fts: sqlite3.Connection) -> None:
    assert _matches(fts, "задачами") == ["kafka"]
    assert _matches(fts, "backend websocket") == ["ws"]
    assert _matches(fts, "kafka or the backend") == ["kafka", "ws"]


def test_fts_query_with_operator_like_input_is_a_valid_plain_search(fts: sqlite3.Connection) -> None:
    assert _matches(fts, 'kafka* NEAR (nothing) -"here" ^anchor') == ["kafka"]
    # unicode61 splits `snake_case` at the underscore on both sides, so the quoted token matches as a phrase.
    assert _matches(fts, "snake_case") == ["ws"]


# exact_terms


def test_exact_terms_preserves_issue_ids_and_hyphenated_tags() -> None:
    text = "Fix ATLAS-27, see sync-worker (again) and ATLAS-27."

    assert textnorm.exact_terms(text) == ["fix", "atlas-27", "see", "sync-worker", "again", "and"]


def test_exact_terms_keeps_single_characters_and_cyrillic() -> None:
    assert textnorm.exact_terms("C и Rust: задачи-2026") == ["c", "и", "rust", "задачи-2026"]


def test_exact_terms_skips_hyphen_runs_used_as_dashes() -> None:
    assert textnorm.exact_terms("Kafka - not WebSocket -- really") == ["kafka", "not", "websocket", "really"]


@pytest.mark.parametrize("text", ["", "   ", "- -- ---", "!!! ..."])
def test_exact_terms_of_text_without_words(text: str) -> None:
    assert textnorm.exact_terms(text) == []


# is_issue_id


@pytest.mark.parametrize("term", ["atlas-27", "ATLAS-27", "ab-1", "proj1-4", "orion-1234"])
def test_is_issue_id_accepts_tracker_keys(term: str) -> None:
    assert textnorm.is_issue_id(term) is True


@pytest.mark.parametrize(
    "term",
    ["sync-worker", "27-atlas", "a-1", "atlas-27-x", "atlas27", "atlas-", "-27", "1ab-2", "atlas-27\n", "atlas_1-2"],
)
def test_is_issue_id_rejects_other_terms(term: str) -> None:
    assert textnorm.is_issue_id(term) is False


# stopwords


def test_stopword_lists_are_lowercase_and_small() -> None:
    assert stopwords.STOPWORDS == stopwords.ENGLISH | stopwords.RUSSIAN
    assert all(word == word.lower() for word in stopwords.STOPWORDS)
    assert len(stopwords.STOPWORDS) < 300
