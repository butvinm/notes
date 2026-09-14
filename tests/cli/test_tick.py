"""Tests for `notes tick`: every due note gets one delivery, one desktop notification, and one sound; only the
latest occurrence counts, once, whatever the gaps, rebuilds, or overlapping runs; and the command never touches
Git.

The desktop calls are recorded, never run. Notes are written by hand and picked up by the pre-command sync; the
clock is fixed through the hidden `--now` option unless a test freezes `clock.now`."""

import json
import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from notes.cli import cli
from tests.conftest import RecordedCommands, deliver, indexed_paths

Git = Callable[..., str]

PLANTS = "notes/2026-09-02-water-the-plants.md"
PLANTS_TITLE = "Water the plants"
STANDUP = "notes/2026-09-01-standup.md"
STANDUP_TITLE = "Prepare the standup"
KAFKA = "notes/2026-09-02-kafka.md"

ANCHOR = "2026-09-02T10:00:00+03:00"
EVERY_3_DAYS = f"every 3 days from {ANCHOR}"
SEPTEMBER_9 = "2026-09-09T10:00:00+03:00"
AT_SEPTEMBER_9 = f"at {SEPTEMBER_9}"

NOTIFY = ("notify-send", "--app-name", "notes", "--")
SOUND = ("canberra-gtk-play", "-i", "message-new-instant")
NO_H1 = "---\nkind: reminder\nstatus: active\nschedule: at 2026-09-01T10:00:00+03:00\n---\n\nNo heading.\n"


@pytest.fixture(autouse=True)
def desktop(recorded_commands: RecordedCommands) -> RecordedCommands:
    """Every test records the desktop calls: `notify-send` and `canberra-gtk-play` must never run for real."""
    return recorded_commands


def note_text(
    *,
    kind: str = "reminder",
    status: str = "active",
    schedule: str | None = AT_SEPTEMBER_9,
    title: str = PLANTS_TITLE,
    supersedes: str | None = None,
) -> str:
    lines = ["---", f"kind: {kind}", f"status: {status}", "tags: [home]"]
    if schedule is not None:
        lines.append(f"schedule: {schedule}")
    if supersedes is not None:
        lines.extend(["related:", "  - relation: supersedes", f"    note: {supersedes}"])
    lines.extend(["---", "", f"# {title}", "", "**Remind me:** Water the balcony plants.", ""])
    return "\n".join(lines)


def write(vault: Path, path: str, text: str) -> None:
    target = vault / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def delivery_rows(vault: Path) -> list[tuple[str, str, str, str | None]]:
    conn = sqlite3.connect(vault / "index.sqlite")
    try:
        query = "SELECT path, occurrence_at, delivered_at, read_at FROM deliveries ORDER BY path, occurrence_at"
        return [tuple(row) for row in conn.execute(query)]
    finally:
        conn.close()


def desktop_calls(desktop: RecordedCommands) -> list[tuple[str, ...]]:
    """The recorded `notify-send` and `canberra-gtk-play` calls in order; the Git calls of `list` or `sync` invoked
    by a test alongside `tick` are not the point here (`tick` itself runs no Git, which one test checks)."""
    return [call for call in desktop.commands() if call[0] in ("notify-send", "canberra-gtk-play")]


def tick(runner: CliRunner, now: str, *args: str) -> Result:
    result = runner.invoke(cli, ["tick", "--now", now, *args])
    assert result.exit_code == 0, result.output
    return result


def delivered_line(vault: Path, path: str, title: str, occurrence: str) -> str:
    return f"delivered: [{path}]({vault / path}) - {title} (due {occurrence})"


# One-shot and recurring schedules


def test_a_one_shot_note_delivers_from_its_time_on_and_only_once(
    runner: CliRunner, vault: Path, desktop: RecordedCommands
) -> None:
    write(vault, PLANTS, note_text())

    before = tick(runner, "2026-09-09T09:59:59+03:00")
    at_time = tick(runner, SEPTEMBER_9)
    later = tick(runner, "2026-09-10T08:00:00+03:00")

    assert (before.stdout, before.stderr) == ("", "")
    assert at_time.stdout == delivered_line(vault, PLANTS, PLANTS_TITLE, SEPTEMBER_9) + "\n"
    assert at_time.stderr == ""
    assert (later.stdout, later.stderr) == ("", "")
    assert delivery_rows(vault) == [(PLANTS, SEPTEMBER_9, SEPTEMBER_9, None)]
    assert desktop_calls(desktop) == [(*NOTIFY, PLANTS_TITLE, PLANTS), SOUND]


