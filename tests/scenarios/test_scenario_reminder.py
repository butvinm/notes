"""Scenario 4 from the plan, end to end through the CLI: receive a reminder.

A reminder with `every 3 days from <anchor>` produces exactly one delivery, one `notify-send`, and one sound at the
first `tick` after the anchor, none on the next tick within the same period, and one more after the period elapses;
a `tick` after a long gap creates one delivery only; `notes recall` lists it under `Unread notes` until `notes read`
closes it; the next occurrence makes it unread again. The module also covers the two edge cases of unread state:
it follows the note through `notes move`, and a rebuilt index delivers the current occurrence once more and the
historical ones never.

The desktop calls are recorded, never run; Git and the fake editor run for real.
"""

import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from notes.cli import cli
from notes.cli import new as cli_new
from tests.conftest import FakeEditor, RecordedCommands

Git = Callable[..., str]

STANDUP = "notes/2026-09-02-standup.md"
MOVED = "notes/2026-09-02-daily-standup.md"
TITLE = "Prepare the standup"
ANCHOR = "2026-09-02T10:00:00+03:00"
SCHEDULE = f"every 3 days from {ANCHOR}"
SEPTEMBER_5 = "2026-09-05T10:00:00+03:00"
SEPTEMBER_8 = "2026-09-08T10:00:00+03:00"
SEPTEMBER_20 = "2026-09-20T10:00:00+03:00"
SEPTEMBER_23 = "2026-09-23T10:00:00+03:00"

NOTIFY = ("notify-send", "--app-name", "notes", "--")
SOUND = ("canberra-gtk-play", "-i", "message-new-instant")
UNREAD = "Unread notes:"

STANDUP_TEXT = f"""\
---
kind: reminder
status: active
tags: [team]
schedule: {SCHEDULE}
---

# {TITLE}

**Remind me:** Collect yesterday's decisions before the standup.
"""


def write(vault: Path, path: str, text: str) -> None:
    (vault / path).write_text(text, encoding="utf-8")


def subjects(git_cmd: Git, repo: Path) -> list[str]:
    return git_cmd(repo, "log", "--format=%s").splitlines()


def delivery_rows(vault: Path) -> list[tuple[str, str, str | None]]:
    """Every delivery as `(path, occurrence_at, read_at)`, by path and occurrence."""
    conn = sqlite3.connect(vault / "index.sqlite")
    try:
        query = "SELECT path, occurrence_at, read_at FROM deliveries ORDER BY path, occurrence_at"
        return [tuple(row) for row in conn.execute(query)]
    finally:
        conn.close()


def desktop_calls(desktop: RecordedCommands) -> list[tuple[str, ...]]:
    """The recorded `notify-send` and `canberra-gtk-play` calls in order, the Git calls left out."""
    return [call for call in desktop.commands() if call[0] in ("notify-send", "canberra-gtk-play")]


def tick(runner: CliRunner, now: str) -> Result:
    result = runner.invoke(cli, ["tick", "--now", now])
    assert result.exit_code == 0, result.output
    return result


def delivered_line(vault: Path, path: str, occurrence: str) -> str:
    return f"delivered: [{path}]({vault / path}) - {TITLE} (due {occurrence})\n"


def unread_recall(vault: Path, path: str) -> list[str]:
    return [UNREAD, f"- [unread] [{path}]({vault / path}) - {TITLE}"]


def unread_list(vault: Path, path: str) -> str:
    return f"[unread] reminder active [{path}]({vault / path}) - {TITLE}\n"


def read_line(vault: Path, path: str, phrase: str) -> str:
    return f"read: [{path}]({vault / path}) - {TITLE} ({phrase})\n"


@pytest.fixture
def desktop(recorded_commands: RecordedCommands) -> RecordedCommands:
    """Record every command; Git runs for real, the desktop programs never do."""
    recorded_commands.passthrough("git")
    return recorded_commands


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch, desktop: RecordedCommands, fake_editor: FakeEditor) -> FakeEditor:
    """`notes new` believes it runs in a terminal, so it asks for the schedule and opens the fake editor, which is
    passed through the recorder and leaves the scaffolded file as it is."""
    desktop.passthrough(str(fake_editor.script))
    monkeypatch.setattr(cli_new, "interactive", lambda: True)
    return fake_editor


