"""Tests for `notes.deliveries`: which notes are due, one row per occurrence, the unread rows and their closing,
the `tick` loop over the desktop helpers, and the rows of a moved note following it.

The notes are inserted straight into the index of a bare vault; the desktop calls of `tick` are recorded."""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from notes import db, deliveries
from notes.config import NotificationsConfig
from notes.deliveries import Delivered, Delivery, Due
from notes.notify import NotifyResult
from tests.conftest import RecordedCommands

OLD = "notes/2026-09-01-old.md"
NEW = "notes/archive/2026-09-01-old.md"
OTHER = "notes/2026-09-02-other.md"

PLANTS = "notes/2026-09-02-water-the-plants.md"
STANDUP = "notes/2026-09-01-standup.md"
LIPS = "notes/2026-08-10-ship-lips-release.md"
LEGACY = "notes/2026-08-30-legacy.md"
KAFKA = "notes/2026-09-02-kafka.md"
IDEA = "notes/2026-08-15-idea.md"

UTC3 = timezone(timedelta(hours=3))
ANCHOR = datetime(2026, 9, 2, 10, 0, tzinfo=UTC3)
ANCHOR_TEXT = "2026-09-02T10:00:00+03:00"
EVERY_3_DAYS = f"every 3 days from {ANCHOR_TEXT}"
NOW = datetime(2026, 9, 9, 12, 30, tzinfo=UTC3)
NOW_TEXT = "2026-09-09T12:30:00+03:00"

NOTIFY = ("notify-send", "--app-name", "notes", "--")
SOUND = ("canberra-gtk-play", "-i", "message-new-instant")


