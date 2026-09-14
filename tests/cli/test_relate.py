"""Tests for `notes relate`: the relation lands in the source note's frontmatter, is indexed, and is committed.

The notes are written by hand and picked up by `notes sync`; every refusal leaves both files, the index, and the
repository as they were.
"""

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner

from notes.cli import cli

Git = Callable[..., str]

KAFKA = "notes/2026-09-02-kafka.md"
OLD = "notes/2026-09-01-old-kafka.md"
PROXY = "notes/archive/2026-08-01-proxy.md"
KAFKA_TITLE = "Project Atlas task updates over Kafka"
OLD_TITLE = "Kafka over WebSocket"
OLD_REFERENCE = "2026-09-01-old-kafka.md"

KAFKA_TEXT = (
    "---\nkind: decision\nstatus: active\ntags: [ATLAS-27, kafka]\nkeywords: []\n---\n\n"
    f"# {KAFKA_TITLE}\n\n**Decision:** Kafka.\n"
)
OLD_TEXT = f"---\nkind: decision # first take\nstatus: active\ntags: [ATLAS-27]\n---\n\n# {OLD_TITLE}\n\nOld.\n"
PROXY_TEXT = "---\nkind: fact\nstatus: active\n---\n\n# Proxy timeouts\n"
SUPERSEDES_OLD = f"related:\n  - relation: supersedes\n    note: {OLD_REFERENCE}\n"


def with_related(text: str, related: str) -> str:
    """`text` with the `related` lines inserted before the closing fence, where the rewriter puts a new key."""
    head, _, rest = text.partition("\n---\n")
    return f"{head}\n{related}---\n{rest}"


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


def relation_rows(vault: Path) -> list[tuple[str, str, str]]:
    conn = sqlite3.connect(vault / "index.sqlite")
    try:
        return [tuple(row) for row in conn.execute("SELECT src, relation, dst FROM relations ORDER BY src, relation")]
    finally:
        conn.close()


@pytest.fixture
def two_notes(runner: CliRunner, vault: Path) -> Path:
    """The vault with the Kafka decision and the older WebSocket one, synced and committed, unrelated so far."""
    write(vault, KAFKA, KAFKA_TEXT)
    write(vault, OLD, OLD_TEXT)
    result = runner.invoke(cli, ["sync"])
    assert result.exit_code == 0, result.output
    return vault


# The relation is written, indexed, and committed


def test_relate_appends_to_a_note_without_related_and_commits(runner: CliRunner, two_notes: Path, git_cmd: Git) -> None:
    vault = two_notes

    result = runner.invoke(cli, ["relate", KAFKA, "supersedes", OLD])

    assert result.exit_code == 0, result.output
    assert result.stdout == (
        f"supersedes: [{KAFKA}]({vault / KAFKA}) - {KAFKA_TITLE} -> [{OLD}]({vault / OLD}) - {OLD_TITLE}\n"
    )
    assert result.stderr == ""
    assert read(vault, KAFKA) == with_related(KAFKA_TEXT, SUPERSEDES_OLD)
    assert read(vault, OLD) == OLD_TEXT
    assert subjects(git_cmd, vault) == [f"notes: update {KAFKA}", "notes: sync 2 files", "notes: initialize vault"]
    assert status(git_cmd, vault) == []
    assert relation_rows(vault) == [(KAFKA, "supersedes", OLD)]


def test_relate_appends_to_an_existing_list_and_keeps_the_formatting(
    runner: CliRunner, two_notes: Path, git_cmd: Git
) -> None:
    vault = two_notes
    existing = f"related:\n  - relation: child\n    note: {OLD_REFERENCE}\n"
    write(vault, KAFKA, with_related(KAFKA_TEXT, existing))

    result = runner.invoke(cli, ["relate", KAFKA, "supersedes", OLD])

    assert result.exit_code == 0, result.output
    appended = f"{existing}  - relation: supersedes\n    note: {OLD_REFERENCE}\n"
    assert read(vault, KAFKA) == with_related(KAFKA_TEXT, appended)
    assert relation_rows(vault) == [(KAFKA, "child", OLD), (KAFKA, "supersedes", OLD)]
    assert subjects(git_cmd, vault)[:2] == [f"notes: update {KAFKA}", f"notes: update {KAFKA}"]
    assert status(git_cmd, vault) == []


