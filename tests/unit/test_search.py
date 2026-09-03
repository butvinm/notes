"""Tests for ranking and search on the corpus under `tests/fixtures/corpus/`: FTS text scores, exact tag and issue-ID
matches, their combination in `rank`, the `search` assembly with its status rules and limit, and the `recall` assembly
with its sections, path boost, threshold, and cap."""

import sqlite3
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from notes import db, search
from notes.config import RecallConfig
from notes.search import Ranked, RecallItem, RecallResult, SearchResult
from tests.conftest import Corpus, indexed_paths

RECALL = RecallConfig()
"""The packaged recall defaults: limit 10, min_score 1.0, path_boost 2.0."""


def unread(conn: sqlite3.Connection, path: str, delivered_at: str) -> None:
    """Record an unread delivery through the open (autocommit) connection."""
    conn.execute(
        "INSERT INTO deliveries (path, occurrence_at, delivered_at) VALUES (?, ?, ?)",
        (path, delivered_at, delivered_at),
    )


@pytest.fixture
def conn(search_corpus: Path) -> Iterator[sqlite3.Connection]:
    connection = db.connect(search_corpus)
    yield connection
    connection.close()


def paths_of(items: Sequence[Ranked | SearchResult | RecallItem]) -> list[str]:
    return [item.path for item in items]


def test_every_corpus_note_is_valid_and_indexed(search_corpus: Path) -> None:
    assert indexed_paths(search_corpus) == sorted(Corpus.ALL)


# text_matches


def test_text_matches_finds_a_russian_note_through_another_inflection(conn: sqlite3.Connection) -> None:
    # `словоформами` (instrumental) and the title's `словоформам` (dative) share the lemma `словоформа`.
    assert list(search.text_matches(conn, "словоформами")) == [Corpus.LEMMAS]
    assert list(search.text_matches(conn, "соединениями")) == [Corpus.RUSSIAN_KAFKA]


def test_text_matches_finds_the_notes_of_a_natural_language_question(conn: sqlite3.Connection) -> None:
    scores = search.text_matches(conn, "why doesn't Project Atlas communicate with the backend over WebSocket")

    assert set(scores) == {Corpus.KAFKA, Corpus.WEBSOCKET, Corpus.RUSSIAN_KAFKA, Corpus.LEGACY}
    assert all(score > 0 for score in scores.values())


def test_text_matches_weights_a_keyword_above_a_body_mention(conn: sqlite3.Connection) -> None:
    # `replay` is a keyword of the Kafka decision and a body word of the retention fact, and appears nowhere else.
    scores = search.text_matches(conn, "replay")

    assert set(scores) == {Corpus.KAFKA, Corpus.RETENTION}
    assert scores[Corpus.KAFKA] > scores[Corpus.RETENTION]


def test_text_matches_weights_a_title_above_a_body_mention(conn: sqlite3.Connection) -> None:
    # `retention` is in the title of the retention fact; `websocket` is only in the body of the Kafka decision.
    scores = search.text_matches(conn, "retention websocket")

    assert scores[Corpus.RETENTION] > scores[Corpus.KAFKA]


@pytest.mark.parametrize("query", ["", "the of and", "почему не это", "quantum entanglement"])
def test_text_matches_is_empty_for_an_empty_or_unmatched_query(conn: sqlite3.Connection, query: str) -> None:
    assert search.text_matches(conn, query) == {}


def test_text_matches_treats_fts5_syntax_as_plain_words(conn: sqlite3.Connection) -> None:
    scores = search.text_matches(conn, 'kafka* NEAR (nothing) -"here" ^anchor')

    assert set(scores) == {Corpus.KAFKA, Corpus.RUSSIAN_KAFKA, Corpus.RETENTION}


# exact_matches


