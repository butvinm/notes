"""Ranking and search over the index: FTS5 text matches, exact tag and issue-ID matches, and their combination.

Candidates are the notes whose FTS row matches the query's normalized tokens plus the notes carrying a tag equal to
one of the query's exact terms; nothing else is a candidate. A text match scores `-bm25` over the FTS columns with
the title, tags, keywords, and body weighted 5, 4, 4, and 1, so higher is better. Every exact match adds
`EXACT_BONUS` and a reason, `issue: ATLAS-27` for a tag shaped like a tracker key and `tag: sync-worker` for any
other tag, which puts a tag or issue-ID match above any text match. Ties are broken by path, so the order is
deterministic.

`search` is the assembly behind `notes search`: archived and superseded notes are left out unless they match an
exact term or the caller asks for everything, and the effective status becomes the last reason when it is not
`active`.

`recall` is the assembly behind `notes recall`, the bounded block the Claude Code hook injects: every unread delivery
first, then the exact matches of the prompt whatever their status, then the active text matches that reach
`recall.min_score`, each note once and at most `recall.limit` of them. A `paths` entry covering the working
directory adds `recall.path_boost` to a candidate's score, but never makes a note a candidate by itself.
"""

import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from notes import queries, textnorm
from notes.config import RecallConfig

EXACT_BONUS = 10.0
"""Added to the score for every exact tag or issue-ID match; more than a text match reaches in practice."""

TEXT_REASON = "text"
"""The reason of an FTS match, listed after the exact-match reasons."""

PATH_REASON = "path"
"""The reason added by `recall` when a `paths` entry of the note covers the working directory."""

UNREAD_REASON = "unread"
"""The reason of a note listed by `recall` for its unread delivery."""

UNREAD_SECTION = "unread"
RELATED_SECTION = "related"
"""The two sections of the recall block: unread deliveries, then exact and text matches of the prompt."""

ACTIVE = "active"

_TEXT_SQL = "SELECT path, -bm25(notes_fts, 0, 5.0, 4.0, 4.0, 1.0) AS score FROM notes_fts WHERE notes_fts MATCH ?"
"""The FTS score, negated so that higher is better; one weight per column: path (unindexed), title, tags, keywords,
body."""


def text_matches(conn: sqlite3.Connection, query: str) -> dict[str, float]:
    """The notes whose FTS row matches the query, with their text score (higher is better).

    The query goes through `textnorm.fts_query`, so an inflected Russian word finds a note written in another
    inflection and FTS5 syntax in the input is inert. A query with nothing searchable (empty, or stopwords only)
    matches nothing.
    """
    match = textnorm.fts_query(query)
    if not match:
        return {}
    return {row["path"]: row["score"] for row in conn.execute(_TEXT_SQL, (match,))}


def exact_matches(conn: sqlite3.Connection, terms: Iterable[str]) -> dict[str, list[str]]:
    """The notes carrying a tag equal to one of `terms` (case-insensitively), with one reason per matched tag.

    Reasons follow the order of `terms` and spell the tag as the note does: `issue: ATLAS-27` for a tag shaped like
    a tracker key, `tag: sync-worker` for any other.
    """
    wanted: dict[str, int] = {}
    for term in terms:
        wanted.setdefault(term.lower(), len(wanted))
    if not wanted:
        return {}
    placeholders = ", ".join("?" for _ in wanted)
    rows = conn.execute(f"SELECT path, tag, tag_lower FROM note_tags WHERE tag_lower IN ({placeholders})", list(wanted))
    found: dict[str, list[tuple[int, str]]] = {}
    for row in rows:
        found.setdefault(row["path"], []).append((wanted[row["tag_lower"]], exact_reason(row["tag"])))
    return {path: [reason for _, reason in sorted(hits)] for path, hits in found.items()}


def exact_reason(tag: str) -> str:
    """`issue: <tag>` when the tag looks like a tracker key, otherwise `tag: <tag>`."""
    return f"issue: {tag}" if textnorm.is_issue_id(tag) else f"tag: {tag}"


