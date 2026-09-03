"""SQLite index at `<vault>/index.sqlite`: connection settings, schema, rebuilds, and the effective-status expression.

Every table except `deliveries` is derived from the Markdown files and can be rebuilt at any time.
A change of `SCHEMA_VERSION` drops and recreates the derived tables and the view, and the next sync repopulates them;
`deliveries` is device-local notification state, the only table that is not derived, and it survives every rebuild.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from notes.vault import db_path

SCHEMA_VERSION = 1
"""Bump when a derived table, the FTS configuration, or the view changes; `ensure_schema` then rebuilds them."""

BUSY_TIMEOUT_MS = 5000
"""How long a connection waits for a lock: `tick` from systemd and an interactive command may overlap."""

EFFECTIVE_STATUS_SQL = """CASE
    WHEN notes.status = 'archived' THEN 'archived'
    WHEN EXISTS (SELECT 1 FROM relations WHERE relations.relation = 'supersedes' AND relations.dst = notes.path)
        THEN 'superseded'
    ELSE 'active'
END"""
"""The effective status of a row of `notes`, usable in any query whose FROM clause exposes the table as `notes`.

Stored statuses are `active` and `archived`; `superseded` is derived from an incoming `supersedes` relation,
and a stored `archived` always wins over it.
"""

DERIVED_TABLES = ("meta", "notes", "note_tags", "relations", "invalid_files", "notes_fts")
DERIVED_VIEWS = ("notes_view",)

_CREATE_DERIVED = (
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    """CREATE TABLE notes (
        path TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        status TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        schedule TEXT,
        paths TEXT NOT NULL,
        tags TEXT NOT NULL,
        keywords TEXT NOT NULL,
        refs TEXT NOT NULL,
        created_date TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        mtime_ns INTEGER NOT NULL,
        size INTEGER NOT NULL
    )""",
    """CREATE TABLE note_tags (
        path TEXT NOT NULL,
        tag TEXT NOT NULL,
        tag_lower TEXT NOT NULL,
        PRIMARY KEY (path, tag_lower)
    )""",
    "CREATE INDEX note_tags_by_tag ON note_tags (tag_lower)",
    """CREATE TABLE relations (
        src TEXT NOT NULL,
        relation TEXT NOT NULL,
        dst TEXT NOT NULL,
        PRIMARY KEY (src, relation, dst)
    )""",
    "CREATE INDEX relations_by_dst ON relations (dst, relation)",
    """CREATE TABLE invalid_files (
        path TEXT PRIMARY KEY,
        content_hash TEXT NOT NULL,
        mtime_ns INTEGER NOT NULL,
        size INTEGER NOT NULL,
        errors TEXT NOT NULL
    )""",
    """CREATE VIRTUAL TABLE notes_fts USING fts5 (
        path UNINDEXED, title, tags, keywords, body,
        tokenize = 'porter unicode61 remove_diacritics 2'
    )""",
    f"CREATE VIEW notes_view AS SELECT notes.*, {EFFECTIVE_STATUS_SQL} AS effective_status FROM notes",
)

_CREATE_DELIVERIES = (
    """CREATE TABLE IF NOT EXISTS deliveries (
        id INTEGER PRIMARY KEY,
        path TEXT NOT NULL,
        occurrence_at TEXT NOT NULL,
        delivered_at TEXT NOT NULL,
        read_at TEXT,
        UNIQUE (path, occurrence_at)
    )""",
    "CREATE INDEX IF NOT EXISTS deliveries_by_path ON deliveries (path)",
)


def connect(vault: Path) -> sqlite3.Connection:
    """Open `<vault>/index.sqlite` in WAL mode with a 5 second busy timeout, foreign keys on, and `sqlite3.Row` rows.

    The connection runs in autocommit mode; writes that must be atomic use `transaction`.
    The schema is not touched here, call `ensure_schema` or use `open_index`.
    """
    conn = sqlite3.connect(db_path(vault), isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def open_index(vault: Path) -> sqlite3.Connection:
    """`connect` followed by `ensure_schema`: the one call a command makes to get a usable index."""
    conn = connect(vault)
    ensure_schema(conn)
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One write transaction: commits when the block finishes, rolls back when it raises.

    `BEGIN IMMEDIATE` takes the write lock up front, so an overlapping process waits for `busy_timeout`
    at the start instead of failing halfway through its writes.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def stored_version(conn: sqlite3.Connection) -> int | None:
    """The schema version recorded in `meta`, or `None` for a fresh or unrecognizable database."""
    if not _table_exists(conn, "meta"):
        return None
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    if row is None:
        return None
    try:
        return int(row[0])
    except ValueError:
        return None


def ensure_schema(conn: sqlite3.Connection) -> bool:
    """Create the schema on a fresh database, or rebuild the derived tables when the stored version differs.

    Returns `True` when tables were created or recreated, which tells the caller that every note needs indexing.
    """
    if stored_version(conn) == SCHEMA_VERSION:
        return False
    rebuild_derived(conn)
    return True


def rebuild_derived(conn: sqlite3.Connection) -> None:
    """Drop and recreate every derived table and view, keep `deliveries`, and record `SCHEMA_VERSION` in `meta`.

    Runs as one transaction, so an interrupted rebuild leaves the previous schema in place.
    """
    with transaction(conn):
        for view in DERIVED_VIEWS:
            conn.execute(f"DROP VIEW IF EXISTS {view}")
        for table in DERIVED_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        for statement in _CREATE_DERIVED:
            conn.execute(statement)
        for statement in _CREATE_DELIVERIES:
            conn.execute(statement)
        conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()
    return row is not None
