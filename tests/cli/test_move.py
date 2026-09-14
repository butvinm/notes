"""Tests for `notes move`: the note is renamed, the references to it and its own references are rewritten, its
deliveries follow it, and the whole move lands in one commit.

The notes are written by hand and picked up by `notes sync`; every refusal leaves files, index, and repository as
they were.
"""

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner

from notes.cli import cli
from tests.conftest import RecordedCommands, deliver, indexed_paths

Git = Callable[..., str]

KAFKA = "notes/2026-09-02-kafka.md"
OLD = "notes/2026-09-01-old-kafka.md"
PROXY = "notes/2026-08-01-proxy.md"
NEW = "notes/2026-09-01-websocket-decision.md"
ARCHIVED = "notes/archive/2026-09-01-old-kafka.md"
KAFKA_TITLE = "Project Atlas task updates over Kafka"
OLD_TITLE = "Kafka over WebSocket"
OCCURRENCE = "2026-09-09T10:00:00+03:00"
NO_KIND = "---\nstatus: active\n---\n\n# No kind\n"


def kafka_text(reference: str = "2026-09-01-old-kafka.md") -> str:
    """The Kafka decision, which supersedes the old one through `reference`."""
    return (
        "---\nkind: decision\nstatus: active\ntags: [ATLAS-27, kafka]\n"
        f"related:\n  - relation: supersedes\n    note: {reference}\n---\n\n# {KAFKA_TITLE}\n\n**Decision:** Kafka.\n"
    )


def old_text(reference: str = "2026-08-01-proxy.md") -> str:
    """The old decision, related to the proxy fact through `reference`; a comment shows the rewriter keeps it."""
    return (
        "---\nkind: decision # first take\nstatus: active\ntags: [ATLAS-27]\n"
        f"related:\n  - relation: related\n    note: {reference}\n---\n\n# {OLD_TITLE}\n\nOld.\n"
    )


PROXY_TEXT = "---\nkind: fact\nstatus: active\n---\n\n# Proxy timeouts\n"


def write(vault: Path, path: str, text: str) -> None:
    target = vault / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def read(vault: Path, path: str) -> str:
    return (vault / path).read_text(encoding="utf-8")


def subjects(git_cmd: Git, repo: Path) -> list[str]:
    return git_cmd(repo, "log", "--format=%s").splitlines()


def status(git_cmd: Git, repo: Path) -> list[str]:
    return git_cmd(repo, "status", "--porcelain").splitlines()


def tracked(git_cmd: Git, repo: Path) -> list[str]:
    return git_cmd(repo, "ls-files", "--", "notes").splitlines()


def relation_rows(vault: Path) -> list[tuple[str, str, str]]:
    conn = sqlite3.connect(vault / "index.sqlite")
    try:
        return [tuple(row) for row in conn.execute("SELECT src, relation, dst FROM relations ORDER BY src, relation")]
    finally:
        conn.close()


def delivery_rows(vault: Path) -> list[tuple[str, str, str | None]]:
    conn = sqlite3.connect(vault / "index.sqlite")
    try:
        query = "SELECT path, occurrence_at, read_at FROM deliveries ORDER BY path, occurrence_at"
        return [tuple(row) for row in conn.execute(query)]
    finally:
        conn.close()


def invalid_paths(vault: Path) -> list[str]:
    conn = sqlite3.connect(vault / "index.sqlite")
    try:
        return [row[0] for row in conn.execute("SELECT path FROM invalid_files ORDER BY path")]
    finally:
        conn.close()


@pytest.fixture
def git_calls(recorded_commands: RecordedCommands) -> RecordedCommands:
    """Record every command while Git keeps running for real, so a test can see whether `git mv` was used."""
    recorded_commands.passthrough("git")
    return recorded_commands


@pytest.fixture
def three_notes(runner: CliRunner, vault: Path) -> Path:
    """The vault with the Kafka decision superseding the old one, which relates to the proxy fact; all committed."""
    write(vault, KAFKA, kafka_text())
    write(vault, OLD, old_text())
    write(vault, PROXY, PROXY_TEXT)
    result = runner.invoke(cli, ["sync"])
    assert result.exit_code == 0, result.output
    return vault