def test_relate_keeps_a_flow_style_related_list_in_flow_style(runner: CliRunner, two_notes: Path) -> None:
    vault = two_notes
    write(vault, KAFKA, with_related(KAFKA_TEXT, f"related: [{{relation: child, note: {OLD_REFERENCE}}}]\n"))

    result = runner.invoke(cli, ["relate", KAFKA, "related", OLD])

    assert result.exit_code == 0, result.output
    flow = f"related: [{{relation: child, note: {OLD_REFERENCE}}}, {{relation: related, note: {OLD_REFERENCE}}}]\n"
    assert read(vault, KAFKA) == with_related(KAFKA_TEXT, flow)
    assert relation_rows(vault) == [(KAFKA, "child", OLD), (KAFKA, "related", OLD)]


def test_supersedes_gives_the_target_the_superseded_status_without_touching_it(
    runner: CliRunner, two_notes: Path
) -> None:
    vault = two_notes
    assert runner.invoke(cli, ["relate", KAFKA, "supersedes", OLD]).exit_code == 0

    listed = runner.invoke(cli, ["list", "--status", "superseded"])
    shown_old = json.loads(runner.invoke(cli, ["show", OLD, "--json"]).stdout)
    shown_kafka = json.loads(runner.invoke(cli, ["show", KAFKA, "--json"]).stdout)

    assert listed.exit_code == 0, listed.output
    assert listed.stdout == f"2026-09-01 decision superseded [{OLD}]({vault / OLD}) - {OLD_TITLE}\n"
    assert (shown_old["status"], shown_old["effective_status"], shown_old["related"]) == ("active", "superseded", [])
    assert shown_kafka["related"] == [{"relation": "supersedes", "note": OLD}]
    assert read(vault, OLD) == OLD_TEXT


def test_relate_across_directories_writes_relative_references(runner: CliRunner, two_notes: Path) -> None:
    vault = two_notes
    write(vault, PROXY, PROXY_TEXT)

    down = runner.invoke(cli, ["relate", KAFKA, "related", PROXY])
    up = runner.invoke(cli, ["relate", PROXY, "child", KAFKA])

    assert (down.exit_code, up.exit_code) == (0, 0), down.output + up.output
    assert down.stdout == (
        f"related: [{KAFKA}]({vault / KAFKA}) - {KAFKA_TITLE} -> [{PROXY}]({vault / PROXY}) - Proxy timeouts\n"
    )
    assert read(vault, KAFKA) == with_related(
        KAFKA_TEXT, "related:\n  - relation: related\n    note: archive/2026-08-01-proxy.md\n"
    )
    assert read(vault, PROXY) == with_related(
        PROXY_TEXT, "related:\n  - relation: child\n    note: ../2026-09-02-kafka.md\n"
    )
    assert relation_rows(vault) == [(KAFKA, "related", PROXY), (PROXY, "child", KAFKA)]


@pytest.mark.parametrize("form", ["bare", "absolute"])
def test_relate_accepts_every_id_form(runner: CliRunner, two_notes: Path, form: str) -> None:
    vault = two_notes
    if form == "bare":
        ids = [KAFKA.removeprefix("notes/"), OLD.removeprefix("notes/")]
    else:
        ids = [str(vault / KAFKA), str(vault / OLD)]

    result = runner.invoke(cli, ["relate", ids[0], "supersedes", ids[1]])

    assert result.exit_code == 0, result.output
    assert result.stdout.startswith(f"supersedes: [{KAFKA}]({vault / KAFKA}) - ")
    assert relation_rows(vault) == [(KAFKA, "supersedes", OLD)]


def test_target_only_has_to_exist(runner: CliRunner, two_notes: Path) -> None:
    vault = two_notes
    headless = "notes/2026-09-02-headless.md"
    write(vault, headless, "---\nstatus: active\n---\n\nno heading\n")

    result = runner.invoke(cli, ["relate", KAFKA, "related", headless])

    assert result.exit_code == 0, result.output
    assert result.stdout == f"related: [{KAFKA}]({vault / KAFKA}) - {KAFKA_TITLE} -> [{headless}]({vault / headless})\n"
    assert relation_rows(vault) == [(KAFKA, "related", headless)]


