"""Incremental sync from the Markdown files under `notes/` into the SQLite index.

Markdown always wins over SQLite: every command on an existing vault runs `run` first, and a row is only ever
reconstructed from its file, never the other way round. One run does, inside a single write transaction:

1. walk `notes/**/*.md` and compare `(mtime_ns, size)` with the row in `notes`; an unchanged valid file is skipped
   without being read. Files recorded in `invalid_files`, and valid files that relate to a file removed since the last
   run, are always re-read, because their validity depends on other files.
2. read the rest and hash the content; a valid file whose hash is unchanged only gets its stat columns refreshed.
3. parse and validate: a valid note replaces its rows in `notes`, `note_tags`, `relations`, and `notes_fts`, and
   leaves `invalid_files`; an invalid file loses every derived row and is recorded in `invalid_files` with its errors.
4. files missing from disk lose their derived rows and their `invalid_files` row.

`deliveries` is never touched here. Committing the outcome to Git is the Git layer's job, driven by the `SyncReport`.
"""

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from notes import document, textnorm
from notes.db import transaction
from notes.document import Note, ValidationError
from notes.vault import known_kinds, notes_dir


@dataclass(frozen=True)
class InvalidFile:
    """A file under `notes/` that is not a valid note, with the errors `notes check` prints."""

    path: str
    errors: tuple[ValidationError, ...]


@dataclass(frozen=True)
class SyncReport:
    """What one run found.

    `changed` holds the valid notes that were indexed anew: new files, files whose content changed, and files that
    were invalid before and are valid now. `removed` holds the files that disappeared from disk. Together they are the
    paths the Git layer stages; invalid files are never staged. `invalid` lists every file that is invalid after the
    run, whether or not it changed, so `notes check` can print all of them. `unchanged_count` counts the valid files
    that needed no re-indexing, so `changed`, `invalid`, and `unchanged_count` partition the files on disk.
    """

    changed: tuple[str, ...]
    removed: tuple[str, ...]
    invalid: tuple[InvalidFile, ...]
    unchanged_count: int

    @property
    def note_count(self) -> int:
        """Valid notes in the index after the run."""
        return len(self.changed) + self.unchanged_count

    @property
    def invalid_paths(self) -> tuple[str, ...]:
        return tuple(item.path for item in self.invalid)


EMPTY_REPORT = SyncReport((), (), (), 0)
"""The report of a run that found nothing: the placeholder a session holds until its first sync."""


def run(vault: Path, conn: sqlite3.Connection) -> SyncReport:
    """Bring the index in line with the files under `notes/` and report what changed.

    The whole run is one `BEGIN IMMEDIATE` transaction: an error anywhere leaves the index as it was.
    """
    sync = _Run(vault, conn, known_kinds(vault))
    with transaction(conn):
        indexed = _indexed(conn)
        invalid_before = {row["path"] for row in conn.execute("SELECT path FROM invalid_files")}
        on_disk = _scan(vault)
        removed = sorted((indexed.keys() | invalid_before) - on_disk.keys())
        referrers = _referrers(conn, removed)
        for path, stat in on_disk.items():
            sync.file(path, stat, indexed.get(path), revalidate=path in referrers)
        for path in removed:
            _forget(conn, path)
    return SyncReport(tuple(sync.changed), tuple(removed), tuple(sync.invalid), sync.unchanged)


def error_lines(vault: Path, invalid: Iterable[InvalidFile]) -> list[str]:
    """One `path:line: message` line per error, with the absolute path so a terminal or editor can jump to it.

    An error without a line, such as a bad filename, is printed as `path: message`.
    """
    lines = []
    for item in invalid:
        location = str(vault / item.path)
        for error in item.errors:
            prefix = f"{location}:{error.line}" if error.line is not None else location
            lines.append(f"{prefix}: {error.message}")
    return lines


def invalid_as_data(vault: Path, invalid: Iterable[InvalidFile]) -> list[dict[str, Any]]:
    """The JSON shape of invalid files: `path`, `abs_path`, and `errors` as `{line, message}` objects."""
    return [
        {
            "path": item.path,
            "abs_path": str(vault / item.path),
            "errors": [{"line": error.line, "message": error.message} for error in item.errors],
        }
        for item in invalid
    ]


def report_as_data(vault: Path, report: SyncReport) -> dict[str, Any]:
    """The JSON shape of a report, as `notes sync --json` prints it."""
    return {
        "changed": list(report.changed),
        "removed": list(report.removed),
        "invalid": invalid_as_data(vault, report.invalid),
        "unchanged_count": report.unchanged_count,
    }


@dataclass(frozen=True)
class _Indexed:
    """The columns of a `notes` row that decide whether its file must be read again."""

    mtime_ns: int
    size: int
    content_hash: str


