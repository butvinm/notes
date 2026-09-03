"""Tests for the index read queries: rows with effective status and unread counts, ordering, filters, path matching."""

import json
import sqlite3
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from notes import db, queries


def insert_note(
    conn: sqlite3.Connection,
    path: str,
    *,
    kind: str = "decision",
    status: str = "active",
    created: str = "2026-09-02",
    paths: Sequence[str] = (),
    tags: Sequence[str] = (),
    schedule: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO notes (path, kind, status, title, body, schedule, paths, tags, keywords, refs,
                              created_date, content_hash, mtime_ns, size)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, '["kw"]', '["CLAUDE.md"]', ?, 'hash', 0, 0)""",
        (
            path,
            kind,
            status,
            f"Title of {path}",
            f"\n# Title of {path}\n",
            schedule,
            _encoded(paths),
            _encoded(tags),
            created,
        ),
    )
    conn.executemany("INSERT INTO note_tags VALUES (?, ?, ?)", [(path, tag, tag.lower()) for tag in tags])


def deliver(conn: sqlite3.Connection, path: str, occurrence: str, *, read_at: str | None = None) -> None:
    conn.execute(
        "INSERT INTO deliveries (path, occurrence_at, delivered_at, read_at) VALUES (?, ?, ?, ?)",
        (path, occurrence, occurrence, read_at),
    )


def paths_of(rows: Sequence[queries.NoteRow]) -> list[str]:
    return [row.path for row in rows]


def _encoded(items: Sequence[str]) -> str:
    return json.dumps(list(items))


@pytest.fixture
def conn(bare_vault: Path) -> Iterator[sqlite3.Connection]:
    connection = db.open_index(bare_vault)
    yield connection
    connection.close()


# get_note and relations_of


def test_get_note_carries_every_column_the_effective_status_and_the_unread_count(conn: sqlite3.Connection) -> None:
    insert_note(conn, "notes/a.md", paths=["~/Dev/x"], tags=["ATLAS-27"], schedule="at 2026-09-09T10:00:00+03:00")
    insert_note(conn, "notes/b.md")
    conn.execute("INSERT INTO relations VALUES ('notes/b.md', 'supersedes', 'notes/a.md')")
    deliver(conn, "notes/a.md", "2026-09-09T10:00:00+03:00")
    deliver(conn, "notes/a.md", "2026-09-12T10:00:00+03:00")
    deliver(conn, "notes/a.md", "2026-09-06T10:00:00+03:00", read_at="2026-09-06T11:00:00+03:00")

    row = queries.get_note(conn, "notes/a.md")

    assert row == queries.NoteRow(
        path="notes/a.md",
        kind="decision",
        status="active",
        effective_status="superseded",
        title="Title of notes/a.md",
        body="\n# Title of notes/a.md\n",
        schedule="at 2026-09-09T10:00:00+03:00",
        paths=("~/Dev/x",),
        tags=("ATLAS-27",),
        keywords=("kw",),
        references=("CLAUDE.md",),
        created_date="2026-09-02",
        unread=2,
    )


def test_get_note_of_an_unindexed_path_is_none(conn: sqlite3.Connection) -> None:
    assert queries.get_note(conn, "notes/missing.md") is None


def test_relations_of_lists_outgoing_relations_only(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO relations VALUES ('notes/new.md', 'supersedes', 'notes/old.md')")
    conn.execute("INSERT INTO relations VALUES ('notes/new.md', 'child', 'notes/part.md')")
    conn.execute("INSERT INTO relations VALUES ('notes/other.md', 'related', 'notes/new.md')")

    assert queries.relations_of(conn, "notes/new.md") == [
        queries.RelationRow("child", "notes/part.md"),
        queries.RelationRow("supersedes", "notes/old.md"),
    ]
    assert queries.relations_of(conn, "notes/old.md") == []


def test_referrers_of_lists_the_sources_pointing_at_a_note_once_each(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO relations VALUES ('notes/new.md', 'supersedes', 'notes/old.md')")
    conn.execute("INSERT INTO relations VALUES ('notes/new.md', 'related', 'notes/old.md')")
    conn.execute("INSERT INTO relations VALUES ('notes/archive/deep.md', 'child', 'notes/old.md')")
    conn.execute("INSERT INTO relations VALUES ('notes/old.md', 'related', 'notes/new.md')")

    assert queries.referrers_of(conn, "notes/old.md") == ["notes/archive/deep.md", "notes/new.md"]
    assert queries.referrers_of(conn, "notes/new.md") == ["notes/old.md"]
    assert queries.referrers_of(conn, "notes/archive/deep.md") == []


# list_notes


def test_list_orders_unread_first_then_newest_then_path(conn: sqlite3.Connection) -> None:
    insert_note(conn, "notes/2026-09-02-b.md", created="2026-09-02")
    insert_note(conn, "notes/2026-09-02-a.md", created="2026-09-02")
    insert_note(conn, "notes/2026-09-03-c.md", created="2026-09-03")
    insert_note(conn, "notes/2026-08-01-old.md", created="2026-08-01")
    insert_note(conn, "notes/2026-08-15-read.md", created="2026-08-15")
    deliver(conn, "notes/2026-08-01-old.md", "2026-09-01T09:00:00+03:00")
    deliver(conn, "notes/2026-08-15-read.md", "2026-09-01T09:00:00+03:00", read_at="2026-09-01T09:30:00+03:00")

    rows = queries.list_notes(conn)

    assert paths_of(rows) == [
        "notes/2026-08-01-old.md",
        "notes/2026-09-03-c.md",
        "notes/2026-09-02-a.md",
        "notes/2026-09-02-b.md",
        "notes/2026-08-15-read.md",
    ]
    assert [row.unread for row in rows] == [1, 0, 0, 0, 0]


def test_list_filters(conn: sqlite3.Connection) -> None:
    insert_note(conn, "notes/decision.md", tags=["ATLAS-27", "Kafka"])
    insert_note(conn, "notes/fact.md", kind="fact", tags=["kafka"])
    insert_note(conn, "notes/archived.md", kind="fact", status="archived")
    insert_note(conn, "notes/old.md")
    conn.execute("INSERT INTO relations VALUES ('notes/decision.md', 'supersedes', 'notes/old.md')")
    deliver(conn, "notes/fact.md", "2026-09-01T09:00:00+03:00")

    assert paths_of(queries.list_notes(conn, kind="fact")) == ["notes/fact.md", "notes/archived.md"]
    assert paths_of(queries.list_notes(conn, status="superseded")) == ["notes/old.md"]
    assert paths_of(queries.list_notes(conn, status="archived")) == ["notes/archived.md"]
    assert paths_of(queries.list_notes(conn, status="active")) == ["notes/fact.md", "notes/decision.md"]
    assert paths_of(queries.list_notes(conn, tag="KAFKA")) == ["notes/fact.md", "notes/decision.md"]
    assert paths_of(queries.list_notes(conn, tag="atlas-27")) == ["notes/decision.md"]
    assert paths_of(queries.list_notes(conn, unread=True)) == ["notes/fact.md"]
    assert paths_of(queries.list_notes(conn, unread=True, kind="decision")) == []
    assert paths_of(queries.list_notes(conn, kind="fact", tag="kafka", unread=True)) == ["notes/fact.md"]


def test_list_path_filter_keeps_notes_whose_entry_covers_the_path(conn: sqlite3.Connection, home: Path) -> None:
    insert_note(conn, "notes/project.md", paths=["~/Dev/exampleco/project-atlas"])
    insert_note(conn, "notes/org.md", paths=["~/Dev/exampleco", "~/Dev/other"])
    insert_note(conn, "notes/none.md")

    assert paths_of(queries.list_notes(conn, path="~/Dev/exampleco/project-atlas/src")) == [
        "notes/org.md",
        "notes/project.md",
    ]
    assert paths_of(queries.list_notes(conn, path=str(home / "Dev" / "exampleco"))) == ["notes/org.md"]
    assert paths_of(queries.list_notes(conn, path="~/Dev/other/x")) == ["notes/org.md"]
    assert paths_of(queries.list_notes(conn, path="~/Dev")) == []


# path matching


def test_absolute_expands_home_and_resolves_relative_paths(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = home / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    assert queries.absolute("~/Dev/x") == home / "Dev" / "x"
    assert queries.absolute("sub/../other") == work / "other"
    assert queries.absolute(".") == work
    assert queries.absolute(str(home / "Dev")) == home / "Dev"


def test_absolute_resolves_symlinks_on_both_sides(home: Path) -> None:
    real = home / "real"
    real.mkdir()
    (home / "link").symlink_to(real)

    assert queries.absolute("~/link/sub") == real / "sub"
    assert queries.covers("~/link", real / "sub")
    assert queries.covers("~/real", queries.absolute("~/link/sub"))


def test_covers_matches_equal_and_descendant_paths_only(home: Path) -> None:
    target = home / "Dev" / "exampleco" / "project-atlas"

    assert queries.covers("~/Dev/exampleco/project-atlas", target)
    assert queries.covers("~/Dev/exampleco", target)
    assert queries.covers("~", target)
    assert not queries.covers("~/Dev/exampleco/project-atlas/src", target)
    assert not queries.covers("~/Dev/exampleco/other-project", target)
    assert not queries.covers("~/Dev/orion", target)


# as_data


def test_as_data_lists_every_metadata_field(conn: sqlite3.Connection, bare_vault: Path) -> None:
    insert_note(conn, "notes/a.md", paths=["~/Dev/x"], tags=["ATLAS-27"])
    row = queries.get_note(conn, "notes/a.md")
    assert row is not None

    assert queries.as_data(bare_vault, row) == {
        "path": "notes/a.md",
        "abs_path": str(bare_vault / "notes/a.md"),
        "kind": "decision",
        "status": "active",
        "effective_status": "active",
        "title": "Title of notes/a.md",
        "created_date": "2026-09-02",
        "schedule": None,
        "paths": ["~/Dev/x"],
        "tags": ["ATLAS-27"],
        "keywords": ["kw"],
        "references": ["CLAUDE.md"],
        "unread": 0,
    }


# all_tags


def test_all_tags_lists_each_tag_once_ordered_by_its_lowercase_form(conn: sqlite3.Connection) -> None:
    insert_note(conn, "notes/a.md", tags=["sync-worker", "ATLAS-27"])
    insert_note(conn, "notes/b.md", tags=["kafka", "ATLAS-27"])
    insert_note(conn, "notes/c.md", tags=["Кафка"])

    assert queries.all_tags(conn) == ["ATLAS-27", "kafka", "sync-worker", "Кафка"]


def test_all_tags_spells_a_tag_the_way_most_notes_do(conn: sqlite3.Connection) -> None:
    insert_note(conn, "notes/a.md", tags=["Kafka"])
    insert_note(conn, "notes/b.md", tags=["kafka"])
    insert_note(conn, "notes/c.md", tags=["kafka"])

    assert queries.all_tags(conn) == ["kafka"]


def test_all_tags_breaks_a_spelling_tie_by_the_spelling_that_sorts_first(conn: sqlite3.Connection) -> None:
    insert_note(conn, "notes/a.md", tags=["kafka"])
    insert_note(conn, "notes/b.md", tags=["Kafka"])

    assert queries.all_tags(conn) == ["Kafka"]


def test_all_tags_of_an_empty_index(conn: sqlite3.Connection) -> None:
    assert queries.all_tags(conn) == []
