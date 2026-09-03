"""Tests for `notes read`: the note's unread deliveries are closed and counted, nothing else marks a note read, the
next occurrence makes it unread again, and the command never touches Git.

The notes are written by hand and picked up by the pre-command sync. Deliveries come from `notes tick --now` with the
desktop calls recorded, or are inserted straight into the index."""

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

ANCHOR = "2026-09-02T10:00:00+03:00"
EVERY_3_DAYS = f"every 3 days from {ANCHOR}"
SEPTEMBER_5 = "2026-09-05T10:00:00+03:00"
SEPTEMBER_9 = "2026-09-09T10:00:00+03:00"
SEPTEMBER_12 = "2026-09-12T10:00:00+03:00"
FROZEN = "2026-09-02T12:00:00+03:00"
"""`frozen_now` as `read_at` is stamped: the canonical text of `FROZEN_NOW`."""

NO_H1 = f"---\nkind: reminder\nstatus: active\nschedule: at {SEPTEMBER_9}\n---\n\nNo heading.\n"

UNREAD = "Unread notes:"


def note_text(*, schedule: str = f"at {SEPTEMBER_9}", title: str = PLANTS_TITLE) -> str:
    return "\n".join(
        [
            "---",
            "kind: reminder",
            "status: active",
            "tags: [home]",
            f"schedule: {schedule}",
            "---",
            "",
            f"# {title}",
            "",
            "**Remind me:** Water the balcony plants.",
            "",
        ]
    )


def write(vault: Path, path: str, text: str) -> None:
    target = vault / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def read_state(vault: Path) -> list[tuple[str, str, str | None]]:
    """Every delivery as `(path, occurrence_at, read_at)`, by path and occurrence."""
    conn = sqlite3.connect(vault / "index.sqlite")
    try:
        query = "SELECT path, occurrence_at, read_at FROM deliveries ORDER BY path, occurrence_at"
        return [tuple(row) for row in conn.execute(query)]
    finally:
        conn.close()


def read(runner: CliRunner, note_id: str, *args: str) -> Result:
    result = runner.invoke(cli, ["read", note_id, *args])
    assert result.exit_code == 0, result.output
    return result


def tick(runner: CliRunner, now: str) -> Result:
    result = runner.invoke(cli, ["tick", "--now", now])
    assert result.exit_code == 0, result.output
    return result


def read_line(vault: Path, path: str, title: str | None, phrase: str) -> str:
    """One `notes read` line as printed: `read: [id](abs) - Title (<phrase>)`, the title only when the note has one."""
    link = f"[{path}]({vault / path})"
    if title is not None:
        link += f" - {title}"
    return f"read: {link} ({phrase})"


def unread_line(vault: Path, path: str, title: str) -> str:
    return f"[unread] reminder active [{path}]({vault / path}) - {title}"


# Closing deliveries


def test_read_closes_every_unread_delivery_of_the_note_and_reports_the_count(
    runner: CliRunner, vault: Path, frozen_now: datetime
) -> None:
    write(vault, PLANTS, note_text())
    write(vault, STANDUP, note_text(schedule=EVERY_3_DAYS, title=STANDUP_TITLE))
    runner.invoke(cli, ["sync"])
    deliver(vault, PLANTS, SEPTEMBER_9)
    deliver(vault, PLANTS, SEPTEMBER_12)
    deliver(vault, PLANTS, "2026-09-06T10:00:00+03:00", read_at="2026-09-06T11:00:00+03:00")
    deliver(vault, STANDUP, ANCHOR)

    result = read(runner, PLANTS)

    assert result.stdout == read_line(vault, PLANTS, PLANTS_TITLE, "closed 2 unread deliveries") + "\n"
    assert result.stderr == ""
    assert read_state(vault) == [
        (STANDUP, ANCHOR, None),
        (PLANTS, "2026-09-06T10:00:00+03:00", "2026-09-06T11:00:00+03:00"),
        (PLANTS, SEPTEMBER_9, FROZEN),
        (PLANTS, SEPTEMBER_12, FROZEN),
    ]