def test_a_recurring_note_delivers_at_the_anchor_and_once_per_period(
    runner: CliRunner, vault: Path, desktop: RecordedCommands
) -> None:
    write(vault, STANDUP, note_text(schedule=EVERY_3_DAYS, title=STANDUP_TITLE))

    before = tick(runner, "2026-09-02T09:59:00+03:00")
    at_anchor = tick(runner, ANCHOR)
    mid_period = tick(runner, "2026-09-03T10:00:00+03:00")
    just_before_next = tick(runner, "2026-09-05T09:59:59+03:00")
    next_period = tick(runner, "2026-09-05T10:00:00+03:00")

    assert before.stdout == mid_period.stdout == just_before_next.stdout == ""
    assert at_anchor.stdout == delivered_line(vault, STANDUP, STANDUP_TITLE, ANCHOR) + "\n"
    assert next_period.stdout == delivered_line(vault, STANDUP, STANDUP_TITLE, "2026-09-05T10:00:00+03:00") + "\n"
    assert delivery_rows(vault) == [
        (STANDUP, ANCHOR, ANCHOR, None),
        (STANDUP, "2026-09-05T10:00:00+03:00", "2026-09-05T10:00:00+03:00", None),
    ]
    assert desktop.commands("notify-send") == [(*NOTIFY, STANDUP_TITLE, STANDUP)] * 2
    assert desktop.commands("canberra-gtk-play") == [SOUND] * 2


def test_a_long_gap_yields_one_delivery_and_one_notification(
    runner: CliRunner, vault: Path, desktop: RecordedCommands
) -> None:
    write(vault, STANDUP, note_text(schedule=EVERY_3_DAYS, title=STANDUP_TITLE))

    after_gap = tick(runner, "2026-09-22T15:00:00+03:00")
    again = tick(runner, "2026-09-22T15:01:00+03:00")

    assert after_gap.stdout == delivered_line(vault, STANDUP, STANDUP_TITLE, "2026-09-20T10:00:00+03:00") + "\n"
    assert again.stdout == ""
    assert delivery_rows(vault) == [(STANDUP, "2026-09-20T10:00:00+03:00", "2026-09-22T15:00:00+03:00", None)]
    assert desktop_calls(desktop) == [(*NOTIFY, STANDUP_TITLE, STANDUP), SOUND]


def test_a_rebuilt_database_gets_one_delivery_for_the_current_occurrence_only(
    runner: CliRunner, vault: Path, desktop: RecordedCommands
) -> None:
    write(vault, STANDUP, note_text(schedule=EVERY_3_DAYS, title=STANDUP_TITLE))
    for now in (ANCHOR, "2026-09-05T10:00:00+03:00", "2026-09-08T10:00:00+03:00"):
        tick(runner, now)
    assert len(delivery_rows(vault)) == 3
    for suffix in ("", "-wal", "-shm"):
        (vault / f"index.sqlite{suffix}").unlink(missing_ok=True)

    rebuilt = tick(runner, "2026-09-08T11:00:00+03:00")
    again = tick(runner, "2026-09-08T11:01:00+03:00")

    assert rebuilt.stdout == delivered_line(vault, STANDUP, STANDUP_TITLE, "2026-09-08T10:00:00+03:00") + "\n"
    assert again.stdout == ""
    assert delivery_rows(vault) == [(STANDUP, "2026-09-08T10:00:00+03:00", "2026-09-08T11:00:00+03:00", None)]
    assert len(desktop.commands("notify-send")) == 4
    assert indexed_paths(vault) == [STANDUP]


def test_several_due_notes_each_get_a_notification_and_a_sound_in_path_order(
    runner: CliRunner, vault: Path, desktop: RecordedCommands
) -> None:
    write(vault, PLANTS, note_text())
    write(vault, STANDUP, note_text(schedule=EVERY_3_DAYS, title=STANDUP_TITLE))

    result = tick(runner, "2026-09-09T12:00:00+03:00")

    assert result.stdout.splitlines() == [
        delivered_line(vault, STANDUP, STANDUP_TITLE, "2026-09-08T10:00:00+03:00"),
        delivered_line(vault, PLANTS, PLANTS_TITLE, SEPTEMBER_9),
    ]
    assert desktop_calls(desktop) == [(*NOTIFY, STANDUP_TITLE, STANDUP), SOUND, (*NOTIFY, PLANTS_TITLE, PLANTS), SOUND]