def test_exact_matches_reports_issue_ids_and_tags_as_the_notes_spell_them(conn: sqlite3.Connection) -> None:
    assert search.exact_matches(conn, ["atlas-27", "sync-worker"]) == {
        Corpus.KAFKA: ["issue: ATLAS-27", "tag: sync-worker"],
        Corpus.WEBSOCKET: ["issue: ATLAS-27"],
    }


def test_exact_matches_orders_reasons_by_the_terms(conn: sqlite3.Connection) -> None:
    matches = search.exact_matches(conn, ["sync-worker", "ATLAS-27", "sync-worker"])

    assert matches[Corpus.KAFKA] == ["tag: sync-worker", "issue: ATLAS-27"]


def test_exact_matches_is_case_insensitive(conn: sqlite3.Connection) -> None:
    assert search.exact_matches(conn, ["LIPS-12"]) == {Corpus.LIPS: ["issue: LIPS-12"]}
    assert search.exact_matches(conn, ["Legacy"]) == {Corpus.LEGACY: ["tag: legacy"]}


def test_exact_matches_ignores_terms_that_are_not_tags(conn: sqlite3.Connection) -> None:
    # `kafka` is a tag on two notes; `replay` is a keyword and `atlas` a title word, neither is a tag.
    matches = search.exact_matches(conn, ["kafka", "replay", "atlas", "atlas-99"])

    assert matches == {Corpus.RUSSIAN_KAFKA: ["tag: kafka"], Corpus.RETENTION: ["tag: kafka"]}
    assert search.exact_matches(conn, []) == {}


def test_exact_matches_matches_a_cyrillic_tag(conn: sqlite3.Connection) -> None:
    assert search.exact_matches(conn, ["Поиск"]) == {Corpus.LEMMAS: ["tag: поиск"]}


@pytest.mark.parametrize(
    ("tag", "reason"),
    [
        ("ATLAS-27", "issue: ATLAS-27"),
        ("proj1-4", "issue: proj1-4"),
        ("sync-worker", "tag: sync-worker"),
        ("kafka", "tag: kafka"),
        ("поиск", "tag: поиск"),
    ],
)
def test_exact_reason(tag: str, reason: str) -> None:
    assert search.exact_reason(tag) == reason


# rank


def test_rank_adds_the_bonus_per_exact_reason_and_the_text_score() -> None:
    text = {"notes/a.md": 2.5, "notes/b.md": 7.0}
    exact = {"notes/a.md": ["issue: X-1", "tag: t"], "notes/c.md": ["tag: t"]}

    assert search.rank(text, exact) == [
        Ranked("notes/a.md", 22.5, ("issue: X-1", "tag: t", "text")),
        Ranked("notes/c.md", 10.0, ("tag: t",)),
        Ranked("notes/b.md", 7.0, ("text",)),
    ]


def test_rank_puts_one_exact_match_above_any_text_score() -> None:
    ranked = search.rank({"notes/text.md": 9.99}, {"notes/tag.md": ["tag: t"]})

    assert paths_of(ranked) == ["notes/tag.md", "notes/text.md"]


def test_rank_breaks_ties_by_path() -> None:
    text = {"notes/b.md": 1.0, "notes/a.md": 1.0, "notes/c.md": 1.0}

    assert paths_of(search.rank(text, {})) == ["notes/a.md", "notes/b.md", "notes/c.md"]


def test_rank_of_nothing_is_empty() -> None:
    assert search.rank({}, {}) == []


# search


def test_search_puts_the_notes_tagged_with_the_issue_first(conn: sqlite3.Connection) -> None:
    results = search.search(conn, "ATLAS-27")

    # The two notes tagged ATLAS-27 come first, between them ordered by text score; the note whose tag and body only
    # mention ATLAS-31 shares the `atlas` token and follows as a plain text match.
    assert set(paths_of(results)[:2]) == {Corpus.KAFKA, Corpus.WEBSOCKET}
    assert paths_of(results)[2:] == [Corpus.RUSSIAN_KAFKA]
    by_path = {item.path: item for item in results}
    assert by_path[Corpus.KAFKA].reasons == ("issue: ATLAS-27", "text")
    assert by_path[Corpus.WEBSOCKET].reasons == ("issue: ATLAS-27", "text", "superseded")
    assert by_path[Corpus.RUSSIAN_KAFKA].reasons == ("text",)
    assert results[0].score > results[1].score > search.EXACT_BONUS > results[2].score > 0