def test_receive_a_reminder(
    runner: CliRunner, vault: Path, git_cmd: Git, desktop: RecordedCommands, terminal: FakeEditor, frozen_now: datetime
) -> None:
    # The reminder is made in the terminal flow: the schedule is asked, the template is kept as the editor left it.
    new = runner.invoke(cli, ["new", "reminder", TITLE, "--name", "standup"], input=f"{SCHEDULE}\n")
    assert new.exit_code == 0, new.output
    assert new.stdout.endswith(f"[{STANDUP}]({vault / STANDUP}) - {TITLE}\n")
    assert f"schedule: {SCHEDULE}\n" in (vault / STANDUP).read_text(encoding="utf-8")
    assert terminal.paths == [str(vault / STANDUP)]
    assert subjects(git_cmd, vault) == [f"notes: update {STANDUP}", "notes: initialize vault"]

    # Before the anchor nothing is due; the first tick after it delivers once, with one notification and one sound.
    assert tick(runner, "2026-09-02T09:59:00+03:00").stdout == ""
    first = tick(runner, "2026-09-02T10:00:30+03:00")
    assert first.stdout == delivered_line(vault, STANDUP, ANCHOR)
    assert first.stderr == ""
    assert desktop_calls(desktop) == [(*NOTIFY, TITLE, STANDUP), SOUND]

    # Within the same period nothing more happens; after the period elapses one more delivery arrives.
    assert tick(runner, "2026-09-03T10:00:00+03:00").stdout == ""
    assert tick(runner, "2026-09-05T09:59:59+03:00").stdout == ""
    assert len(desktop_calls(desktop)) == 2
    second = tick(runner, SEPTEMBER_5)
    assert second.stdout == delivered_line(vault, STANDUP, SEPTEMBER_5)
    assert len(desktop_calls(desktop)) == 4

    # A long gap: one delivery for the latest occurrence only, the missed ones are never replayed.
    after_gap = tick(runner, "2026-09-22T15:00:00+03:00")
    assert after_gap.stdout == delivered_line(vault, STANDUP, SEPTEMBER_20)
    assert tick(runner, "2026-09-22T15:01:00+03:00").stdout == ""
    assert desktop_calls(desktop) == [(*NOTIFY, TITLE, STANDUP), SOUND] * 3
    assert delivery_rows(vault) == [
        (STANDUP, ANCHOR, None),
        (STANDUP, SEPTEMBER_5, None),
        (STANDUP, SEPTEMBER_20, None),
    ]

    # The note is unread, once, until `notes read` closes every unread delivery.
    assert runner.invoke(cli, ["recall"]).stdout.splitlines() == unread_recall(vault, STANDUP)
    assert runner.invoke(cli, ["recall", "kafka"]).stdout.splitlines() == unread_recall(vault, STANDUP)
    assert runner.invoke(cli, ["list", "--unread"]).stdout == unread_list(vault, STANDUP)
    read = runner.invoke(cli, ["read", STANDUP])
    assert read.exit_code == 0, read.output
    assert read.stdout == read_line(vault, STANDUP, "closed 3 unread deliveries")
    assert runner.invoke(cli, ["recall"]).stdout == ""
    assert runner.invoke(cli, ["list", "--unread"]).stdout == ""
    assert all(read_at is not None for _, _, read_at in delivery_rows(vault))

    # The next occurrence makes it unread again.
    assert tick(runner, SEPTEMBER_23).stdout == delivered_line(vault, STANDUP, SEPTEMBER_23)
    assert runner.invoke(cli, ["recall"]).stdout.splitlines() == unread_recall(vault, STANDUP)
    assert runner.invoke(cli, ["read", STANDUP]).stdout == read_line(vault, STANDUP, "closed 1 unread delivery")
    assert len(desktop_calls(desktop)) == 8

    # Neither `tick` nor `read` touched Git.
    assert subjects(git_cmd, vault) == [f"notes: update {STANDUP}", "notes: initialize vault"]
    assert git_cmd(vault, "status", "--porcelain") == ""


def test_unread_state_follows_a_move_and_a_rebuilt_index_delivers_the_current_occurrence_once(
    runner: CliRunner, vault: Path, git_cmd: Git, desktop: RecordedCommands
) -> None:
    write(vault, STANDUP, STANDUP_TEXT)
    assert runner.invoke(cli, ["sync"]).exit_code == 0
    assert tick(runner, "2026-09-02T10:00:30+03:00").stdout == delivered_line(vault, STANDUP, ANCHOR)

    # The move carries the unread delivery over to the new ID; the occurrence is not delivered a second time.
    moved = runner.invoke(cli, ["move", STANDUP, MOVED])
    assert moved.exit_code == 0, moved.output
    assert moved.stdout == f"[{MOVED}]({vault / MOVED}) - {TITLE}\n"
    assert subjects(git_cmd, vault)[0] == f"notes: move {STANDUP} -> {MOVED}"
    assert delivery_rows(vault) == [(MOVED, ANCHOR, None)]
    assert runner.invoke(cli, ["list", "--unread"]).stdout == unread_list(vault, MOVED)
    assert runner.invoke(cli, ["recall"]).stdout.splitlines() == unread_recall(vault, MOVED)
    assert tick(runner, "2026-09-02T11:00:00+03:00").stdout == ""
    assert runner.invoke(cli, ["read", MOVED]).stdout == read_line(vault, MOVED, "closed 1 unread delivery")
    assert tick(runner, SEPTEMBER_8).stdout == delivered_line(vault, MOVED, SEPTEMBER_8)
    assert len(desktop_calls(desktop)) == 4

    # Without the database the delivery history is gone: the current occurrence is delivered once more, and only it.
    for suffix in ("", "-wal", "-shm"):
        (vault / f"index.sqlite{suffix}").unlink(missing_ok=True)
    rebuilt = tick(runner, "2026-09-08T11:00:00+03:00")
    assert rebuilt.stdout == delivered_line(vault, MOVED, SEPTEMBER_8)
    assert tick(runner, "2026-09-08T11:01:00+03:00").stdout == ""
    assert delivery_rows(vault) == [(MOVED, SEPTEMBER_8, None)]
    assert len(desktop_calls(desktop)) == 6
    assert runner.invoke(cli, ["list", "--unread"]).stdout == unread_list(vault, MOVED)