def test_the_next_occurrence_makes_a_read_note_unread_again(
    runner: CliRunner, vault: Path, frozen_now: datetime
) -> None:
    write(vault, STANDUP, note_text(schedule=EVERY_3_DAYS, title=STANDUP_TITLE))
    tick(runner, ANCHOR)
    conn = sqlite3.connect(vault / "index.sqlite")
    with conn:
        conn.execute("UPDATE deliveries SET read_at = '2026-09-02T11:00:00+03:00'")
    conn.close()
    assert runner.invoke(cli, ["list", "--unread"]).stdout == ""

    tick(runner, "2026-09-05T10:00:00+03:00")

    unread = runner.invoke(cli, ["list", "--unread"])
    assert unread.stdout == (
        f"[unread] 2026-09-01 reminder active [{STANDUP}]({vault / STANDUP}) - {STANDUP_TITLE} "
        "(every 3 days, next 2026-09-05 10:00)\n"
    )
    assert delivery_rows(vault) == [
        (STANDUP, ANCHOR, ANCHOR, "2026-09-02T11:00:00+03:00"),
        (STANDUP, "2026-09-05T10:00:00+03:00", "2026-09-05T10:00:00+03:00", None),
    ]


def test_an_occurrence_recorded_by_an_overlapping_run_is_not_notified_again(
    runner: CliRunner, vault: Path, desktop: RecordedCommands
) -> None:
    write(vault, PLANTS, note_text())
    runner.invoke(cli, ["sync"])
    deliver(vault, PLANTS, SEPTEMBER_9, delivered_at="2026-09-09T10:00:01+03:00")

    result = tick(runner, "2026-09-09T10:00:02+03:00")

    assert result.stdout == ""
    assert desktop_calls(desktop) == []
    assert delivery_rows(vault) == [(PLANTS, SEPTEMBER_9, "2026-09-09T10:00:01+03:00", None)]


# Which notes deliver


def test_superseded_and_archived_notes_never_deliver(runner: CliRunner, vault: Path, desktop: RecordedCommands) -> None:
    write(vault, PLANTS, note_text(status="archived"))
    write(vault, STANDUP, note_text(schedule="at 2026-09-01T10:00:00+03:00", title=STANDUP_TITLE))
    write(vault, KAFKA, note_text(kind="decision", schedule=None, title="Kafka", supersedes="2026-09-01-standup.md"))

    result = tick(runner, "2026-09-10T10:00:00+03:00")
    superseded = runner.invoke(cli, ["list", "--status", "superseded"])
    archived = runner.invoke(cli, ["list", "--status", "archived"])

    assert (result.stdout, result.stderr) == ("", "")
    assert delivery_rows(vault) == []
    assert desktop_calls(desktop) == []
    assert STANDUP in superseded.stdout
    assert PLANTS in archived.stdout


def test_a_note_without_a_schedule_never_delivers(runner: CliRunner, vault: Path, desktop: RecordedCommands) -> None:
    write(vault, KAFKA, note_text(kind="decision", schedule=None, title="Kafka"))

    result = tick(runner, "2026-09-10T10:00:00+03:00")

    assert result.stdout == ""
    assert delivery_rows(vault) == []
    assert desktop_calls(desktop) == []


def test_an_invalid_note_never_delivers(runner: CliRunner, vault: Path, desktop: RecordedCommands) -> None:
    write(vault, PLANTS, NO_H1)

    result = tick(runner, "2026-09-10T10:00:00+03:00")
    check = runner.invoke(cli, ["check"])

    assert (result.exit_code, result.stdout) == (0, "")
    assert delivery_rows(vault) == []
    assert desktop_calls(desktop) == []
    assert check.exit_code == 1
    assert PLANTS in check.stdout


def test_a_note_in_a_subdirectory_delivers_under_its_id(runner: CliRunner, vault: Path) -> None:
    nested = "notes/home/2026-09-02-water-the-plants.md"
    write(vault, nested, note_text())

    result = tick(runner, SEPTEMBER_9)

    assert result.stdout == delivered_line(vault, nested, PLANTS_TITLE, SEPTEMBER_9) + "\n"
    assert delivery_rows(vault) == [(nested, SEPTEMBER_9, SEPTEMBER_9, None)]