def test_search_carries_the_title_and_the_effective_status(conn: sqlite3.Connection) -> None:
    results = search.search(conn, "WebSocket")

    assert results == [
        SearchResult(Corpus.KAFKA, "Project Atlas task updates over Kafka", "active", results[0].score, ("text",))
    ]


def test_search_omits_archived_and_superseded_text_matches_by_default(conn: sqlite3.Connection) -> None:
    assert paths_of(search.search(conn, "WebSocket")) == [Corpus.KAFKA]
    assert paths_of(search.search(conn, "nginx")) == []


def test_search_all_includes_them_with_their_status_as_the_last_reason(conn: sqlite3.Connection) -> None:
    results = search.search(conn, "WebSocket", include_all=True)

    assert set(paths_of(results)) == {Corpus.KAFKA, Corpus.WEBSOCKET, Corpus.LEGACY}
    by_path = {item.path: item for item in results}
    assert by_path[Corpus.WEBSOCKET].reasons == ("text", "superseded")
    assert by_path[Corpus.LEGACY].reasons == ("text", "archived")
    assert by_path[Corpus.KAFKA].reasons == ("text",)
    assert [item.score for item in results] == sorted((item.score for item in results), reverse=True)


def test_search_keeps_an_archived_or_superseded_note_matched_by_an_exact_term(conn: sqlite3.Connection) -> None:
    legacy = search.search(conn, "legacy")
    atlas = search.search(conn, "project-atlas")

    assert paths_of(legacy) == [Corpus.LEGACY]
    assert legacy[0].reasons == ("tag: legacy", "text", "archived")
    assert set(paths_of(atlas)[:2]) == {Corpus.KAFKA, Corpus.WEBSOCKET}
    assert paths_of(atlas)[2:] == [Corpus.RUSSIAN_KAFKA]
    by_path = {item.path: item for item in atlas}
    assert by_path[Corpus.WEBSOCKET].reasons == ("tag: project-atlas", "text", "superseded")
    assert by_path[Corpus.KAFKA].reasons == ("tag: project-atlas", "text")


def test_search_limit_counts_the_results_that_remain(conn: sqlite3.Connection) -> None:
    everything = search.search(conn, "kafka")

    assert len(everything) == 3
    assert paths_of(search.search(conn, "kafka", limit=2)) == paths_of(everything)[:2]
    # `WebSocket` scores the superseded and archived notes above the active one; the limit is not spent on them.
    assert paths_of(search.search(conn, "WebSocket", limit=1)) == [Corpus.KAFKA]
    assert len(search.search(conn, "WebSocket", limit=2, include_all=True)) == 2


def test_search_of_an_empty_or_unmatched_query_is_empty(conn: sqlite3.Connection) -> None:
    assert search.search(conn, "") == []
    assert search.search(conn, "the of and") == []
    assert search.search(conn, "quantum entanglement") == []


