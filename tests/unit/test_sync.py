"""Tests for the incremental sync engine: indexing, skipping, invalidation, removal, and re-validation."""

import json
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from notes import db, document, sync, textnorm
from notes.document import ValidationError
from notes.sync import InvalidFile, SyncReport

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "notes"
KAFKA = "2026-09-02-project-atlas-kafka-task-updates.md"
WEBSOCKET = "2026-08-27-project-atlas-websocket.md"
CYRILLIC = "2026-09-01-обновления-задач-через-kafka.md"
NO_KIND = "---\nstatus: active\n---\n\n# No kind\n"
SECOND = 10**9


def note_text(kind: str = "decision", title: str = "A title", extra: str = "", body: str = "") -> str:
    return f"---\nkind: {kind}\nstatus: active\n{extra}---\n\n# {title}\n{body}"


def write(vault: Path, name: str, text: str) -> str:
    """Write `<vault>/notes/<name>` and return its ID; a rewrite bumps the mtime so a coarse clock cannot hide it."""
    target = vault / "notes" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    previous = target.stat().st_mtime_ns if target.exists() else None
    target.write_text(text, encoding="utf-8")
    if previous is not None:
        os.utime(target, ns=(previous + SECOND, previous + SECOND))
    return f"notes/{name}"


def install(vault: Path, name: str) -> str:
    return write(vault, name, (FIXTURES / name).read_text(encoding="utf-8"))


def touch_later(path: Path, seconds: int = 5) -> None:
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + seconds * SECOND))


def rows(conn: sqlite3.Connection, sql: str, *params: object) -> list[tuple[object, ...]]:
    return [tuple(row) for row in conn.execute(sql, params)]


def paths(conn: sqlite3.Connection, table: str) -> list[str]:
    column = "src" if table == "relations" else "path"
    return [row[0] for row in conn.execute(f"SELECT DISTINCT {column} FROM {table} ORDER BY 1")]


def effective_status(conn: sqlite3.Connection, path: str) -> str:
    return conn.execute("SELECT effective_status FROM notes_view WHERE path = ?", (path,)).fetchone()[0]


@pytest.fixture
def conn(vault: Path) -> Iterator[sqlite3.Connection]:
    connection = db.open_index(vault)
    yield connection
    connection.close()