# The desktop


def test_a_notification_failure_is_a_warning_and_leaves_the_delivery_unread(
    runner: CliRunner, vault: Path, desktop: RecordedCommands
) -> None:
    write(vault, PLANTS, note_text())
    desktop.script("notify-send", returncode=1, stderr="Failed to show notification: Could not connect\n")

    result = tick(runner, SEPTEMBER_9)
    unread = runner.invoke(cli, ["list", "--unread"])

    assert result.stdout == delivered_line(vault, PLANTS, PLANTS_TITLE, SEPTEMBER_9) + "\n"
    assert result.stderr == f"warning: {PLANTS}: notify-send failed: Failed to show notification: Could not connect\n"
    assert delivery_rows(vault) == [(PLANTS, SEPTEMBER_9, SEPTEMBER_9, None)]
    assert desktop_calls(desktop) == [(*NOTIFY, PLANTS_TITLE, PLANTS), SOUND]
    assert unread.stdout == (
        f"[unread] 2026-09-02 reminder active [{PLANTS}]({vault / PLANTS}) - {PLANTS_TITLE} (at 2026-09-09 10:00)\n"
    )
    assert tick(runner, "2026-09-09T10:01:00+03:00").stdout == ""


def test_a_missing_notify_send_is_a_warning(runner: CliRunner, vault: Path, desktop: RecordedCommands) -> None:
    write(vault, PLANTS, note_text())
    desktop.missing("notify-send")

    result = tick(runner, SEPTEMBER_9)

    assert result.stderr == f"warning: {PLANTS}: notify-send is not installed or not on PATH\n"
    assert delivery_rows(vault) == [(PLANTS, SEPTEMBER_9, SEPTEMBER_9, None)]


def test_a_sound_failure_is_a_warning(runner: CliRunner, vault: Path, desktop: RecordedCommands) -> None:
    write(vault, PLANTS, note_text())
    desktop.script("canberra-gtk-play", returncode=1, stderr="Failed to play sound\n")

    result = tick(runner, SEPTEMBER_9)

    assert result.stdout == delivered_line(vault, PLANTS, PLANTS_TITLE, SEPTEMBER_9) + "\n"
    assert result.stderr == f"warning: {PLANTS}: canberra-gtk-play failed: Failed to play sound\n"
    assert delivery_rows(vault) == [(PLANTS, SEPTEMBER_9, SEPTEMBER_9, None)]


def test_sound_off_skips_canberra(runner: CliRunner, vault: Path, desktop: RecordedCommands) -> None:
    write(vault, PLANTS, note_text())
    (vault / "config.toml").write_text("[notifications]\nsound = false\n", encoding="utf-8")

    result = tick(runner, SEPTEMBER_9)

    assert result.stdout == delivered_line(vault, PLANTS, PLANTS_TITLE, SEPTEMBER_9) + "\n"
    assert result.stderr == ""
    assert desktop_calls(desktop) == [(*NOTIFY, PLANTS_TITLE, PLANTS)]


def test_the_sound_id_comes_from_the_config(runner: CliRunner, vault: Path, desktop: RecordedCommands) -> None:
    write(vault, PLANTS, note_text())
    (vault / "config.toml").write_text('[notifications]\nsound_id = "bell"\n', encoding="utf-8")

    tick(runner, SEPTEMBER_9)

    assert desktop.commands("canberra-gtk-play") == [("canberra-gtk-play", "-i", "bell")]


# Git: never a commit, never a push


def test_tick_never_commits_or_pushes(
    runner: CliRunner, vault: Path, remote: Path, git_cmd: Git, desktop: RecordedCommands
) -> None:
    git_cmd(vault, "remote", "add", "origin", str(remote))
    (vault / "config.toml").write_text("[git]\nauto_push = true\n", encoding="utf-8")
    write(vault, PLANTS, note_text())

    result = tick(runner, SEPTEMBER_9)

    assert result.stdout == delivered_line(vault, PLANTS, PLANTS_TITLE, SEPTEMBER_9) + "\n"
    assert result.stderr == ""
    assert indexed_paths(vault) == [PLANTS]
    assert desktop.commands("git") == []
    assert git_cmd(vault, "log", "--format=%s").splitlines() == ["notes: initialize vault"]
    assert git_cmd(vault, "status", "--porcelain").splitlines() == [" M config.toml", f"?? {PLANTS}"]
    assert git_cmd(remote, "for-each-ref", "refs/heads/master") == ""

    desktop.passthrough("git")
    later = runner.invoke(cli, ["sync"])

    assert later.exit_code == 0, later.output
    assert git_cmd(vault, "log", "--format=%s", "-1").strip() == "notes: sync 2 files"
    assert PLANTS in git_cmd(vault, "ls-files").splitlines()
    assert git_cmd(remote, "for-each-ref", "refs/heads/master") != ""