def test_read_with_nothing_unread_reports_zero_and_exits_0(runner: CliRunner, vault: Path) -> None:
    write(vault, PLANTS, note_text())
    runner.invoke(cli, ["sync"])
    deliver(vault, PLANTS, "2026-09-06T10:00:00+03:00", read_at="2026-09-06T11:00:00+03:00")

    result = read(runner, PLANTS)
    again = read(runner, PLANTS)

    assert result.stdout == again.stdout == read_line(vault, PLANTS, PLANTS_TITLE, "no unread deliveries") + "\n"
    assert result.stderr == ""
    assert read_state(vault) == [(PLANTS, "2026-09-06T10:00:00+03:00", "2026-09-06T11:00:00+03:00")]


def test_read_closes_a_single_delivery_in_the_singular(runner: CliRunner, vault: Path) -> None:
    write(vault, PLANTS, note_text())
    runner.invoke(cli, ["sync"])
    deliver(vault, PLANTS, SEPTEMBER_9)

    result = read(runner, PLANTS)

    assert result.stdout == read_line(vault, PLANTS, PLANTS_TITLE, "closed 1 unread delivery") + "\n"
    assert [(path, read_at is not None) for path, _, read_at in read_state(vault)] == [(PLANTS, True)]


def test_read_touches_only_the_named_note(runner: CliRunner, vault: Path) -> None:
    write(vault, PLANTS, note_text())
    write(vault, STANDUP, note_text(schedule=EVERY_3_DAYS, title=STANDUP_TITLE))
    runner.invoke(cli, ["sync"])
    deliver(vault, PLANTS, SEPTEMBER_9)
    deliver(vault, STANDUP, ANCHOR)

    read(runner, STANDUP)
    unread = runner.invoke(cli, ["list", "--unread"])

    assert unread.stdout == unread_line(vault, PLANTS, PLANTS_TITLE) + "\n"
    assert [(path, read_at is None) for path, _, read_at in read_state(vault)] == [(STANDUP, False), (PLANTS, True)]


# Unread surfacing before and after


def test_after_read_the_note_leaves_list_unread_and_the_recall_block(runner: CliRunner, vault: Path) -> None:
    write(vault, PLANTS, note_text())
    runner.invoke(cli, ["sync"])
    deliver(vault, PLANTS, SEPTEMBER_9)
    before_list = runner.invoke(cli, ["list", "--unread"])
    before_recall = runner.invoke(cli, ["recall"])

    read(runner, PLANTS)
    after_list = runner.invoke(cli, ["list", "--unread"])
    after_recall = runner.invoke(cli, ["recall"])
    after_query = runner.invoke(cli, ["recall", "home"])
    listed = runner.invoke(cli, ["list"])
    shown = json.loads(runner.invoke(cli, ["show", PLANTS, "--json"]).stdout)

    assert before_list.stdout == unread_line(vault, PLANTS, PLANTS_TITLE) + "\n"
    assert before_recall.stdout.splitlines()[0] == UNREAD
    assert (after_list.exit_code, after_list.stdout) == (0, "")
    assert (after_recall.exit_code, after_recall.stdout) == (0, "")
    assert after_query.stdout.splitlines() == [
        "Related notes:",
        f"- [tag: home, text] [{PLANTS}]({vault / PLANTS}) - {PLANTS_TITLE}",
    ]
    assert listed.stdout == f"reminder active [{PLANTS}]({vault / PLANTS}) - {PLANTS_TITLE}\n"
    assert shown["unread"] == 0


