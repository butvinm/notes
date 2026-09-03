"""Tests for the SQLite index: connection settings, schema creation, derived-table rebuilds, and effective status."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from notes import db

DERIVED = {"meta", "notes", "note_tags", "relations", "invalid_files", "notes_fts"}


def object_names(conn: sqlite3.Connection, kind: str) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = ? AND name NOT LIKE 'sqlite_%'", (kind,))
    return {row["name"] for row in rows}


def insert_note(conn: sqlite3.Connection, path: str, *, status: str = "active") -> None:
    conn.execute(
        """INSERT INTO notes (path, kind, status, title, body, schedule, paths, tags, keywords, refs,
                              created_date, content_hash, mtime_ns, size)
           VALUES (?, 'decision', ?, 'Title', '', NULL, '[]', '[]', '[]', '[]', '2026-09-02', 'hash', 0, 0)""",
        (path, status),
    )


def effective_statuses(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT path, effective_status FROM notes_view ORDER BY path")
    return {row["path"]: row["effective_status"] for row in rows}


@pytest.fixture
def conn(bare_vault: Path) -> Iterator[sqlite3.Connection]:
    connection = db.open_index(bare_vault)
    yield connection
    connection.close()


def test_connect_creates_the_database_inside_the_vault(bare_vault: Path) -> None:
    connection = db.connect(bare_vault)
    connection.close()

    assert (bare_vault / "index.sqlite").is_file()


def test_connection_settings(bare_vault: Path) -> None:
    connection = db.connect(bare_vault)

    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert connection.execute("SELECT 1 AS one").fetchone()["one"] == 1
    assert not connection.in_transaction
    connection.close()


def test_fresh_database_has_every_table_and_the_view(conn: sqlite3.Connection) -> None:
    assert DERIVED | {"deliveries"} <= object_names(conn, "table")
    assert object_names(conn, "view") == {"notes_view"}
    assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == str(db.SCHEMA_VERSION)
    assert db.stored_version(conn) == db.SCHEMA_VERSION


def test_ensure_schema_builds_once(bare_vault: Path) -> None:
    connection = db.connect(bare_vault)

    assert db.ensure_schema(connection) is True
    assert db.ensure_schema(connection) is False
    connection.close()


def test_stored_version_of_an_unrecognizable_database(bare_vault: Path) -> None:
    connection = db.connect(bare_vault)
    assert db.stored_version(connection) is None

    connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    assert db.stored_version(connection) is None

    connection.execute("INSERT INTO meta VALUES ('schema_version', 'garbage')")
    assert db.stored_version(connection) is None
    assert db.ensure_schema(connection) is True
    assert db.stored_version(connection) == db.SCHEMA_VERSION
    connection.close()


def test_fts5_stems_latin_and_ignores_diacritics(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO notes_fts (path, title, tags, keywords, body) VALUES (?, ?, ?, ?, ?)",
        ("notes/a.md", "Café tasks", "sync-worker", "Atlas", "Updates are running over Kafka"),
    )

    assert conn.execute("SELECT path FROM notes_fts WHERE notes_fts MATCH 'task'").fetchone()[0] == "notes/a.md"
    assert conn.execute("SELECT path FROM notes_fts WHERE notes_fts MATCH 'cafe'").fetchone()[0] == "notes/a.md"
    assert conn.execute("SELECT path FROM notes_fts WHERE notes_fts MATCH 'run'").fetchone()[0] == "notes/a.md"
    assert conn.execute("SELECT path FROM notes_fts WHERE notes_fts MATCH 'websocket'").fetchone() is None
    score = conn.execute(
        "SELECT bm25(notes_fts, 0, 5.0, 4.0, 4.0, 1.0) FROM notes_fts WHERE notes_fts MATCH 'kafka'"
    ).fetchone()[0]
    assert score < 0


def test_version_bump_keeps_deliveries_and_recreates_derived_tables(
    bare_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = db.open_index(bare_vault)
    insert_note(connection, "notes/a.md")
    connection.execute("INSERT INTO note_tags VALUES ('notes/a.md', 'Kafka', 'kafka')")
    connection.execute("INSERT INTO relations VALUES ('notes/a.md', 'related', 'notes/b.md')")
    connection.execute("INSERT INTO invalid_files VALUES ('notes/bad.md', 'hash', 0, 0, '[]')")
    connection.execute(
        "INSERT INTO notes_fts (path, title, tags, keywords, body) VALUES ('notes/a.md', 't', '', '', '')"
    )
    connection.execute(
        "INSERT INTO deliveries (path, occurrence_at, delivered_at, read_at) VALUES (?, ?, ?, NULL)",
        ("notes/a.md", "2026-09-02T10:00:00+03:00", "2026-09-02T10:00:30+03:00"),
    )
    connection.close()

    monkeypatch.setattr(db, "SCHEMA_VERSION", db.SCHEMA_VERSION + 1)
    connection = db.connect(bare_vault)
    assert db.ensure_schema(connection) is True

    for table in DERIVED - {"meta"}:
        assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table
    assert db.stored_version(connection) == db.SCHEMA_VERSION
    assert object_names(connection, "view") == {"notes_view"}
    delivery = connection.execute("SELECT path, occurrence_at, delivered_at, read_at FROM deliveries").fetchone()
    assert tuple(delivery) == ("notes/a.md", "2026-09-02T10:00:00+03:00", "2026-09-02T10:00:30+03:00", None)
    connection.close()


def test_matching_version_leaves_rows_alone(bare_vault: Path) -> None:
    connection = db.open_index(bare_vault)
    insert_note(connection, "notes/a.md")
    connection.close()

    connection = db.open_index(bare_vault)
    assert connection.execute("SELECT path FROM notes").fetchone()[0] == "notes/a.md"
    connection.close()


def test_deliveries_are_unique_per_occurrence(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO deliveries (path, occurrence_at, delivered_at) VALUES ('notes/a.md', 't1', 'now')")

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO deliveries (path, occurrence_at, delivered_at) VALUES ('notes/a.md', 't1', 'later')")
    conn.execute("INSERT INTO deliveries (path, occurrence_at, delivered_at) VALUES ('notes/a.md', 't2', 'later')")
    assert conn.execute("SELECT count(*) FROM deliveries").fetchone()[0] == 2


def test_effective_status_for_every_combination(conn: sqlite3.Connection) -> None:
    insert_note(conn, "notes/active.md")
    insert_note(conn, "notes/archived.md", status="archived")
    insert_note(conn, "notes/superseded.md")
    insert_note(conn, "notes/archived-and-superseded.md", status="archived")
    insert_note(conn, "notes/only-related.md")
    insert_note(conn, "notes/newer.md")
    conn.execute("INSERT INTO relations VALUES ('notes/newer.md', 'supersedes', 'notes/superseded.md')")
    conn.execute("INSERT INTO relations VALUES ('notes/newer.md', 'supersedes', 'notes/archived-and-superseded.md')")
    conn.execute("INSERT INTO relations VALUES ('notes/newer.md', 'related', 'notes/only-related.md')")
    conn.execute("INSERT INTO relations VALUES ('notes/newer.md', 'child', 'notes/active.md')")

    assert effective_statuses(conn) == {
        "notes/active.md": "active",
        "notes/archived.md": "archived",
        "notes/superseded.md": "superseded",
        "notes/archived-and-superseded.md": "archived",
        "notes/only-related.md": "active",
        "notes/newer.md": "active",
    }


def test_effective_status_sql_composes_into_other_queries(conn: sqlite3.Connection) -> None:
    insert_note(conn, "notes/old.md")
    insert_note(conn, "notes/new.md")
    conn.execute("INSERT INTO relations VALUES ('notes/new.md', 'supersedes', 'notes/old.md')")

    rows = conn.execute(f"SELECT path FROM notes WHERE {db.EFFECTIVE_STATUS_SQL} = 'superseded'").fetchall()
    assert [row["path"] for row in rows] == ["notes/old.md"]


def test_transaction_commits_on_success(conn: sqlite3.Connection) -> None:
    with db.transaction(conn):
        insert_note(conn, "notes/a.md")
        assert conn.in_transaction

    assert not conn.in_transaction
    assert conn.execute("SELECT count(*) FROM notes").fetchone()[0] == 1


def test_transaction_rolls_back_on_error(conn: sqlite3.Connection) -> None:
    with pytest.raises(RuntimeError, match="boom"), db.transaction(conn):
        insert_note(conn, "notes/a.md")
        raise RuntimeError("boom")

    assert not conn.in_transaction
    assert conn.execute("SELECT count(*) FROM notes").fetchone()[0] == 0


def test_rebuild_is_atomic(bare_vault: Path) -> None:
    """A failure in the middle of a rebuild (here: the view creation is denied) leaves the previous schema and rows."""
    connection = db.open_index(bare_vault)
    insert_note(connection, "notes/a.md")

    def deny_views(action: int, *_: object) -> int:
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_CREATE_VIEW else sqlite3.SQLITE_OK

    connection.set_authorizer(deny_views)
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        db.rebuild_derived(connection)
    connection.set_authorizer(None)

    assert not connection.in_transaction
    assert connection.execute("SELECT path FROM notes").fetchone()[0] == "notes/a.md"
    assert db.stored_version(connection) == db.SCHEMA_VERSION
    assert object_names(connection, "view") == {"notes_view"}
    connection.close()