# The clock


def test_tick_reads_the_clock_when_now_is_not_given(runner: CliRunner, vault: Path, frozen_now: datetime) -> None:
    write(vault, PLANTS, note_text(schedule="at 2026-09-02T11:00:00+03:00"))
    write(vault, STANDUP, note_text(schedule="at 2026-09-02T12:00:01+03:00", title=STANDUP_TITLE))

    result = runner.invoke(cli, ["tick"])

    assert result.exit_code == 0, result.output
    assert result.stdout == delivered_line(vault, PLANTS, PLANTS_TITLE, "2026-09-02T11:00:00+03:00") + "\n"
    assert delivery_rows(vault) == [(PLANTS, "2026-09-02T11:00:00+03:00", "2026-09-02T12:00:00+03:00", None)]


@pytest.mark.parametrize("now", ["2026-09-09T10:00:00", "yesterday", "2026-13-40T10:00:00+03:00"])
def test_tick_rejects_a_naive_or_malformed_now(runner: CliRunner, vault: Path, now: str) -> None:
    write(vault, PLANTS, note_text())

    result = runner.invoke(cli, ["tick", "--now", now])

    assert result.exit_code == 2
    assert "Invalid value for '--now'" in result.output
    assert delivery_rows(vault) == []


def test_tick_now_accepts_z_and_omitted_seconds(runner: CliRunner, vault: Path) -> None:
    write(vault, PLANTS, note_text())

    result = tick(runner, "2026-09-09T07:00Z")

    assert delivery_rows(vault) == [(PLANTS, SEPTEMBER_9, "2026-09-09T07:00:00+00:00", None)]
    assert result.stdout == delivered_line(vault, PLANTS, PLANTS_TITLE, SEPTEMBER_9) + "\n"


# JSON


def test_tick_json(runner: CliRunner, vault: Path, desktop: RecordedCommands) -> None:
    write(vault, PLANTS, note_text())
    write(vault, STANDUP, note_text(schedule=EVERY_3_DAYS, title=STANDUP_TITLE))
    desktop.script("notify-send", returncode=1, stderr="Could not connect\n")

    result = runner.invoke(cli, ["tick", "--now", "2026-09-09T12:00:00+03:00", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {
            "path": STANDUP,
            "abs_path": str(vault / STANDUP),
            "title": STANDUP_TITLE,
            "occurrence_at": "2026-09-08T10:00:00+03:00",
            "delivered_at": "2026-09-09T12:00:00+03:00",
            "notified": False,
        },
        {
            "path": PLANTS,
            "abs_path": str(vault / PLANTS),
            "title": PLANTS_TITLE,
            "occurrence_at": SEPTEMBER_9,
            "delivered_at": "2026-09-09T12:00:00+03:00",
            "notified": False,
        },
    ]
    assert result.stderr.splitlines() == [
        f"warning: {STANDUP}: notify-send failed: Could not connect",
        f"warning: {PLANTS}: notify-send failed: Could not connect",
    ]


def test_tick_json_with_nothing_due_is_an_empty_list(runner: CliRunner, vault: Path) -> None:
    result = runner.invoke(cli, ["tick", "--now", SEPTEMBER_9, "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == []


# Registration


def test_tick_is_listed_in_root_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "  tick " in result.output


def test_tick_help_hides_now_and_needs_no_vault(runner: CliRunner, home: Path) -> None:
    result = runner.invoke(cli, ["tick", "--help"])

    assert result.exit_code == 0
    assert "--json" in result.output
    assert "--now" not in result.output
    assert not (home / ".notes").exists()


def test_tick_fails_without_a_vault(runner: CliRunner, home: Path) -> None:
    result = runner.invoke(cli, ["tick"])

    assert result.exit_code == 1
    assert result.stderr == "Error: no vault at ~/.notes, run `notes init`\n"