class _Run:
    """The state of one run: the vault, the connection, the known kinds, and the report being assembled."""

    def __init__(self, vault: Path, conn: sqlite3.Connection, kinds: list[str]) -> None:
        self.vault = vault
        self.conn = conn
        self.kinds = kinds
        self.changed: list[str] = []
        self.invalid: list[InvalidFile] = []
        self.unchanged = 0

    def file(self, path: str, stat: os.stat_result, before: _Indexed | None, *, revalidate: bool) -> None:
        """Bring one file up to date; `before` is its current valid row, `revalidate` forces a read of a valid file."""
        if before is not None and not revalidate and (before.mtime_ns, before.size) == (stat.st_mtime_ns, stat.st_size):
            self.unchanged += 1
            return
        raw = (self.vault / path).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if before is not None and not revalidate and digest == before.content_hash:
            self.conn.execute(
                "UPDATE notes SET mtime_ns = ?, size = ? WHERE path = ?", (stat.st_mtime_ns, stat.st_size, path)
            )
            self.unchanged += 1
            return
        note, errors = self._parse(path, raw)
        if note is None:
            self._mark_invalid(path, digest, stat, errors)
            self.invalid.append(InvalidFile(path, tuple(errors)))
            return
        self._index(note, stat)
        if before is not None and digest == before.content_hash:
            self.unchanged += 1
        else:
            self.changed.append(path)

    def _parse(self, path: str, raw: bytes) -> tuple[Note | None, list[ValidationError]]:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            return None, [ValidationError(None, f"file is not valid UTF-8 ({error.reason} at byte {error.start})")]
        doc = document.parse(path, text)
        errors = document.validate(doc, self.kinds, self.vault)
        if errors:
            return None, errors
        return doc.to_note(), []

    def _index(self, note: Note, stat: os.stat_result) -> None:
        conn = self.conn
        conn.execute(
            """INSERT OR REPLACE INTO notes (path, kind, status, title, body, schedule, paths, tags, keywords, refs,
                                             created_date, content_hash, mtime_ns, size)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                note.path,
                note.kind,
                note.status,
                note.title,
                note.body,
                note.schedule,
                _json_list(note.paths),
                _json_list(note.tags),
                _json_list(note.keywords),
                _json_list(note.references),
                note.created_date.isoformat(),
                note.content_hash,
                stat.st_mtime_ns,
                stat.st_size,
            ),
        )
        conn.execute("DELETE FROM note_tags WHERE path = ?", (note.path,))
        conn.executemany(
            "INSERT OR IGNORE INTO note_tags (path, tag, tag_lower) VALUES (?, ?, ?)",
            [(note.path, tag, tag.lower()) for tag in note.tags],
        )
        conn.execute("DELETE FROM relations WHERE src = ?", (note.path,))
        conn.executemany(
            "INSERT OR IGNORE INTO relations (src, relation, dst) VALUES (?, ?, ?)",
            [(note.path, relation.relation, relation.target_path) for relation in note.related],
        )
        conn.execute("DELETE FROM notes_fts WHERE path = ?", (note.path,))
        conn.execute(
            "INSERT INTO notes_fts (path, title, tags, keywords, body) VALUES (?, ?, ?, ?, ?)",
            (
                note.path,
                textnorm.normalize(note.title),
                textnorm.normalize(" ".join(note.tags)),
                textnorm.normalize(" ".join(note.keywords)),
                textnorm.normalize(note.body),
            ),
        )
        conn.execute("DELETE FROM invalid_files WHERE path = ?", (note.path,))

    def _mark_invalid(self, path: str, digest: str, stat: os.stat_result, errors: list[ValidationError]) -> None:
        _forget_derived(self.conn, path)
        recorded = json.dumps([{"line": error.line, "message": error.message} for error in errors], ensure_ascii=False)
        self.conn.execute(
            "INSERT OR REPLACE INTO invalid_files (path, content_hash, mtime_ns, size, errors) VALUES (?, ?, ?, ?, ?)",
            (path, digest, stat.st_mtime_ns, stat.st_size, recorded),
        )


def _indexed(conn: sqlite3.Connection) -> dict[str, _Indexed]:
    rows = conn.execute("SELECT path, mtime_ns, size, content_hash FROM notes")
    return {row["path"]: _Indexed(row["mtime_ns"], row["size"], row["content_hash"]) for row in rows}


def _scan(vault: Path) -> dict[str, os.stat_result]:
    """Every `.md` file under `notes/` by vault-relative path, in path order, with its stat."""
    found = {}
    for file in notes_dir(vault).rglob("*.md"):
        if file.is_file():
            found[file.relative_to(vault).as_posix()] = file.stat()
    return dict(sorted(found.items()))


def _referrers(conn: sqlite3.Connection, removed: list[str]) -> set[str]:
    """The notes with a relation pointing at a removed file; they must be validated again."""
    if not removed:
        return set()
    placeholders = ", ".join("?" for _ in removed)
    rows = conn.execute(f"SELECT DISTINCT src FROM relations WHERE dst IN ({placeholders})", removed)
    return {row["src"] for row in rows}


def _forget_derived(conn: sqlite3.Connection, path: str) -> None:
    conn.execute("DELETE FROM notes WHERE path = ?", (path,))
    conn.execute("DELETE FROM note_tags WHERE path = ?", (path,))
    conn.execute("DELETE FROM relations WHERE src = ?", (path,))
    conn.execute("DELETE FROM notes_fts WHERE path = ?", (path,))


def _forget(conn: sqlite3.Connection, path: str) -> None:
    _forget_derived(conn, path)
    conn.execute("DELETE FROM invalid_files WHERE path = ?", (path,))


def _json_list(items: tuple[str, ...]) -> str:
    return json.dumps(list(items), ensure_ascii=False)
