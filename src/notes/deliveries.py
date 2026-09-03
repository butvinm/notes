"""Deliveries: the device-local record of which scheduled occurrences were shown on this machine and read.

`deliveries` is the one table of the index that is not derived from the Markdown files, so sync never touches it;
the commands that change it write it directly: `tick` records every occurrence it shows, `read` closes a note's
unread rows, and `move` re-points them when a note is renamed.

An occurrence is named by its canonical timestamp text, so a schedule names the same occurrence in every run, and
`(path, occurrence_at)` is unique in the table. Only the latest occurrence at or before `now` is ever considered,
so a machine that was off for a while gets one delivery per note and the missed occurrences are never replayed;
during normal operation every new occurrence makes the note unread again. The uniqueness also keeps two
overlapping runs (the systemd timer and a `notes tick` typed by hand) from showing one occurrence twice: `record`
says whether it inserted the row, and `tick` notifies only then.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from notes import notify
from notes import schedule as schedules
from notes.config import NotificationsConfig
from notes.db import transaction
from notes.notify import NotifyResult

_SCHEDULED_SQL = """
SELECT path, title, schedule FROM notes_view
 WHERE schedule IS NOT NULL AND effective_status = 'active'
 ORDER BY path
"""
"""The notes that can become due: scheduled and active. An archived or superseded note never delivers."""

_UNREAD_SQL = "SELECT id, path, occurrence_at, delivered_at, read_at FROM deliveries WHERE read_at IS NULL"


@dataclass(frozen=True)
class Due:
    """A note whose latest scheduled occurrence has not been delivered on this machine yet."""

    path: str
    title: str
    occurrence_at: str


@dataclass(frozen=True)
class Delivery:
    """One row of `deliveries`; `read_at` is None while the delivery is unread."""

    id: int
    path: str
    occurrence_at: str
    delivered_at: str
    read_at: str | None


@dataclass(frozen=True)
class Delivered:
    """One delivery `tick` created, with how the desktop took it.

    `notified` is the result of `notify-send`, `sound` the result of the sound (None when the sound is off).
    A failure of either leaves the delivery recorded and unread; `warnings` spells it out for stderr.
    """

    path: str
    title: str
    occurrence_at: str
    delivered_at: str
    notified: NotifyResult
    sound: NotifyResult | None

    @property
    def warnings(self) -> list[str]:
        """What failed on the desktop, one line per failure, each starting with the note's ID."""
        results = (self.notified, self.sound)
        return [f"{self.path}: {result.error}" for result in results if result is not None and not result]


def due_notes(conn: sqlite3.Connection, now: datetime) -> list[Due]:
    """The active scheduled notes whose latest occurrence at or before `now` has no delivery yet, by path.

    A schedule that has not started yet is not due. An indexed schedule is canonical, so parsing it cannot fail.
    """
    due = []
    for row in conn.execute(_SCHEDULED_SQL).fetchall():
        occurrence = schedules.latest_due(schedules.parse(row["schedule"]), now)
        if occurrence is None:
            continue
        text = schedules.format_timestamp(occurrence)
        if not _delivered(conn, row["path"], text):
            due.append(Due(row["path"], row["title"], text))
    return due


def record(conn: sqlite3.Connection, path: str, occurrence: str, now: datetime) -> bool:
    """Insert the unread delivery of `occurrence` for the note at `path`, delivered at `now`.

    Returns False, leaving the table as it is, when that occurrence already has a row: another run got there first.
    """
    with transaction(conn):
        cursor = conn.execute(
            "INSERT OR IGNORE INTO deliveries (path, occurrence_at, delivered_at, read_at) VALUES (?, ?, ?, NULL)",
            (path, occurrence, schedules.format_timestamp(now)),
        )
        return cursor.rowcount == 1


def unread(conn: sqlite3.Connection, path: str | None = None) -> list[Delivery]:
    """The unread deliveries, of every note or of the one at `path`, ordered by path and occurrence."""
    query, params = _UNREAD_SQL, ()
    if path is not None:
        query, params = f"{_UNREAD_SQL} AND path = ?", (path,)
    rows = conn.execute(f"{query} ORDER BY path, occurrence_at", params)
    return [Delivery(row["id"], row["path"], row["occurrence_at"], row["delivered_at"], row["read_at"]) for row in rows]


def mark_read(conn: sqlite3.Connection, path: str, now: datetime) -> int:
    """Close every unread delivery of the note at `path` with `now`; returns how many were closed (zero is fine)."""
    with transaction(conn):
        cursor = conn.execute(
            "UPDATE deliveries SET read_at = ? WHERE path = ? AND read_at IS NULL",
            (schedules.format_timestamp(now), path),
        )
        return cursor.rowcount


def tick(conn: sqlite3.Connection, config: NotificationsConfig, now: datetime) -> list[Delivered]:
    """Deliver what is due at `now`: record each occurrence once, show the notification, play the sound when on.

    An occurrence another run recorded meanwhile is skipped without a notification. A notification or sound
    failure never raises: it is reported in the result, and the delivery stays recorded and unread.
    """
    delivered_at = schedules.format_timestamp(now)
    delivered = []
    for due in due_notes(conn, now):
        if not record(conn, due.path, due.occurrence_at, now):
            continue
        notified = notify.send(due.title, due.path)
        sound = notify.play_sound(config.sound_id) if config.sound else None
        delivered.append(Delivered(due.path, due.title, due.occurrence_at, delivered_at, notified, sound))
    return delivered


def move(conn: sqlite3.Connection, old: str, new: str) -> int:
    """Re-point the deliveries of the note renamed from `old` to `new`; returns how many rows followed it.

    Rows already under `new` are dropped first: no file existed there, so they are leftovers of a note deleted
    earlier, and keeping them would hand its unread state to the moved note (and collide with its rows on the
    `(path, occurrence_at)` key).
    """
    with transaction(conn):
        _delete(conn, new)
        cursor = conn.execute("UPDATE deliveries SET path = ? WHERE path = ?", (new, old))
        return cursor.rowcount


def clear(conn: sqlite3.Connection, path: str) -> int:
    """Drop every delivery recorded for `path`; returns how many rows went.

    A note freshly created at a path starts without delivery state. Sync never touches this table, so rows can
    outlive the note they belong to (a file deleted by hand), and the path is free again for the next note of the
    same day and short name; those rows would show the new note as unread with the old note's timestamp and
    suppress its first occurrence when the two happen to meet. The path was claimed with `O_EXCL`, so nothing
    else can own the rows. `move` states the same rule for the path it moves onto.
    """
    with transaction(conn):
        return _delete(conn, path)


def _delete(conn: sqlite3.Connection, path: str) -> int:
    return conn.execute("DELETE FROM deliveries WHERE path = ?", (path,)).rowcount


def _delivered(conn: sqlite3.Connection, path: str, occurrence: str) -> bool:
    row = conn.execute("SELECT 1 FROM deliveries WHERE path = ? AND occurrence_at = ?", (path, occurrence)).fetchone()
    return row is not None