@pytest.fixture
def parse_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the path of every file the engine parses."""
    calls: list[str] = []
    real = document.parse

    def counting(path: str, text: str) -> document.Document:
        calls.append(path)
        return real(path, text)

    monkeypatch.setattr(document, "parse", counting)
    return calls


# Indexing


def test_empty_vault_yields_an_empty_report(vault: Path, conn: sqlite3.Connection) -> None:
    assert sync.run(vault, conn) == SyncReport((), (), (), 0)
    assert sync.run(vault, conn) == sync.EMPTY_REPORT


def test_new_file_is_indexed_with_every_derived_row(vault: Path, conn: sqlite3.Connection) -> None:
    websocket = install(vault, WEBSOCKET)
    path = install(vault, KAFKA)

    report = sync.run(vault, conn)

    assert report == SyncReport((websocket, path), (), (), 0)
    assert report.note_count == 2
    row = conn.execute("SELECT * FROM notes WHERE path = ?", (path,)).fetchone()
    assert (row["kind"], row["status"]) == ("decision", "active")
    assert row["title"] == "Project Atlas task updates over Kafka"
    assert row["body"].startswith("\n# Project Atlas task updates over Kafka\n")
    assert json.loads(row["paths"]) == ["~/Dev/exampleco/project-atlas"]
    assert json.loads(row["tags"]) == ["ATLAS-27", "project-atlas", "sync-worker"]
    assert json.loads(row["keywords"])[:3] == ["Kafka", "tasks", "runs"]
    assert json.loads(row["refs"]) == ["CLAUDE.md", "https://tracker.example/browse/ATLAS-27"]
    assert row["schedule"] == "at 2026-09-09T10:00:00+03:00"
    assert row["created_date"] == "2026-09-02"
    stat = (vault / path).stat()
    assert (row["mtime_ns"], row["size"]) == (stat.st_mtime_ns, stat.st_size)
    assert row["content_hash"] == document.content_hash((vault / path).read_text(encoding="utf-8"))
    assert rows(conn, "SELECT tag, tag_lower FROM note_tags WHERE path = ? ORDER BY tag", path) == [
        ("ATLAS-27", "atlas-27"),
        ("project-atlas", "project-atlas"),
        ("sync-worker", "sync-worker"),
    ]
    assert rows(conn, "SELECT relation, dst FROM relations WHERE src = ?", path) == [("supersedes", websocket)]
    assert effective_status(conn, websocket) == "superseded"
    assert paths(conn, "invalid_files") == []


def test_fts_rows_hold_normalized_text(vault: Path, conn: sqlite3.Connection) -> None:
    install(vault, WEBSOCKET)
    path = install(vault, KAFKA)

    sync.run(vault, conn)

    fts = conn.execute("SELECT title, tags, keywords, body FROM notes_fts WHERE path = ?", (path,)).fetchone()
    assert fts["title"] == "project atlas task updates over kafka"
    assert fts["tags"] == "atlas 27 project atlas sync worker"
    assert fts["keywords"].split()[:4] == ["kafka", "tasks", "runs", "launches"]
    assert "websocket" in fts["body"].split()
    assert conn.execute("SELECT path FROM notes_fts WHERE notes_fts MATCH 'proxy'").fetchone()[0] == path


def test_cyrillic_note_is_lemmatized_and_found_in_another_inflection(vault: Path, conn: sqlite3.Connection) -> None:
    path = install(vault, CYRILLIC)

    sync.run(vault, conn)

    fts = conn.execute("SELECT title, keywords FROM notes_fts WHERE path = ?", (path,)).fetchone()
    assert fts["title"].split()[:2] == ["обновление", "задача"]
    assert "очередь" in fts["keywords"].split()
    query = textnorm.fts_query("задачами")
    assert conn.execute("SELECT path FROM notes_fts WHERE notes_fts MATCH ?", (query,)).fetchone()[0] == path


def test_note_in_a_subdirectory_is_indexed_with_relative_targets(vault: Path, conn: sqlite3.Connection) -> None:
    websocket = install(vault, WEBSOCKET)
    extra = f"related:\n  - relation: related\n    note: ../{WEBSOCKET}\n"
    path = write(vault, "projects/2026-09-02-sub.md", note_text(extra=extra))

    report = sync.run(vault, conn)

    assert report.changed == (websocket, path)
    assert rows(conn, "SELECT relation, dst FROM relations WHERE src = ?", path) == [("related", websocket)]
    assert conn.execute("SELECT created_date FROM notes WHERE path = ?", (path,)).fetchone()[0] == "2026-09-02"


def test_tags_that_differ_only_in_case_collapse_into_one_row(vault: Path, conn: sqlite3.Connection) -> None:
    path = write(vault, "2026-09-02-a.md", note_text(extra="tags: [Kafka, kafka, KAFKA]\n"))

    sync.run(vault, conn)

    assert rows(conn, "SELECT tag, tag_lower FROM note_tags WHERE path = ?", path) == [("Kafka", "kafka")]
    assert json.loads(conn.execute("SELECT tags FROM notes").fetchone()[0]) == ["Kafka", "kafka", "KAFKA"]


# Skipping unchanged files


def test_unchanged_file_is_skipped_without_parsing(
    vault: Path, conn: sqlite3.Connection, parse_calls: list[str]
) -> None:
    path = write(vault, "2026-09-02-a.md", note_text())
    sync.run(vault, conn)
    assert parse_calls == [path]

    report = sync.run(vault, conn)

    assert parse_calls == [path]
    assert report == SyncReport((), (), (), 1)


def test_touched_file_with_same_content_updates_stat_only(
    vault: Path, conn: sqlite3.Connection, parse_calls: list[str]
) -> None:
    path = write(vault, "2026-09-02-a.md", note_text())
    sync.run(vault, conn)
    before = conn.execute("SELECT mtime_ns FROM notes WHERE path = ?", (path,)).fetchone()[0]
    touch_later(vault / path)
    parse_calls.clear()

    report = sync.run(vault, conn)

    assert parse_calls == []
    assert report == SyncReport((), (), (), 1)
    after = conn.execute("SELECT mtime_ns FROM notes WHERE path = ?", (path,)).fetchone()[0]
    assert after == before + 5 * SECOND
    assert after == (vault / path).stat().st_mtime_ns


def test_changed_content_is_reindexed(vault: Path, conn: sqlite3.Connection) -> None:
    path = write(vault, "2026-09-02-a.md", note_text(title="Old title", extra="tags: [old]\n", body="\nOld body.\n"))
    sync.run(vault, conn)

    write(vault, "2026-09-02-a.md", note_text(title="New title", extra="tags: [new]\n", body="\nNew body here.\n"))
    report = sync.run(vault, conn)

    assert report == SyncReport((path,), (), (), 0)
    assert conn.execute("SELECT title FROM notes WHERE path = ?", (path,)).fetchone()[0] == "New title"
    assert rows(conn, "SELECT tag FROM note_tags WHERE path = ?", path) == [("new",)]
    assert conn.execute("SELECT path FROM notes_fts WHERE notes_fts MATCH 'old'").fetchone() is None
    assert conn.execute("SELECT path FROM notes_fts WHERE notes_fts MATCH 'new'").fetchone()[0] == path
    assert conn.execute("SELECT count(*) FROM notes_fts").fetchone()[0] == 1


# Invalid files


def test_invalid_file_is_excluded_and_recorded_with_errors(vault: Path, conn: sqlite3.Connection) -> None:
    websocket = install(vault, WEBSOCKET)
    bad = write(vault, "2026-09-02-bad.md", NO_KIND)

    report = sync.run(vault, conn)

    assert report == SyncReport((websocket,), (), (InvalidFile(bad, (ValidationError(1, "kind is required"),)),), 0)
    assert report.invalid_paths == (bad,)
    assert paths(conn, "notes") == [websocket]
    assert paths(conn, "notes_fts") == [websocket]
    row = conn.execute("SELECT * FROM invalid_files").fetchone()
    assert row["path"] == bad
    assert json.loads(row["errors"]) == [{"line": 1, "message": "kind is required"}]
    assert row["content_hash"] == document.content_hash(NO_KIND)
    stat = (vault / bad).stat()
    assert (row["mtime_ns"], row["size"]) == (stat.st_mtime_ns, stat.st_size)


def test_fixing_an_invalid_file_removes_its_record(vault: Path, conn: sqlite3.Connection) -> None:
    bad = write(vault, "2026-09-02-bad.md", NO_KIND)
    sync.run(vault, conn)

    write(vault, "2026-09-02-bad.md", note_text(title="No kind"))
    report = sync.run(vault, conn)

    assert report == SyncReport((bad,), (), (), 0)
    assert paths(conn, "invalid_files") == []
    assert paths(conn, "notes") == [bad]


def test_valid_file_that_becomes_invalid_loses_every_derived_row(vault: Path, conn: sqlite3.Connection) -> None:
    websocket = install(vault, WEBSOCKET)
    path = install(vault, KAFKA)
    sync.run(vault, conn)

    write(vault, KAFKA, "---\nkind: decision\nstatus: bogus\n---\n\n# Broken now\n")
    report = sync.run(vault, conn)

    assert report.changed == ()
    assert report.invalid_paths == (path,)
    assert report.invalid[0].errors == (ValidationError(3, "status must be active or archived"),)
    for table in ("notes", "note_tags", "notes_fts"):
        assert paths(conn, table) == [websocket], table
    assert paths(conn, "relations") == []
    assert paths(conn, "invalid_files") == [path]
    assert effective_status(conn, websocket) == "active"


def test_invalid_files_are_reread_every_run_while_valid_files_are_not(
    vault: Path, conn: sqlite3.Connection, parse_calls: list[str]
) -> None:
    write(vault, "2026-09-02-good.md", note_text())
    bad = write(vault, "2026-09-02-bad.md", NO_KIND)
    sync.run(vault, conn)
    parse_calls.clear()

    for _ in range(2):
        report = sync.run(vault, conn)
        assert report == SyncReport((), (), (InvalidFile(bad, (ValidationError(1, "kind is required"),)),), 1)
    assert parse_calls == [bad, bad]


def test_undecodable_file_is_invalid(vault: Path, conn: sqlite3.Connection) -> None:
    target = vault / "notes" / "2026-09-02-bin.md"
    target.write_bytes(b"---\nkind: decision\nstatus: active\n---\n\n# \xff\xfe\n")

    report = sync.run(vault, conn)

    assert report.invalid_paths == ("notes/2026-09-02-bin.md",)
    (error,) = report.invalid[0].errors
    assert error.line is None
    assert error.message.startswith("file is not valid UTF-8")
    assert paths(conn, "notes") == []


# Removed files and their referrers


def test_removed_file_drops_every_row(vault: Path, conn: sqlite3.Connection) -> None:
    websocket = install(vault, WEBSOCKET)
    path = install(vault, KAFKA)
    bad = write(vault, "2026-09-02-bad.md", NO_KIND)
    sync.run(vault, conn)

    (vault / path).unlink()
    (vault / bad).unlink()
    report = sync.run(vault, conn)

    assert report == SyncReport((), (bad, path), (), 1)
    for table in ("notes", "note_tags", "notes_fts"):
        assert paths(conn, table) == [websocket], table
    assert paths(conn, "relations") == []
    assert paths(conn, "invalid_files") == []
    assert effective_status(conn, websocket) == "active"


def test_removing_a_relation_target_invalidates_the_referrer_and_restoring_it_recovers(
    vault: Path, conn: sqlite3.Connection, parse_calls: list[str]
) -> None:
    websocket = install(vault, WEBSOCKET)
    kafka = install(vault, KAFKA)
    sync.run(vault, conn)
    parse_calls.clear()

    (vault / websocket).unlink()
    report = sync.run(vault, conn)

    assert report.removed == (websocket,)
    assert report.changed == ()
    assert report.invalid_paths == (kafka,)
    (error,) = report.invalid[0].errors
    assert error.line == 12
    assert f"target `{WEBSOCKET}` does not exist" in error.message
    assert paths(conn, "notes") == []
    assert paths(conn, "relations") == []
    assert paths(conn, "invalid_files") == [kafka]
    assert parse_calls == [kafka]

    install(vault, WEBSOCKET)
    parse_calls.clear()
    report = sync.run(vault, conn)

    assert report == SyncReport((websocket, kafka), (), (), 0)
    assert paths(conn, "notes") == [websocket, kafka]
    assert paths(conn, "invalid_files") == []
    assert rows(conn, "SELECT relation, dst FROM relations WHERE src = ?", kafka) == [("supersedes", websocket)]
    assert effective_status(conn, websocket) == "superseded"
    assert sorted(parse_calls) == [websocket, kafka]


def test_referrer_changed_in_the_same_run_as_the_removal_is_handled_once(
    vault: Path, conn: sqlite3.Connection, parse_calls: list[str]
) -> None:
    websocket = install(vault, WEBSOCKET)
    kafka = install(vault, KAFKA)
    sync.run(vault, conn)
    parse_calls.clear()

    (vault / websocket).unlink()
    write(vault, KAFKA, note_text(title="No relations any more"))
    report = sync.run(vault, conn)

    assert report == SyncReport((kafka,), (websocket,), (), 0)
    assert parse_calls == [kafka]


# Deliveries and atomicity


def test_deliveries_survive_an_invalid_then_valid_cycle_and_a_removal(vault: Path, conn: sqlite3.Connection) -> None:
    path = write(vault, "2026-09-02-a.md", note_text(kind="reminder", extra="schedule: at 2026-09-02T10:00:00+03:00\n"))
    sync.run(vault, conn)
    conn.execute(
        "INSERT INTO deliveries (path, occurrence_at, delivered_at) VALUES (?, ?, ?)",
        (path, "2026-09-02T10:00:00+03:00", "2026-09-02T10:00:30+03:00"),
    )

    write(vault, "2026-09-02-a.md", NO_KIND)
    assert sync.run(vault, conn).invalid_paths == (path,)
    write(vault, "2026-09-02-a.md", note_text(kind="reminder", extra="schedule: at 2026-09-02T10:00:00+03:00\n"))
    assert sync.run(vault, conn).changed == (path,)
    (vault / path).unlink()
    assert sync.run(vault, conn).removed == (path,)

    delivery = conn.execute("SELECT path, occurrence_at, read_at FROM deliveries").fetchone()
    assert tuple(delivery) == (path, "2026-09-02T10:00:00+03:00", None)


def test_run_is_one_transaction(vault: Path, conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    first = write(vault, "2026-09-02-a.md", note_text())
    second = write(vault, "2026-09-02-b.md", note_text())
    real = document.validate

    def failing(doc: document.Document, kinds: list[str], root: Path) -> list[ValidationError]:
        if doc.path == second:
            raise RuntimeError("boom")
        return real(doc, kinds, root)

    monkeypatch.setattr(document, "validate", failing)
    with pytest.raises(RuntimeError, match="boom"):
        sync.run(vault, conn)

    assert not conn.in_transaction
    assert paths(conn, "notes") == []
    monkeypatch.setattr(document, "validate", real)
    assert sync.run(vault, conn).changed == (first, second)


def test_deleted_index_is_rebuilt_from_the_files(vault: Path) -> None:
    path = write(vault, "2026-09-02-a.md", note_text())
    connection = db.open_index(vault)
    assert sync.run(vault, connection).changed == (path,)
    connection.close()
    for name in ("index.sqlite", "index.sqlite-wal", "index.sqlite-shm"):
        (vault / name).unlink(missing_ok=True)

    connection = db.open_index(vault)
    report = sync.run(vault, connection)

    assert report == SyncReport((path,), (), (), 0)
    assert paths(connection, "notes") == [path]
    connection.close()


# Report rendering


def test_report_partitions_the_files_on_disk(vault: Path, conn: sqlite3.Connection) -> None:
    first = write(vault, "2026-09-02-a.md", note_text())
    write(vault, "2026-09-02-b.md", note_text())
    bad = write(vault, "2026-09-02-bad.md", NO_KIND)
    sync.run(vault, conn)

    write(vault, "2026-09-02-a.md", note_text(title="Changed"))
    report = sync.run(vault, conn)

    assert report.changed == (first,)
    assert report.unchanged_count == 1
    assert report.invalid_paths == (bad,)
    assert report.note_count == 2


def test_error_lines_use_absolute_paths_and_omit_missing_line_numbers(vault: Path) -> None:
    invalid = [
        InvalidFile("notes/x.md", (ValidationError(3, "kind is required"), ValidationError(None, "bad name"))),
        InvalidFile("notes/y.md", (ValidationError(1, "status is required"),)),
    ]

    assert sync.error_lines(vault, invalid) == [
        f"{vault}/notes/x.md:3: kind is required",
        f"{vault}/notes/x.md: bad name",
        f"{vault}/notes/y.md:1: status is required",
    ]


def test_report_as_data(vault: Path) -> None:
    invalid = InvalidFile("notes/x.md", (ValidationError(None, "bad name"),))
    report = SyncReport(("notes/a.md",), ("notes/b.md",), (invalid,), 4)

    assert sync.report_as_data(vault, report) == {
        "changed": ["notes/a.md"],
        "removed": ["notes/b.md"],
        "invalid": [
            {"path": "notes/x.md", "abs_path": f"{vault}/notes/x.md", "errors": [{"line": None, "message": "bad name"}]}
        ],
        "unchanged_count": 4,
    }