# The move rewrites references, follows deliveries, and commits


def test_move_within_notes_rewrites_incoming_and_outgoing_references_and_commits(
    runner: CliRunner, three_notes: Path, git_cmd: Git
) -> None:
    vault = three_notes

    result = runner.invoke(cli, ["move", OLD, NEW])

    assert result.exit_code == 0, result.output
    assert result.stdout == (
        f"[{NEW}]({vault / NEW}) - {OLD_TITLE}\nupdated: [{KAFKA}]({vault / KAFKA}) - {KAFKA_TITLE}\n"
    )
    assert result.stderr == ""
    assert not (vault / OLD).exists()
    assert read(vault, NEW) == old_text()
    assert read(vault, KAFKA) == kafka_text("2026-09-01-websocket-decision.md")
    assert read(vault, PROXY) == PROXY_TEXT
    assert subjects(git_cmd, vault) == [f"notes: move {OLD} -> {NEW}", "notes: sync 3 files", "notes: initialize vault"]
    assert status(git_cmd, vault) == []
    assert tracked(git_cmd, vault) == sorted([KAFKA, NEW, PROXY, "notes/.gitkeep"])
    assert relation_rows(vault) == [(NEW, "related", PROXY), (KAFKA, "supersedes", NEW)]
    assert indexed_paths(vault) == sorted([KAFKA, NEW, PROXY])


def test_list_shows_the_new_id_only_with_the_derived_status_intact(runner: CliRunner, three_notes: Path) -> None:
    vault = three_notes
    assert runner.invoke(cli, ["move", OLD, NEW]).exit_code == 0

    listed = runner.invoke(cli, ["list"])
    superseded = runner.invoke(cli, ["list", "--status", "superseded"])

    assert listed.exit_code == 0, listed.output
    assert OLD not in listed.stdout
    assert f"[{NEW}]({vault / NEW}) - {OLD_TITLE}" in listed.stdout
    assert superseded.stdout == f"decision superseded [{NEW}]({vault / NEW}) - {OLD_TITLE}\n"


def test_move_into_a_subdirectory_rewrites_relative_references_in_both_directions(
    runner: CliRunner, three_notes: Path, git_cmd: Git
) -> None:
    vault = three_notes

    down = runner.invoke(cli, ["move", OLD, ARCHIVED])

    assert down.exit_code == 0, down.output
    assert read(vault, ARCHIVED) == old_text("../2026-08-01-proxy.md")
    assert read(vault, KAFKA) == kafka_text("archive/2026-09-01-old-kafka.md")
    assert relation_rows(vault) == [(KAFKA, "supersedes", ARCHIVED), (ARCHIVED, "related", PROXY)]

    up = runner.invoke(cli, ["move", ARCHIVED, OLD])

    assert up.exit_code == 0, up.output
    assert read(vault, OLD) == old_text()
    assert read(vault, KAFKA) == kafka_text()
    assert relation_rows(vault) == [(OLD, "related", PROXY), (KAFKA, "supersedes", OLD)]
    assert subjects(git_cmd, vault)[:2] == [f"notes: move {ARCHIVED} -> {OLD}", f"notes: move {OLD} -> {ARCHIVED}"]
    assert status(git_cmd, vault) == []


def test_unread_deliveries_follow_the_note(runner: CliRunner, three_notes: Path) -> None:
    vault = three_notes
    deliver(vault, OLD, OCCURRENCE)
    deliver(vault, OLD, "2026-09-06T10:00:00+03:00", read_at="2026-09-06T11:00:00+03:00")
    deliver(vault, KAFKA, OCCURRENCE)

    result = runner.invoke(cli, ["move", OLD, NEW])
    unread = runner.invoke(cli, ["list", "--unread"])

    assert result.exit_code == 0, result.output
    assert delivery_rows(vault) == [
        (NEW, "2026-09-06T10:00:00+03:00", "2026-09-06T11:00:00+03:00"),
        (NEW, OCCURRENCE, None),
        (KAFKA, OCCURRENCE, None),
    ]
    assert unread.stdout == (
        f"[unread] decision active     [{KAFKA}]({vault / KAFKA}) - {KAFKA_TITLE}\n"
        f"[unread] decision superseded [{NEW}]({vault / NEW}) - {OLD_TITLE}\n"
    )