def test_the_next_occurrence_makes_a_read_note_unread_again(
    runner: CliRunner, vault: Path, recorded_commands: RecordedCommands
) -> None:
    write(vault, STANDUP, note_text(schedule=EVERY_3_DAYS, title=STANDUP_TITLE))
    tick(runner, ANCHOR)

    first = read(runner, STANDUP)
    quiet = runner.invoke(cli, ["list", "--unread"])
    tick(runner, SEPTEMBER_5)
    unread = runner.invoke(cli, ["list", "--unread"])
    recalled = runner.invoke(cli, ["recall"])
    second = read(runner, STANDUP)

    assert first.stdout == read_line(vault, STANDUP, STANDUP_TITLE, "closed 1 unread delivery") + "\n"
    assert quiet.stdout == ""
    assert unread.stdout == unread_line(vault, STANDUP, STANDUP_TITLE) + "\n"
    assert recalled.stdout.splitlines() == [UNREAD, f"- [unread] [{STANDUP}]({vault / STANDUP}) - {STANDUP_TITLE}"]
    assert second.stdout == first.stdout
    assert [(occurrence, read_at is not None) for _, occurrence, read_at in read_state(vault)] == [
        (ANCHOR, True),
        (SEPTEMBER_5, True),
    ]
    assert len(recorded_commands.commands("notify-send")) == 2


def test_showing_listing_searching_and_recalling_never_mark_a_note_read(runner: CliRunner, vault: Path) -> None:
    write(vault, PLANTS, note_text())
    runner.invoke(cli, ["sync"])
    deliver(vault, PLANTS, SEPTEMBER_9)

    for args in (["show", PLANTS], ["show", PLANTS, "--json"], ["list"], ["list", "--unread"], ["recall"]):
        result = runner.invoke(cli, args)
        assert result.exit_code == 0, result.output
    for args in (["recall", "water the plants"], ["search", "plants"], ["recall", "--json"]):
        result = runner.invoke(cli, args)
        assert result.exit_code == 0, result.output
        assert PLANTS in result.stdout
    unread = runner.invoke(cli, ["list", "--unread"])

    assert unread.stdout == unread_line(vault, PLANTS, PLANTS_TITLE) + "\n"
    assert read_state(vault) == [(PLANTS, SEPTEMBER_9, None)]


# IDs


@pytest.mark.parametrize("form", ["2026-09-02-water-the-plants.md", "absolute", PLANTS])
def test_read_accepts_every_id_form_and_prints_the_canonical_id(runner: CliRunner, vault: Path, form: str) -> None:
    write(vault, PLANTS, note_text())
    runner.invoke(cli, ["sync"])
    deliver(vault, PLANTS, SEPTEMBER_9)
    note_id = str(vault / PLANTS) if form == "absolute" else form

    result = read(runner, note_id)

    assert result.stdout == read_line(vault, PLANTS, PLANTS_TITLE, "closed 1 unread delivery") + "\n"


def test_read_of_a_note_in_a_subdirectory(runner: CliRunner, vault: Path) -> None:
    nested = "notes/home/2026-09-02-water-the-plants.md"
    write(vault, nested, note_text())
    runner.invoke(cli, ["sync"])
    deliver(vault, nested, SEPTEMBER_9)

    result = read(runner, "home/2026-09-02-water-the-plants.md")

    assert result.stdout == read_line(vault, nested, PLANTS_TITLE, "closed 1 unread delivery") + "\n"
    assert [(path, read_at is not None) for path, _, read_at in read_state(vault)] == [(nested, True)]


def test_read_of_an_invalid_note_closes_its_deliveries_and_prints_the_bare_link(runner: CliRunner, vault: Path) -> None:
    write(vault, PLANTS, NO_H1)
    deliver(vault, PLANTS, SEPTEMBER_9)

    result = read(runner, PLANTS)
    as_json = json.loads(read(runner, PLANTS, "--json").stdout)

    assert result.stdout == read_line(vault, PLANTS, None, "closed 1 unread delivery") + "\n"
    assert (as_json["title"], as_json["closed"]) == (None, 0)
    assert [(path, read_at is not None) for path, _, read_at in read_state(vault)] == [(PLANTS, True)]
    assert indexed_paths(vault) == []


GONE = "notes/2026-01-01-gone.md"
"""A note that was delivered and deleted since: its rows stay, but there is no note to read."""