def insert_note(
    conn: sqlite3.Connection,
    path: str,
    *,
    kind: str = "reminder",
    status: str = "active",
    schedule: str | None = None,
    title: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO notes (path, kind, status, title, body, schedule, paths, tags, keywords, refs,
                              created_date, content_hash, mtime_ns, size)
           VALUES (?, ?, ?, ?, '', ?, '[]', '[]', '[]', '[]', '2026-09-02', 'hash', 0, 0)""",
        (path, kind, status, title or f"Title of {path}", schedule),
    )


def deliver(
    conn: sqlite3.Connection, path: str, occurrence: str, *, delivered_at: str | None = None, read_at: str | None = None
) -> None:
    conn.execute(
        "INSERT INTO deliveries (path, occurrence_at, delivered_at, read_at) VALUES (?, ?, ?, ?)",
        (path, occurrence, delivered_at or occurrence, read_at),
    )


def rows(conn: sqlite3.Connection) -> list[tuple[str, str, str | None]]:
    query = "SELECT path, occurrence_at, read_at FROM deliveries ORDER BY path, occurrence_at"
    return [tuple(row) for row in conn.execute(query)]


def full_rows(conn: sqlite3.Connection) -> list[tuple[str, str, str, str | None]]:
    query = "SELECT path, occurrence_at, delivered_at, read_at FROM deliveries ORDER BY path, occurrence_at"
    return [tuple(row) for row in conn.execute(query)]


def due_paths(conn: sqlite3.Connection, now: datetime) -> list[str]:
    return [due.path for due in deliveries.due_notes(conn, now)]


@pytest.fixture
def conn(bare_vault: Path) -> Iterator[sqlite3.Connection]:
    connection = db.open_index(bare_vault)
    yield connection
    connection.close()


# due_notes


def test_due_notes_lists_active_scheduled_notes_whose_latest_occurrence_is_undelivered(
    conn: sqlite3.Connection,
) -> None:
    insert_note(conn, PLANTS, schedule="at 2026-09-09T10:00:00+03:00", title="Water the plants")
    insert_note(conn, STANDUP, schedule=EVERY_3_DAYS, title="Standup")
    insert_note(conn, LIPS, kind="reminder", schedule="at 2026-09-15T18:00:00+03:00")
    insert_note(conn, IDEA, kind="idea")
    insert_note(conn, LEGACY, status="archived", schedule="at 2026-08-30T10:00:00+03:00")
    insert_note(conn, OLD, schedule="at 2026-09-01T10:00:00+03:00")
    insert_note(conn, KAFKA, kind="decision")
    conn.execute("INSERT INTO relations VALUES (?, 'supersedes', ?)", (KAFKA, OLD))
    insert_note(conn, OTHER, schedule="at 2026-09-02T10:00:00+03:00")
    deliver(conn, OTHER, "2026-09-02T10:00:00+03:00", read_at="2026-09-02T11:00:00+03:00")

    assert deliveries.due_notes(conn, NOW) == [
        Due(STANDUP, "Standup", "2026-09-08T10:00:00+03:00"),
        Due(PLANTS, "Water the plants", "2026-09-09T10:00:00+03:00"),
    ]


def test_due_notes_is_empty_before_a_schedule_starts(conn: sqlite3.Connection) -> None:
    insert_note(conn, PLANTS, schedule="at 2026-09-09T10:00:00+03:00")
    insert_note(conn, STANDUP, schedule=EVERY_3_DAYS)

    assert due_paths(conn, datetime(2026, 9, 2, 9, 59, tzinfo=UTC3)) == []
    assert due_paths(conn, ANCHOR) == [STANDUP]
    assert due_paths(conn, datetime(2026, 9, 9, 10, 0, tzinfo=UTC3)) == [STANDUP, PLANTS]


def test_due_notes_names_the_latest_occurrence_only_and_forgets_the_missed_ones(conn: sqlite3.Connection) -> None:
    insert_note(conn, STANDUP, schedule=EVERY_3_DAYS)
    deliver(conn, STANDUP, ANCHOR_TEXT, read_at="2026-09-02T11:00:00+03:00")

    long_gap = deliveries.due_notes(conn, ANCHOR + timedelta(days=20, hours=5))

    assert long_gap == [Due(STANDUP, f"Title of {STANDUP}", "2026-09-20T10:00:00+03:00")]
    assert due_paths(conn, ANCHOR + timedelta(days=2, hours=23)) == []
    assert due_paths(conn, ANCHOR + timedelta(days=3)) == [STANDUP]


def test_due_notes_compares_instants_across_offsets(conn: sqlite3.Connection) -> None:
    insert_note(conn, PLANTS, schedule="at 2026-09-09T10:00:00+03:00")

    assert due_paths(conn, datetime(2026, 9, 9, 6, 59, tzinfo=UTC)) == []
    assert due_paths(conn, datetime(2026, 9, 9, 7, 0, tzinfo=UTC)) == [PLANTS]


def test_due_notes_rejects_a_naive_now(conn: sqlite3.Connection) -> None:
    insert_note(conn, PLANTS, schedule="at 2026-09-09T10:00:00+03:00")

    with pytest.raises(ValueError, match="timezone-aware"):
        deliveries.due_notes(conn, datetime(2026, 9, 9, 10, 0))


# record


def test_record_inserts_an_unread_delivery_and_refuses_a_duplicate(conn: sqlite3.Connection) -> None:
    first = deliveries.record(conn, PLANTS, "2026-09-09T10:00:00+03:00", NOW)
    again = deliveries.record(conn, PLANTS, "2026-09-09T10:00:00+03:00", NOW + timedelta(minutes=1))
    other = deliveries.record(conn, PLANTS, "2026-09-12T10:00:00+03:00", NOW + timedelta(minutes=1))

    assert (first, again, other) == (True, False, True)
    assert full_rows(conn) == [
        (PLANTS, "2026-09-09T10:00:00+03:00", NOW_TEXT, None),
        (PLANTS, "2026-09-12T10:00:00+03:00", "2026-09-09T12:31:00+03:00", None),
    ]


def test_record_drops_sub_second_precision_from_the_delivery_time(conn: sqlite3.Connection) -> None:
    assert deliveries.record(conn, PLANTS, "2026-09-09T10:00:00+03:00", NOW.replace(microsecond=654321))

    assert full_rows(conn) == [(PLANTS, "2026-09-09T10:00:00+03:00", NOW_TEXT, None)]


# unread and mark_read


def test_unread_lists_the_open_deliveries_of_every_note_or_of_one(conn: sqlite3.Connection) -> None:
    deliver(conn, PLANTS, "2026-09-12T10:00:00+03:00")
    deliver(conn, PLANTS, "2026-09-09T10:00:00+03:00")
    deliver(conn, PLANTS, "2026-09-06T10:00:00+03:00", read_at="2026-09-06T11:00:00+03:00")
    deliver(conn, LIPS, "2026-09-15T18:00:00+03:00", delivered_at="2026-09-15T18:00:30+03:00")

    everything = deliveries.unread(conn)
    plants = deliveries.unread(conn, PLANTS)

    assert [(item.path, item.occurrence_at) for item in everything] == [
        (LIPS, "2026-09-15T18:00:00+03:00"),
        (PLANTS, "2026-09-09T10:00:00+03:00"),
        (PLANTS, "2026-09-12T10:00:00+03:00"),
    ]
    assert everything[0] == Delivery(4, LIPS, "2026-09-15T18:00:00+03:00", "2026-09-15T18:00:30+03:00", None)
    assert plants == everything[1:]
    assert deliveries.unread(conn, STANDUP) == []


def test_mark_read_closes_only_the_unread_rows_of_the_note_and_counts_them(conn: sqlite3.Connection) -> None:
    deliver(conn, PLANTS, "2026-09-09T10:00:00+03:00")
    deliver(conn, PLANTS, "2026-09-12T10:00:00+03:00")
    deliver(conn, PLANTS, "2026-09-06T10:00:00+03:00", read_at="2026-09-06T11:00:00+03:00")
    deliver(conn, LIPS, "2026-09-15T18:00:00+03:00")

    closed = deliveries.mark_read(conn, PLANTS, NOW)
    nothing = deliveries.mark_read(conn, PLANTS, NOW + timedelta(hours=1))

    assert (closed, nothing) == (2, 0)
    assert rows(conn) == [
        (LIPS, "2026-09-15T18:00:00+03:00", None),
        (PLANTS, "2026-09-06T10:00:00+03:00", "2026-09-06T11:00:00+03:00"),
        (PLANTS, "2026-09-09T10:00:00+03:00", NOW_TEXT),
        (PLANTS, "2026-09-12T10:00:00+03:00", NOW_TEXT),
    ]
    assert deliveries.unread(conn, PLANTS) == []


def test_mark_read_of_a_note_without_deliveries_is_zero(conn: sqlite3.Connection) -> None:
    assert deliveries.mark_read(conn, PLANTS, NOW) == 0
    assert rows(conn) == []


# tick


def test_tick_records_notifies_and_plays_the_sound_once_per_due_note(
    conn: sqlite3.Connection, recorded_commands: RecordedCommands
) -> None:
    insert_note(conn, PLANTS, schedule="at 2026-09-09T10:00:00+03:00", title="Water the plants")
    insert_note(conn, STANDUP, schedule=EVERY_3_DAYS, title="Standup")
    insert_note(conn, LIPS, kind="reminder", schedule="at 2026-09-15T18:00:00+03:00")

    delivered = deliveries.tick(conn, NotificationsConfig(), NOW)

    assert delivered == [
        Delivered(STANDUP, "Standup", "2026-09-08T10:00:00+03:00", NOW_TEXT, NotifyResult(True), NotifyResult(True)),
        Delivered(
            PLANTS, "Water the plants", "2026-09-09T10:00:00+03:00", NOW_TEXT, NotifyResult(True), NotifyResult(True)
        ),
    ]
    assert recorded_commands.commands() == [
        (*NOTIFY, "Standup", STANDUP),
        SOUND,
        (*NOTIFY, "Water the plants", PLANTS),
        SOUND,
    ]
    assert full_rows(conn) == [
        (STANDUP, "2026-09-08T10:00:00+03:00", NOW_TEXT, None),
        (PLANTS, "2026-09-09T10:00:00+03:00", NOW_TEXT, None),
    ]
    assert [item.warnings for item in delivered] == [[], []]


def test_tick_delivers_an_occurrence_once_and_the_next_one_after_the_period(
    conn: sqlite3.Connection, recorded_commands: RecordedCommands
) -> None:
    insert_note(conn, STANDUP, schedule=EVERY_3_DAYS)

    at_anchor = deliveries.tick(conn, NotificationsConfig(), ANCHOR)
    mid_period = deliveries.tick(conn, NotificationsConfig(), ANCHOR + timedelta(days=1))
    next_period = deliveries.tick(conn, NotificationsConfig(), ANCHOR + timedelta(days=3))

    assert [item.occurrence_at for item in at_anchor] == [ANCHOR_TEXT]
    assert mid_period == []
    assert [item.occurrence_at for item in next_period] == ["2026-09-05T10:00:00+03:00"]
    assert len(recorded_commands.commands("notify-send")) == 2
    assert len(recorded_commands.commands("canberra-gtk-play")) == 2


def test_tick_skips_an_occurrence_another_run_recorded_meanwhile(
    conn: sqlite3.Connection, recorded_commands: RecordedCommands
) -> None:
    insert_note(conn, PLANTS, schedule="at 2026-09-09T10:00:00+03:00")
    deliver(conn, PLANTS, "2026-09-09T10:00:00+03:00")

    assert deliveries.tick(conn, NotificationsConfig(), NOW) == []
    assert recorded_commands.commands() == []


def test_tick_with_the_sound_off_never_plays_it(conn: sqlite3.Connection, recorded_commands: RecordedCommands) -> None:
    insert_note(conn, PLANTS, schedule="at 2026-09-09T10:00:00+03:00", title="Water the plants")

    delivered = deliveries.tick(conn, NotificationsConfig(sound=False), NOW)

    assert delivered[0].sound is None
    assert delivered[0].warnings == []
    assert recorded_commands.commands() == [(*NOTIFY, "Water the plants", PLANTS)]


def test_tick_plays_the_configured_sound(conn: sqlite3.Connection, recorded_commands: RecordedCommands) -> None:
    insert_note(conn, PLANTS, schedule="at 2026-09-09T10:00:00+03:00")

    deliveries.tick(conn, NotificationsConfig(sound_id="bell"), NOW)

    assert recorded_commands.commands("canberra-gtk-play") == [("canberra-gtk-play", "-i", "bell")]


def test_tick_reports_desktop_failures_as_warnings_and_keeps_the_delivery_unread(
    conn: sqlite3.Connection, recorded_commands: RecordedCommands
) -> None:
    insert_note(conn, PLANTS, schedule="at 2026-09-09T10:00:00+03:00")
    recorded_commands.script("notify-send", returncode=1, stderr="Failed to show notification: Could not connect\n")
    recorded_commands.missing("canberra-gtk-play")

    delivered = deliveries.tick(conn, NotificationsConfig(), NOW)

    assert len(delivered) == 1
    assert delivered[0].notified == NotifyResult(
        False, "notify-send failed: Failed to show notification: Could not connect"
    )
    assert delivered[0].sound == NotifyResult(False, "canberra-gtk-play is not installed or not on PATH")
    assert delivered[0].warnings == [
        f"{PLANTS}: notify-send failed: Failed to show notification: Could not connect",
        f"{PLANTS}: canberra-gtk-play is not installed or not on PATH",
    ]
    assert rows(conn) == [(PLANTS, "2026-09-09T10:00:00+03:00", None)]
    assert deliveries.tick(conn, NotificationsConfig(), NOW) == []


def test_delivered_warnings_name_only_what_failed() -> None:
    fine = Delivered(PLANTS, "T", ANCHOR_TEXT, NOW_TEXT, NotifyResult(True), NotifyResult(True))
    sound_failed = Delivered(PLANTS, "T", ANCHOR_TEXT, NOW_TEXT, NotifyResult(True), NotifyResult(False, "no sound"))

    assert fine.warnings == []
    assert sound_failed.warnings == [f"{PLANTS}: no sound"]


# move


def test_move_repoints_every_row_of_the_note_and_reports_the_count(conn: sqlite3.Connection) -> None:
    deliver(conn, OLD, "2026-09-09T10:00:00+03:00")
    deliver(conn, OLD, "2026-09-06T10:00:00+03:00", read_at="2026-09-06T11:00:00+03:00")
    deliver(conn, OTHER, "2026-09-09T10:00:00+03:00")

    moved = deliveries.move(conn, OLD, NEW)

    assert moved == 2
    assert rows(conn) == [
        (OTHER, "2026-09-09T10:00:00+03:00", None),
        (NEW, "2026-09-06T10:00:00+03:00", "2026-09-06T11:00:00+03:00"),
        (NEW, "2026-09-09T10:00:00+03:00", None),
    ]


def test_move_without_rows_is_nothing(conn: sqlite3.Connection) -> None:
    deliver(conn, OTHER, "2026-09-09T10:00:00+03:00")

    assert deliveries.move(conn, OLD, NEW) == 0
    assert rows(conn) == [(OTHER, "2026-09-09T10:00:00+03:00", None)]


def test_move_drops_the_leftovers_under_the_new_path(conn: sqlite3.Connection) -> None:
    deliver(conn, NEW, "2026-09-09T10:00:00+03:00")
    deliver(conn, NEW, "2026-08-01T10:00:00+03:00")
    deliver(conn, OLD, "2026-09-09T10:00:00+03:00")

    assert deliveries.move(conn, OLD, NEW) == 1
    assert rows(conn) == [(NEW, "2026-09-09T10:00:00+03:00", None)]


def test_clear_drops_every_row_of_one_path_and_reports_the_count(conn: sqlite3.Connection) -> None:
    deliver(conn, OLD, "2026-09-09T10:00:00+03:00")
    deliver(conn, OLD, "2026-09-06T10:00:00+03:00", read_at="2026-09-06T11:00:00+03:00")
    deliver(conn, OTHER, "2026-09-09T10:00:00+03:00")

    assert deliveries.clear(conn, OLD) == 2
    assert rows(conn) == [(OTHER, "2026-09-09T10:00:00+03:00", None)]


def test_clear_of_a_path_without_rows_is_nothing(conn: sqlite3.Connection) -> None:
    deliver(conn, OTHER, "2026-09-09T10:00:00+03:00")

    assert deliveries.clear(conn, OLD) == 0
    assert rows(conn) == [(OTHER, "2026-09-09T10:00:00+03:00", None)]