def test_tracked_note_is_moved_with_git_mv(runner: CliRunner, git_calls: RecordedCommands, three_notes: Path) -> None:
    result = runner.invoke(cli, ["move", OLD, NEW])

    assert result.exit_code == 0, result.output
    assert git_calls.commands("git", "mv") == [("git", "mv", "--", OLD, NEW)]


def test_untracked_note_is_renamed_without_git_mv(
    runner: CliRunner, git_calls: RecordedCommands, three_notes: Path, git_cmd: Git
) -> None:
    vault = three_notes
    undated = "notes/kafka-undated.md"
    dated = "notes/2026-09-02-kafka-undated.md"
    write(vault, undated, PROXY_TEXT)
    checked = runner.invoke(cli, ["check"])
    assert checked.exit_code == 1
    assert status(git_cmd, vault) == [f"?? {undated}"]

    result = runner.invoke(cli, ["move", undated, dated])

    assert result.exit_code == 0, result.output
    assert result.stdout == f"[{dated}]({vault / dated}) - Proxy timeouts\n"
    assert git_calls.commands("git", "mv") == []
    assert read(vault, dated) == PROXY_TEXT
    assert subjects(git_cmd, vault)[0] == f"notes: move {undated} -> {dated}"
    assert status(git_cmd, vault) == []
    assert dated in tracked(git_cmd, vault)
    assert dated in indexed_paths(vault)
    assert invalid_paths(vault) == []


@pytest.mark.parametrize("form", ["bare", "absolute"])
def test_move_accepts_every_id_form_for_both_arguments(runner: CliRunner, three_notes: Path, form: str) -> None:
    vault = three_notes
    if form == "bare":
        args = [OLD.removeprefix("notes/"), NEW.removeprefix("notes/")]
    else:
        args = [str(vault / OLD), str(vault / NEW)]

    result = runner.invoke(cli, ["move", *args])

    assert result.exit_code == 0, result.output
    assert result.stdout.startswith(f"[{NEW}]({vault / NEW}) - {OLD_TITLE}\n")
    assert (vault / NEW).is_file()
    assert not (vault / OLD).exists()


