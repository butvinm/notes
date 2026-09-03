"""Scenario 5 from the plan, end to end through the CLI: return to an old decision.

A new note with `related: [{relation: supersedes, note: <old>}]` gives the old note the effective status
`superseded`; `notes recall "ATLAS-27"` finds the notes tagged `ATLAS-27` with the reason `issue: ATLAS-27`; the
default `notes search` prefers the active note and omits the superseded one unless it is matched exactly or `--all`
is given; `notes list --status superseded` shows it; the old file's content is unchanged. The relation is written by
hand in the first test and appended by `notes relate` in the second.
"""

import json
from collections.abc import Callable
from pathlib import Path

from click.testing import CliRunner

from notes.cli import cli

Git = Callable[..., str]

OLD = "notes/2026-08-27-project-atlas-websocket.md"
OLD_TITLE = "Project Atlas task updates over WebSocket"
NEW = "notes/2026-09-02-project-atlas-kafka-task-updates.md"
NEW_TITLE = "Project Atlas task updates over Kafka"
SUPERSEDES = "related:\n  - relation: supersedes\n    note: 2026-08-27-project-atlas-websocket.md\n"

OLD_TEXT = """\
---
kind: decision
status: active
paths: [~/Dev/exampleco/project-atlas]
tags: [ATLAS-27, project-atlas]
keywords: [WebSocket, push, Project Atlas]
references: []
---

# Project Atlas task updates over WebSocket

**Decision:** The backend pushes task updates to Project Atlas over a WebSocket connection.

**Rationale:** One connection per client is simple and needs no broker.
"""


def new_text(related: str = "") -> str:
    """The new decision, with `related` appended to the frontmatter when given."""
    return (
        "---\nkind: decision\nstatus: active\npaths: [~/Dev/exampleco/project-atlas]\n"
        "tags: [ATLAS-27, project-atlas, sync-worker]\nkeywords: [Kafka, Atlas, Project Atlas, replay]\n"
        f"references: [CLAUDE.md]\n{related}---\n\n"
        f"# {NEW_TITLE}\n\n"
        "**Decision:** Task updates flow from the backend to Project Atlas over Kafka instead of WebSocket.\n\n"
        "**Rationale:** The proxy drops WebSocket connections under load; Kafka gives durable delivery and replay.\n"
    )


def write(vault: Path, path: str, text: str) -> None:
    (vault / path).write_text(text, encoding="utf-8")


def subjects(git_cmd: Git, repo: Path) -> list[str]:
    return git_cmd(repo, "log", "--format=%s").splitlines()


def status(git_cmd: Git, repo: Path) -> list[str]:
    return git_cmd(repo, "status", "--porcelain").splitlines()


def reasons_by_id(output: str) -> dict[str, str]:
    """The bracketed reasons of every `search` or `recall` line, by ID; headings are skipped."""
    entries = [entry.removeprefix("- ") for entry in output.splitlines() if "] [" in entry]
    ids = [entry.split("] [", 1)[1].split("]", 1)[0] for entry in entries]
    reasons = [entry.split("] [", 1)[0].removeprefix("[") for entry in entries]
    return dict(zip(ids, reasons, strict=True))


def list_line(vault: Path, effective_status: str, path: str, title: str) -> str:
    return f"decision {effective_status} [{path}]({vault / path}) - {title}"


def test_return_to_an_old_decision(runner: CliRunner, vault: Path, git_cmd: Git, tmp_path: Path) -> None:
    write(vault, OLD, OLD_TEXT)
    assert runner.invoke(cli, ["sync"]).exit_code == 0
    write(vault, NEW, new_text(SUPERSEDES))

    # The old note's effective status is derived from the new note's relation.
    superseded = runner.invoke(cli, ["list", "--status", "superseded"])
    assert superseded.exit_code == 0, superseded.output
    assert superseded.stdout == list_line(vault, "superseded", OLD, OLD_TITLE) + "\n"
    assert runner.invoke(cli, ["list"]).stdout.splitlines() == [
        list_line(vault, "active", NEW, NEW_TITLE),
        list_line(vault, "superseded", OLD, OLD_TITLE),
    ]

    # The issue ID recalls both, whatever their status, with the reason spelled out.
    recalled = runner.invoke(cli, ["recall", "--cwd", str(tmp_path), "ATLAS-27"])
    assert recalled.exit_code == 0, recalled.output
    assert recalled.stdout.splitlines()[0] == "Related notes:"
    assert reasons_by_id(recalled.stdout) == {
        NEW: "issue: ATLAS-27, text",
        OLD: "issue: ATLAS-27, text, superseded",
    }

    # Text search prefers the active note; the superseded one appears only on an exact match or with --all.
    by_text = runner.invoke(cli, ["search", "WebSocket"])
    assert by_text.stdout == f"[text] [{NEW}]({vault / NEW}) - {NEW_TITLE}\n"
    by_issue = runner.invoke(cli, ["search", "ATLAS-27"])
    assert reasons_by_id(by_issue.stdout) == {
        NEW: "issue: ATLAS-27, text",
        OLD: "issue: ATLAS-27, text, superseded",
    }
    with_all = runner.invoke(cli, ["search", "WebSocket", "--all"])
    assert reasons_by_id(with_all.stdout) == {NEW: "text", OLD: "text, superseded"}

    # The old file was never touched: same bytes, one commit in its history, nothing pending.
    assert (vault / OLD).read_text(encoding="utf-8") == OLD_TEXT
    assert runner.invoke(cli, ["show", OLD]).stdout == OLD_TEXT
    assert git_cmd(vault, "log", "--format=%s", "--", OLD).splitlines() == [f"notes: update {OLD}"]
    assert subjects(git_cmd, vault) == [f"notes: update {NEW}", f"notes: update {OLD}", "notes: initialize vault"]
    assert status(git_cmd, vault) == []
    shown = json.loads(runner.invoke(cli, ["show", OLD, "--json"]).stdout)
    assert (shown["status"], shown["effective_status"]) == ("active", "superseded")


def test_relate_supersedes_derives_the_status_without_touching_the_old_file(
    runner: CliRunner, vault: Path, git_cmd: Git
) -> None:
    write(vault, OLD, OLD_TEXT)
    write(vault, NEW, new_text())
    assert runner.invoke(cli, ["sync"]).exit_code == 0
    assert runner.invoke(cli, ["list", "--status", "superseded"]).stdout == ""

    related = runner.invoke(cli, ["relate", NEW, "supersedes", OLD])

    assert related.exit_code == 0, related.output
    assert related.stdout == (
        f"supersedes: [{NEW}]({vault / NEW}) - {NEW_TITLE} -> [{OLD}]({vault / OLD}) - {OLD_TITLE}\n"
    )
    assert (vault / NEW).read_text(encoding="utf-8") == new_text(SUPERSEDES)
    assert (vault / OLD).read_text(encoding="utf-8") == OLD_TEXT
    assert subjects(git_cmd, vault) == [f"notes: update {NEW}", "notes: sync 2 files", "notes: initialize vault"]
    assert status(git_cmd, vault) == []
    assert runner.invoke(cli, ["list", "--status", "superseded"]).stdout == (
        list_line(vault, "superseded", OLD, OLD_TITLE) + "\n"
    )
    assert reasons_by_id(runner.invoke(cli, ["search", "WebSocket", "--all"]).stdout) == {
        NEW: "text",
        OLD: "text, superseded",
    }