def test_search_survives_a_note_removed_between_its_queries(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `tick` from systemd may sync a deleted note away while a search is between its two queries."""
    real_rows = search._rows

    def rows_without_kafka(connection: sqlite3.Connection, paths: Sequence[str]) -> dict[str, sqlite3.Row]:
        return {path: row for path, row in real_rows(connection, paths).items() if path != Corpus.KAFKA}

    monkeypatch.setattr(search, "_rows", rows_without_kafka)

    assert paths_of(search.search(conn, "ATLAS-27")) == [Corpus.WEBSOCKET, Corpus.RUSSIAN_KAFKA]


# as_data


def test_as_data(conn: sqlite3.Connection, search_corpus: Path) -> None:
    result = next(item for item in search.search(conn, "ATLAS-27") if item.path == Corpus.WEBSOCKET)

    assert search.as_data(search_corpus, result) == {
        "path": Corpus.WEBSOCKET,
        "abs_path": str(search_corpus / Corpus.WEBSOCKET),
        "title": "Project Atlas task updates over WebSocket",
        "effective_status": "superseded",
        "score": round(result.score, 3),
        "reasons": ["issue: ATLAS-27", "text", "superseded"],
    }


def test_with_status() -> None:
    assert search.with_status(["text"], "active") == ("text",)
    assert search.with_status(("issue: X-1", "text"), "superseded") == ("issue: X-1", "text", "superseded")
    assert search.with_status([], "archived") == ("archived",)


# recall


def test_recall_lists_unread_then_exact_then_text_matches(conn: sqlite3.Connection) -> None:
    unread(conn, Corpus.PLANTS, "2026-09-09T10:00:00+03:00")

    result = search.recall(conn, RECALL, "ATLAS-27")

    assert result.unread == (RecallItem("unread", Corpus.PLANTS, "Water the plants", 0.0, ("unread",)),)
    assert {item.path for item in result.related[:2]} == {Corpus.KAFKA, Corpus.WEBSOCKET}
    assert paths_of(result.related[2:]) == [Corpus.RUSSIAN_KAFKA]
    by_path = {item.path: item for item in result.related}
    assert by_path[Corpus.KAFKA].reasons == ("issue: ATLAS-27", "text")
    assert by_path[Corpus.WEBSOCKET].reasons == ("issue: ATLAS-27", "text", "superseded")
    assert by_path[Corpus.RUSSIAN_KAFKA].reasons == ("text",)
    assert all(item.section == "related" for item in result.related)
    assert result.related[0].score > result.related[1].score > search.EXACT_BONUS > result.related[2].score
    assert result.items == [*result.unread, *result.related]


def test_recall_without_a_query_is_the_unread_section_alone(conn: sqlite3.Connection) -> None:
    unread(conn, Corpus.LIPS, "2026-09-15T18:00:00+03:00")
    unread(conn, Corpus.PLANTS, "2026-09-09T10:00:00+03:00")
    unread(conn, Corpus.PLANTS, "2026-09-12T10:00:00+03:00")

    assert paths_of(search.recall(conn, RECALL).unread) == [Corpus.LIPS, Corpus.PLANTS]
    assert search.recall(conn, RECALL).related == ()
    assert search.recall(conn, RECALL, "") == search.recall(conn, RECALL, None)


def test_recall_of_nothing_is_empty(conn: sqlite3.Connection) -> None:
    result = search.recall(conn, RECALL, "quantum entanglement", cwd="~/Dev/exampleco")

    assert result == RecallResult((), ())
    assert result.items == []


def test_recall_skips_deliveries_of_notes_that_are_not_indexed(conn: sqlite3.Connection) -> None:
    unread(conn, "notes/2026-01-01-gone.md", "2026-09-01T09:00:00+03:00")

    assert search.recall(conn, RECALL).unread == ()


def test_recall_marks_the_status_of_an_unread_note(conn: sqlite3.Connection) -> None:
    unread(conn, Corpus.LEGACY, "2026-09-01T09:00:00+03:00")

    assert search.recall(conn, RECALL).unread[0].reasons == ("unread", "archived")


def test_recall_never_lists_a_note_twice(conn: sqlite3.Connection) -> None:
    unread(conn, Corpus.KAFKA, "2026-09-01T09:00:00+03:00")

    result = search.recall(conn, RECALL, "ATLAS-27")

    assert paths_of(result.unread) == [Corpus.KAFKA]
    assert Corpus.KAFKA not in paths_of(result.related)


def test_recall_adds_the_path_boost_to_a_note_whose_paths_cover_the_cwd(conn: sqlite3.Connection) -> None:
    plain = {item.path: item for item in search.recall(conn, RECALL, "replay").related}
    boosted = {item.path: item for item in search.recall(conn, RECALL, "replay", cwd="~/Dev/exampleco/x").related}

    assert boosted[Corpus.RETENTION].score == pytest.approx(plain[Corpus.RETENTION].score + RECALL.path_boost)
    assert boosted[Corpus.RETENTION].reasons == ("text", "path")
    assert boosted[Corpus.KAFKA] == plain[Corpus.KAFKA]


def test_recall_boost_applies_to_exact_matches_too(conn: sqlite3.Connection) -> None:
    # All three notes matched by `ATLAS-27` carry the path `~/Dev/exampleco/project-atlas`.
    result = search.recall(conn, RECALL, "ATLAS-27", cwd="~/Dev/exampleco/project-atlas")

    by_path = {item.path: item for item in result.related}
    assert by_path[Corpus.KAFKA].reasons == ("issue: ATLAS-27", "text", "path")
    assert by_path[Corpus.WEBSOCKET].reasons == ("issue: ATLAS-27", "text", "path", "superseded")
    assert by_path[Corpus.RUSSIAN_KAFKA].reasons == ("text", "path")


def test_recall_path_alone_is_never_a_candidate(conn: sqlite3.Connection) -> None:
    assert search.recall(conn, RECALL, "kafka", cwd="~/Dev/orion").related != ()
    assert Corpus.ORION not in paths_of(search.recall(conn, RECALL, "kafka", cwd="~/Dev/orion").related)
    assert search.recall(conn, RECALL, None, cwd="~/Dev/orion") == RecallResult((), ())


def test_recall_drops_text_matches_below_min_score_but_keeps_exact_ones(conn: sqlite3.Connection) -> None:
    strict = RecallConfig(min_score=3.0)

    assert search.recall(conn, strict, "replay").related == ()
    assert {item.path for item in search.recall(conn, strict, "ATLAS-27").related} == {Corpus.KAFKA, Corpus.WEBSOCKET}
    assert search.recall(conn, RecallConfig(min_score=1.5), "replay", cwd="~/Dev/exampleco").related[0].path == (
        Corpus.RETENTION
    )


def test_recall_excludes_archived_and_superseded_text_matches(conn: sqlite3.Connection) -> None:
    assert paths_of(search.recall(conn, RECALL, "WebSocket").related) == [Corpus.KAFKA]
    assert search.recall(conn, RECALL, "nginx").related == ()


def test_recall_caps_the_total_at_the_limit_unread_first(conn: sqlite3.Connection) -> None:
    unread(conn, Corpus.PLANTS, "2026-09-09T10:00:00+03:00")
    unread(conn, Corpus.LIPS, "2026-09-08T10:00:00+03:00")

    two = search.recall(conn, RecallConfig(limit=2), "kafka")
    three = search.recall(conn, RecallConfig(limit=3), "kafka")
    one = search.recall(conn, RecallConfig(limit=1), "kafka")

    assert paths_of(two.items) == [Corpus.PLANTS, Corpus.LIPS]
    assert paths_of(three.items) == [Corpus.PLANTS, Corpus.LIPS, paths_of(search.search(conn, "kafka"))[0]]
    assert paths_of(one.items) == [Corpus.PLANTS]


def test_recall_as_data(conn: sqlite3.Connection, search_corpus: Path) -> None:
    item = search.recall(conn, RECALL, "legacy").related[0]

    assert search.recall_as_data(search_corpus, item) == {
        "section": "related",
        "reasons": ["tag: legacy", "text", "archived"],
        "path": Corpus.LEGACY,
        "abs_path": str(search_corpus / Corpus.LEGACY),
        "title": "Legacy proxy drops idle WebSocket connections",
    }