@dataclass(frozen=True)
class Ranked:
    """A candidate with its combined score and reasons: the exact-match reasons, then `text` for an FTS match."""

    path: str
    score: float
    reasons: tuple[str, ...]


def rank(text_scores: Mapping[str, float], exact: Mapping[str, Sequence[str]]) -> list[Ranked]:
    """Combine text scores and exact matches: `EXACT_BONUS` per exact reason plus the text score, best first.

    Every path in either mapping is a candidate. Equal scores are ordered by path, so the result is deterministic.
    """
    ranked = []
    for path in text_scores.keys() | exact.keys():
        reasons = list(exact.get(path, ()))
        score = EXACT_BONUS * len(reasons)
        if path in text_scores:
            score += text_scores[path]
            reasons.append(TEXT_REASON)
        ranked.append(Ranked(path, score, tuple(reasons)))
    ranked.sort(key=lambda item: (-item.score, item.path))
    return ranked


@dataclass(frozen=True)
class SearchResult:
    """One result of `notes search`: the note, its effective status, its score, and why it matched.

    `reasons` are the exact-match reasons, then `text` for an FTS match, then the effective status when it is not
    `active`.
    """

    path: str
    title: str
    effective_status: str
    score: float
    reasons: tuple[str, ...]


def search(
    conn: sqlite3.Connection, query: str, *, limit: int | None = None, include_all: bool = False
) -> list[SearchResult]:
    """The ranked results of a search, at most `limit` of them.

    Archived and superseded notes are omitted unless they match an exact term or `include_all` is set; the limit
    counts the results that remain, not the candidates.
    """
    exact = exact_matches(conn, textnorm.exact_terms(query))
    ranked = rank(text_matches(conn, query), exact)
    if not ranked:
        return []
    rows = _rows(conn, [item.path for item in ranked])
    results: list[SearchResult] = []
    for item in ranked:
        row = rows.get(item.path)
        if row is None:
            # A sync from another process (`tick`) removed the note between the FTS query and this one.
            continue
        status = row["effective_status"]
        if status != ACTIVE and not (include_all or item.path in exact):
            continue
        results.append(SearchResult(item.path, row["title"], status, item.score, with_status(item.reasons, status)))
        if limit is not None and len(results) >= limit:
            break
    return results


def with_status(reasons: Sequence[str], status: str) -> tuple[str, ...]:
    """The reasons with the effective status appended when it is not `active`."""
    return tuple(reasons) if status == ACTIVE else (*reasons, status)


def as_data(vault: Path, result: SearchResult) -> dict[str, Any]:
    """The JSON shape of one result: `path`, `abs_path`, `title`, `effective_status`, `score`, `reasons`."""
    return {
        "path": result.path,
        "abs_path": str(vault / result.path),
        "title": result.title,
        "effective_status": result.effective_status,
        "score": round(result.score, 3),
        "reasons": list(result.reasons),
    }


@dataclass(frozen=True)
class RecallItem:
    """One line of the recall block: its section, the note, its score within the section, and why it is listed.

    `reasons` are `unread` for the unread section; otherwise the exact-match reasons, then `text` for an FTS
    match, then `path` when the working directory boosted the note. The effective status comes last when it is
    not `active`.
    """

    section: str
    path: str
    title: str
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class RecallResult:
    """The recall block: the unread section, then the related section, each in the order it is printed."""

    unread: tuple[RecallItem, ...]
    related: tuple[RecallItem, ...]

    @property
    def items(self) -> list[RecallItem]:
        """Every listed note in printing order; empty when there is nothing to inject."""
        return [*self.unread, *self.related]


def recall(
    conn: sqlite3.Connection, config: RecallConfig, query: str | None = None, cwd: str | None = None
) -> RecallResult:
    """The bounded block for a prompt: unread deliveries, then exact matches, then active text matches.

    Unread deliveries come newest first, whatever the note's status; without a `query` they are the whole block.
    Exact matches keep their status with the status as the last reason; text matches must be active and reach
    `config.min_score`. A note appears once, in the first section that lists it, and the total is capped at
    `config.limit`. `cwd` adds `config.path_boost` to every candidate with a `paths` entry equal to it or above it.
    """
    unread = _unread_items(conn, config.limit)
    remaining = config.limit - len(unread)
    related: list[RecallItem] = []
    if query and remaining > 0:
        related = _related_items(conn, config, query, cwd, {item.path for item in unread}, remaining)
    return RecallResult(tuple(unread), tuple(related))


