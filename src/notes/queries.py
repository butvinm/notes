"""Read queries over the index for `notes show` and `notes list`: notes with their effective status and unread count,
their outgoing relations, and the `paths` matching that `list --path` (and later `recall --cwd`) share.

Nothing here writes: sync fills the index, and only `tick`, `read`, and `move` change it afterwards.
"""

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EFFECTIVE_STATUSES = ("active", "archived", "superseded")
"""The values `effective_status` takes: the two stored statuses plus the derived `superseded`."""


@dataclass(frozen=True)
class NoteRow:
    """One indexed note: the stored columns, the derived effective status, and how many of its deliveries are unread."""

    path: str
    kind: str
    status: str
    effective_status: str
    title: str
    body: str
    schedule: str | None
    paths: tuple[str, ...]
    tags: tuple[str, ...]
    keywords: tuple[str, ...]
    references: tuple[str, ...]
    created_date: str
    unread: int


@dataclass(frozen=True)
class RelationRow:
    """An outgoing relation of a note; `target` is the vault-relative path of the other note."""

    relation: str
    target: str


_SELECT = """
SELECT listed.* FROM (
    SELECT notes_view.*,
           (SELECT count(*) FROM deliveries
             WHERE deliveries.path = notes_view.path AND deliveries.read_at IS NULL) AS unread
      FROM notes_view
) AS listed
"""

_ORDER = " ORDER BY listed.unread > 0 DESC, listed.created_date DESC, listed.path"


def get_note(conn: sqlite3.Connection, path: str) -> NoteRow | None:
    """The indexed note at the vault-relative `path`, or None when the file is not a valid, indexed note."""
    row = conn.execute(_SELECT + " WHERE listed.path = ?", (path,)).fetchone()
    return _note_row(row) if row is not None else None


def relations_of(conn: sqlite3.Connection, path: str) -> list[RelationRow]:
    """The outgoing relations of the note at `path`, ordered by relation name and target."""
    rows = conn.execute("SELECT relation, dst FROM relations WHERE src = ? ORDER BY relation, dst", (path,))
    return [RelationRow(row["relation"], row["dst"]) for row in rows]


def referrers_of(conn: sqlite3.Connection, path: str) -> list[str]:
    """The indexed notes with a relation pointing at `path`, by path; `notes move` rewrites their references."""
    rows = conn.execute("SELECT DISTINCT src FROM relations WHERE dst = ? ORDER BY src", (path,))
    return [row["src"] for row in rows]


def all_tags(conn: sqlite3.Connection) -> list[str]:
    """Every tag in the index once, ordered by its lowercase form, spelled the way most notes spell it.

    A tie between spellings goes to the one that sorts first. This is the list the generator prompt offers as the
    vault's existing tags.
    """
    rows = conn.execute(
        "SELECT tag, tag_lower, count(*) AS uses FROM note_tags"
        " GROUP BY tag_lower, tag ORDER BY tag_lower, uses DESC, tag"
    )
    tags: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if row["tag_lower"] not in seen:
            seen.add(row["tag_lower"])
            tags.append(row["tag"])
    return tags


def list_notes(
    conn: sqlite3.Connection,
    *,
    unread: bool = False,
    kind: str | None = None,
    status: str | None = None,
    tag: str | None = None,
    path: str | None = None,
) -> list[NoteRow]:
    """The indexed notes, unread first, then newest first, then by path; every given filter must hold.

    `status` is an effective status, so `superseded` works. `tag` is matched case-insensitively.
    `path` keeps the notes with a `paths` entry that, expanded, equals it or is one of its ancestors.
    """
    conditions = []
    params: list[Any] = []
    if unread:
        conditions.append("listed.unread > 0")
    if kind is not None:
        conditions.append("listed.kind = ?")
        params.append(kind)
    if status is not None:
        conditions.append("listed.effective_status = ?")
        params.append(status)
    if tag is not None:
        conditions.append(
            "EXISTS (SELECT 1 FROM note_tags WHERE note_tags.path = listed.path AND note_tags.tag_lower = ?)"
        )
        params.append(tag.lower())
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = [_note_row(row) for row in conn.execute(_SELECT + where + _ORDER, params)]
    if path is None:
        return rows
    target = absolute(path)
    return [row for row in rows if any(covers(entry, target) for entry in row.paths)]


def absolute(text: str) -> Path:
    """`text` as an absolute path: `~` expanded, a relative path taken from the current directory, symlinks resolved.

    Both a `paths` entry and the path it is compared with go through here, so a project reached through a symlink
    matches whether the note holds the logical path and the shell reports the physical one or the other way round.
    """
    return Path(os.path.expanduser(text)).resolve()


def covers(entry: str, target: Path) -> bool:
    """Whether the `paths` entry `entry`, expanded with `absolute`, equals `target` or is one of its ancestors."""
    expanded = absolute(entry)
    return expanded == target or expanded in target.parents


def as_data(vault: Path, row: NoteRow) -> dict[str, Any]:
    """The JSON shape of a note's metadata: one element of `notes list --json`, the start of `notes show --json`."""
    return {
        "path": row.path,
        "abs_path": str(vault / row.path),
        "kind": row.kind,
        "status": row.status,
        "effective_status": row.effective_status,
        "title": row.title,
        "created_date": row.created_date,
        "schedule": row.schedule,
        "paths": list(row.paths),
        "tags": list(row.tags),
        "keywords": list(row.keywords),
        "references": list(row.references),
        "unread": row.unread,
    }


def _note_row(row: sqlite3.Row) -> NoteRow:
    return NoteRow(
        path=row["path"],
        kind=row["kind"],
        status=row["status"],
        effective_status=row["effective_status"],
        title=row["title"],
        body=row["body"],
        schedule=row["schedule"],
        paths=_strings(row["paths"]),
        tags=_strings(row["tags"]),
        keywords=_strings(row["keywords"]),
        references=_strings(row["refs"]),
        created_date=row["created_date"],
        unread=row["unread"],
    )


def _strings(encoded: str) -> tuple[str, ...]:
    return tuple(json.loads(encoded))