@pytest.mark.parametrize(
    ("note_id", "message"),
    [
        ("nope.md", "no note at notes/nope.md"),
        (GONE, f"no note at {GONE}"),
        ("../config.toml", "../config.toml: not a path under notes/ in the vault"),
    ],
    ids=["missing", "deleted", "outside"],
)
def test_read_of_an_unknown_id_fails_and_changes_nothing(
    runner: CliRunner, vault: Path, note_id: str, message: str
) -> None:
    write(vault, PLANTS, note_text())
    runner.invoke(cli, ["sync"])
    deliver(vault, PLANTS, SEPTEMBER_9)
    deliver(vault, GONE, "2026-01-01T10:00:00+03:00")

    result = runner.invoke(cli, ["read", note_id])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == f"Error: {message}\n"
    assert read_state(vault) == [(GONE, "2026-01-01T10:00:00+03:00", None), (PLANTS, SEPTEMBER_9, None)]


def test_read_of_an_unknown_id_fails_as_json(runner: CliRunner, vault: Path) -> None:
    result = runner.invoke(cli, ["read", "nope.md", "--json"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert json.loads(result.stderr) == {"error": {"type": "usage_error", "message": "no note at notes/nope.md"}}


# Git: never a commit, never a push


def test_read_never_commits_or_pushes(
    runner: CliRunner, vault: Path, remote: Path, git_cmd: Git, recorded_commands: RecordedCommands
) -> None:
    git_cmd(vault, "remote", "add", "origin", str(remote))
    (vault / "config.toml").write_text("[git]\nauto_push = true\n", encoding="utf-8")
    write(vault, PLANTS, note_text())
    deliver(vault, PLANTS, SEPTEMBER_9)

    result = read(runner, PLANTS)

    assert result.stdout == read_line(vault, PLANTS, PLANTS_TITLE, "closed 1 unread delivery") + "\n"
    assert result.stderr == ""
    assert indexed_paths(vault) == [PLANTS]
    assert recorded_commands.commands("git") == []
    assert git_cmd(vault, "log", "--format=%s").splitlines() == ["notes: initialize vault"]
    assert git_cmd(vault, "status", "--porcelain").splitlines() == [" M config.toml", f"?? {PLANTS}"]
    assert git_cmd(remote, "for-each-ref", "refs/heads/master") == ""

    recorded_commands.passthrough("git")
    later = runner.invoke(cli, ["sync"])

    assert later.exit_code == 0, later.output
    assert git_cmd(vault, "log", "--format=%s", "-1").strip() == "notes: sync 2 files"
    assert PLANTS in git_cmd(vault, "ls-files").splitlines()
    assert git_cmd(remote, "for-each-ref", "refs/heads/master") != ""


# JSON


def test_read_json(runner: CliRunner, vault: Path, frozen_now: datetime) -> None:
    write(vault, PLANTS, note_text())
    runner.invoke(cli, ["sync"])
    deliver(vault, PLANTS, SEPTEMBER_9)
    deliver(vault, PLANTS, SEPTEMBER_12)

    closed = json.loads(read(runner, PLANTS, "--json").stdout)
    nothing = json.loads(read(runner, PLANTS, "--json").stdout)

    assert closed == {
        "path": PLANTS,
        "abs_path": str(vault / PLANTS),
        "title": PLANTS_TITLE,
        "closed": 2,
        "read_at": FROZEN,
    }
    assert nothing == {**closed, "closed": 0, "read_at": None}


# Registration


def test_read_is_listed_in_root_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "  read " in result.output


def test_read_help_needs_no_vault(runner: CliRunner, home: Path) -> None:
    result = runner.invoke(cli, ["read", "--help"])

    assert result.exit_code == 0
    assert "--json" in result.output
    assert " ID" in result.output
    assert not (home / ".notes").exists()


def test_read_fails_without_a_vault(runner: CliRunner, home: Path) -> None:
    result = runner.invoke(cli, ["read", PLANTS])

    assert result.exit_code == 1
    assert result.stderr == "Error: no vault at ~/.notes, run `notes init`\n"


def test_read_requires_exactly_one_id(runner: CliRunner, vault: Path) -> None:
    none = runner.invoke(cli, ["read"])
    two = runner.invoke(cli, ["read", PLANTS, STANDUP])

    assert (none.exit_code, two.exit_code) == (2, 2)