def test_move_json(runner: CliRunner, three_notes: Path) -> None:
    vault = three_notes

    result = runner.invoke(cli, ["move", OLD, NEW, "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "path": NEW,
        "abs_path": str(vault / NEW),
        "title": OLD_TITLE,
        "valid": True,
        "errors": [],
        "old_path": OLD,
        "updated": [{"path": KAFKA, "abs_path": str(vault / KAFKA), "title": KAFKA_TITLE}],
    }
    assert result.stderr == ""


# A note that is still invalid after the move


def test_untracked_note_still_invalid_after_the_move_is_reported_and_left_uncommitted(
    runner: CliRunner, three_notes: Path, git_cmd: Git
) -> None:
    vault = three_notes
    undated = "notes/kafka-undated.md"
    dated = "notes/2026-09-02-kafka-undated.md"
    write(vault, undated, NO_KIND)
    assert runner.invoke(cli, ["sync"]).exit_code == 0

    result = runner.invoke(cli, ["move", undated, dated])

    assert result.exit_code == 1
    assert result.stdout == f"[{dated}]({vault / dated}) - No kind\n"
    assert result.stderr == (
        f"{vault / dated}:1: kind is required\n"
        f"Error: {dated} is not a valid note and stays uncommitted; fix it and run `notes sync`\n"
    )
    assert read(vault, dated) == NO_KIND
    assert status(git_cmd, vault) == [f"?? {dated}"]
    assert subjects(git_cmd, vault) == ["notes: sync 3 files", "notes: initialize vault"]
    assert invalid_paths(vault) == [dated]


def test_tracked_note_still_invalid_after_the_move_stays_at_head_until_it_is_fixed(
    runner: CliRunner, three_notes: Path, git_cmd: Git
) -> None:
    """The rename is left out of the commit whole, so Git keeps the note under its old name until it is valid."""
    vault = three_notes
    broken = old_text().replace("\nOld.\n", "\n# Second\n")
    write(vault, OLD, broken)

    result = runner.invoke(cli, ["move", OLD, NEW, "--json"])

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert (data["path"], data["old_path"], data["valid"]) == (NEW, OLD, False)
    assert data["errors"] == [{"line": 12, "message": "more than one H1: a note has exactly one title"}]
    assert result.stderr == ""
    assert read(vault, NEW) == broken
    assert read(vault, KAFKA) == kafka_text("2026-09-01-websocket-decision.md")
    assert status(git_cmd, vault) == [f" D {OLD}", f"?? {NEW}"]
    assert subjects(git_cmd, vault)[0] == f"notes: move {OLD} -> {NEW}"
    assert OLD in tracked(git_cmd, vault)
    assert NEW not in tracked(git_cmd, vault)
    assert invalid_paths(vault) == [NEW]

    (vault / NEW).write_text(old_text(), encoding="utf-8")
    assert runner.invoke(cli, ["sync"]).exit_code == 0
    assert subjects(git_cmd, vault)[0] == "notes: sync 2 files"
    assert status(git_cmd, vault) == []
    assert OLD not in tracked(git_cmd, vault)
    assert NEW in tracked(git_cmd, vault)


# Refusals leave everything as it was


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ([OLD, KAFKA], f"{KAFKA} already exists"),
        ([OLD, "archive"], "notes/archive: a note file must end with .md"),
        ([OLD, "../config.toml"], "../config.toml: not a path under notes/ in the vault"),
        ([OLD, "/etc/2026-09-01-hostname.md"], "/etc/2026-09-01-hostname.md: not a path under notes/ in the vault"),
        ([OLD, "2026-09-01-websocket.txt"], "notes/2026-09-01-websocket.txt: a note file must end with .md"),
        ([OLD, "websocket.md"], "filename `websocket.md` must look like YYYY-MM-DD-<slug>.md"),
        ([OLD, "2026-13-40-websocket.md"], "filename `2026-13-40-websocket.md` has an impossible date 2026-13-40"),
        ([OLD, OLD], f"{OLD} is already the note's path"),
        (["nope.md", NEW], "no note at notes/nope.md"),
    ],
    ids=[
        "target exists",
        "target is a directory",
        "outside notes",
        "outside the vault",
        "wrong extension",
        "undated filename",
        "impossible date",
        "same path",
        "missing source",
    ],
)
def test_refusals_change_nothing(
    runner: CliRunner, three_notes: Path, git_cmd: Git, args: list[str], message: str
) -> None:
    vault = three_notes
    (vault / "notes" / "archive").mkdir()
    deliver(vault, OLD, OCCURRENCE)

    result = runner.invoke(cli, ["move", *args])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == f"Error: {message}\n"
    assert read(vault, OLD) == old_text()
    assert read(vault, KAFKA) == kafka_text()
    assert not (vault / NEW).exists()
    assert subjects(git_cmd, vault) == ["notes: sync 3 files", "notes: initialize vault"]
    assert status(git_cmd, vault) == []
    assert relation_rows(vault) == [(OLD, "related", PROXY), (KAFKA, "supersedes", OLD)]
    assert delivery_rows(vault) == [(OLD, OCCURRENCE, None)]


def test_refusal_json(runner: CliRunner, three_notes: Path) -> None:
    result = runner.invoke(cli, ["move", OLD, KAFKA, "--json"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert json.loads(result.stderr) == {"error": {"type": "usage_error", "message": f"{KAFKA} already exists"}}


# Registration


def test_move_is_listed_in_root_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "  move " in result.output


def test_move_help_needs_no_vault(runner: CliRunner, home: Path) -> None:
    result = runner.invoke(cli, ["move", "--help"])

    assert result.exit_code == 0
    assert "ID NEW-PATH" in result.output
    assert not (home / ".notes").exists()


def test_move_without_a_vault_fails(runner: CliRunner, home: Path) -> None:
    result = runner.invoke(cli, ["move", OLD, NEW])

    assert result.exit_code == 1
    assert result.stderr == "Error: no vault at ~/.notes, run `notes init`\n"