def test_relate_json(runner: CliRunner, two_notes: Path) -> None:
    vault = two_notes

    result = runner.invoke(cli, ["relate", KAFKA, "supersedes", OLD, "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "path": KAFKA,
        "abs_path": str(vault / KAFKA),
        "title": KAFKA_TITLE,
        "relation": "supersedes",
        "target": {"path": OLD, "abs_path": str(vault / OLD), "title": OLD_TITLE},
        "reference": OLD_REFERENCE,
    }
    assert result.stderr == ""


# Refusals leave everything as it was


def test_duplicate_relation_is_rejected(runner: CliRunner, two_notes: Path, git_cmd: Git) -> None:
    vault = two_notes
    assert runner.invoke(cli, ["relate", KAFKA, "supersedes", OLD]).exit_code == 0

    result = runner.invoke(cli, ["relate", KAFKA, "supersedes", OLD])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == f"Error: duplicate relation: {KAFKA} already has `supersedes {OLD_REFERENCE}`\n"
    assert read(vault, KAFKA) == with_related(KAFKA_TEXT, SUPERSEDES_OLD)
    assert subjects(git_cmd, vault) == [f"notes: update {KAFKA}", "notes: sync 2 files", "notes: initialize vault"]
    assert status(git_cmd, vault) == []
    assert relation_rows(vault) == [(KAFKA, "supersedes", OLD)]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ([KAFKA, "duplicate", OLD], "unknown relation `duplicate` (expected supersedes, child, or related)"),
        ([KAFKA, "related", KAFKA], "a note cannot relate to itself"),
        ([KAFKA, "supersedes", "nope.md"], "no note at notes/nope.md"),
        (["nope.md", "supersedes", OLD], "no note at notes/nope.md"),
    ],
    ids=["unknown relation", "self relation", "missing target", "missing source"],
)
def test_refusals_change_nothing(
    runner: CliRunner, two_notes: Path, git_cmd: Git, args: list[str], message: str
) -> None:
    vault = two_notes

    result = runner.invoke(cli, ["relate", *args])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == f"Error: {message}\n"
    assert read(vault, KAFKA) == KAFKA_TEXT
    assert read(vault, OLD) == OLD_TEXT
    assert subjects(git_cmd, vault) == ["notes: sync 2 files", "notes: initialize vault"]
    assert status(git_cmd, vault) == []
    assert relation_rows(vault) == []


def test_refusal_json(runner: CliRunner, two_notes: Path) -> None:
    result = runner.invoke(cli, ["relate", KAFKA, "duplicate", OLD, "--json"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "error": {
            "type": "usage_error",
            "message": "unknown relation `duplicate` (expected supersedes, child, or related)",
        }
    }


def test_invalid_source_is_refused_and_left_untouched(runner: CliRunner, two_notes: Path, git_cmd: Git) -> None:
    vault = two_notes
    bad = "notes/2026-09-02-bad.md"
    text = "---\nstatus: active\n---\n\n# No kind\n"
    write(vault, bad, text)

    result = runner.invoke(cli, ["relate", bad, "supersedes", OLD])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == f"Error: {bad} is not a valid note, so no relation was added; run `notes check`\n"
    assert read(vault, bad) == text
    assert status(git_cmd, vault) == [f"?? {bad}"]
    assert relation_rows(vault) == []


# Registration


def test_relate_is_listed_in_root_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "  relate " in result.output


def test_relate_help_needs_no_vault(runner: CliRunner, home: Path) -> None:
    result = runner.invoke(cli, ["relate", "--help"])

    assert result.exit_code == 0
    assert "ID RELATION OTHER-ID" in result.output
    assert not (home / ".notes").exists()


def test_relate_without_a_vault_fails(runner: CliRunner, home: Path) -> None:
    result = runner.invoke(cli, ["relate", KAFKA, "supersedes", OLD])

    assert result.exit_code == 1
    assert result.stderr == "Error: no vault at ~/.notes, run `notes init`\n"