def recall_as_data(vault: Path, item: RecallItem) -> dict[str, Any]:
    """The JSON shape of one recall line: `section`, `reasons`, `path`, `abs_path`, `title`."""
    return {
        "section": item.section,
        "reasons": list(item.reasons),
        "path": item.path,
        "abs_path": str(vault / item.path),
        "title": item.title,
    }


_UNREAD_SQL = """
SELECT deliveries.path, deliveries.delivered_at, notes_view.title, notes_view.effective_status
  FROM deliveries JOIN notes_view ON notes_view.path = deliveries.path
 WHERE deliveries.read_at IS NULL
"""
"""Every unread delivery of an indexed note; a delivery of a deleted or currently invalid note has nothing to show."""


def _unread_items(conn: sqlite3.Connection, limit: int) -> list[RecallItem]:
    """The notes with an unread delivery, newest delivery first (ties by path), each once, at most `limit`.

    Timestamps are compared as instants rather than as text, so deliveries recorded under different UTC offsets
    still order by time.
    """
    newest: dict[str, tuple[datetime, str, str]] = {}
    for row in conn.execute(_UNREAD_SQL):
        delivered = datetime.fromisoformat(row["delivered_at"])
        current = newest.get(row["path"])
        if current is None or delivered > current[0]:
            newest[row["path"]] = (delivered, row["title"], row["effective_status"])
    ordered = sorted(newest.items(), key=lambda entry: (-entry[1][0].timestamp(), entry[0]))
    return [
        RecallItem(UNREAD_SECTION, path, title, 0.0, with_status((UNREAD_REASON,), status))
        for path, (_, title, status) in ordered[:limit]
    ]


def _related_items(
    conn: sqlite3.Connection, config: RecallConfig, query: str, cwd: str | None, seen: set[str], limit: int
) -> list[RecallItem]:
    """The related section: exact matches of any status, then active text matches at or above `min_score`.

    Both groups are ordered by boosted score, best first, ties by path; notes in `seen` are skipped.
    """
    exact = exact_matches(conn, textnorm.exact_terms(query))
    ranked = rank(text_matches(conn, query), exact)
    if not ranked:
        return []
    rows = _rows(conn, [item.path for item in ranked])
    target = queries.absolute(cwd) if cwd else None
    by_exact: list[RecallItem] = []
    by_text: list[RecallItem] = []
    for item in ranked:
        row = rows.get(item.path)
        if row is None or item.path in seen:
            continue
        score, reasons = item.score, item.reasons
        if target is not None and _covers_target(row["paths"], target):
            score += config.path_boost
            reasons = (*reasons, PATH_REASON)
        status = row["effective_status"]
        if item.path in exact:
            by_exact.append(RecallItem(RELATED_SECTION, item.path, row["title"], score, with_status(reasons, status)))
        elif status == ACTIVE and score >= config.min_score:
            by_text.append(RecallItem(RELATED_SECTION, item.path, row["title"], score, reasons))
    ordered = [*sorted(by_exact, key=_best_first), *sorted(by_text, key=_best_first)]
    return ordered[:limit]


def _best_first(item: RecallItem) -> tuple[float, str]:
    return (-item.score, item.path)


def _covers_target(paths_json: str, target: Path) -> bool:
    """Whether any entry of the note's `paths` (a JSON array) equals `target` or is one of its ancestors."""
    return any(queries.covers(entry, target) for entry in json.loads(paths_json))


def _rows(conn: sqlite3.Connection, paths: Sequence[str]) -> dict[str, sqlite3.Row]:
    placeholders = ", ".join("?" for _ in paths)
    rows = conn.execute(
        f"SELECT path, title, effective_status, paths FROM notes_view WHERE path IN ({placeholders})", list(paths)
    )
    return {row["path"]: row for row in rows}
