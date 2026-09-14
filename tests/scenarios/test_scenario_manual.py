"""Scenario 3 from the plan, end to end through the CLI: work manually without an agent.

A file written straight into `~/.notes/notes/` is validated by `notes check` and listed by `notes list`, which
synchronizes on its own, and a commit is made locally; with an unreachable remote and `auto_push` on, the push
fails as a warning and succeeds on a later command once the remote is reachable. The module also covers the edge
cases of the same workflow: an invalid file is kept out of the index and out of Git until it is fixed by hand, and
a deleted `index.sqlite` is rebuilt from the Markdown by the next command without a commit.
"""

from collections.abc import Callable
from pathlib import Path

from click.testing import CliRunner

from notes.cli import cli
from tests.conftest import indexed_paths

Git = Callable[..., str]

KAFKA = "notes/2026-09-02-kafka-task-updates.md"
KAFKA_TITLE = "Project Atlas task updates over Kafka"
PROXY = "notes/2026-09-03-proxy-timeouts.md"
PROXY_TITLE = "Legacy proxy drops idle WebSocket connections"
STANDUP = "notes/2026-09-04-standup.md"
STANDUP_TITLE = "Standup moved to 10:30"

KAFKA_TEXT = """\
---
kind: decision
status: active
paths: [~/Dev/exampleco/project-atlas]
tags: [ATLAS-27, project-atlas]
keywords: [Kafka, Project Atlas]
references: [CLAUDE.md]
---

# Project Atlas task updates over Kafka

**Decision:** Task updates flow from the backend to Project Atlas over Kafka instead of WebSocket.

**Rationale:** The proxy drops WebSocket connections under load; Kafka gives durable delivery.
"""

PROXY_TEXT = """\
---
kind: fact
status: active
tags: [legacy, proxy]
---

# Legacy proxy drops idle WebSocket connections

**Fact:** The legacy proxy closes idle WebSocket connections after sixty seconds.

**Source:** The ops runbook.
"""

BROKEN_TEXT = """\
---
status: active
tags: [team]
---

# Standup moved to 10:30

**Fact:** The daily standup moved from 10:00 to 10:30.

**Source:** The team channel, 2026-07-01.
"""
"""A note without its `kind`: invalid until the line is added by hand."""

FIXED_TEXT = BROKEN_TEXT.replace("---\nstatus: active\n", "---\nkind: fact\nstatus: active\n", 1)


def write(vault: Path, path: str, text: str) -> None:
    (vault / path).write_text(text, encoding="utf-8")


def subjects(git_cmd: Git, repo: Path) -> list[str]:
    return git_cmd(repo, "log", "--format=%s").splitlines()


def status(git_cmd: Git, repo: Path) -> list[str]:
    return git_cmd(repo, "status", "--porcelain").splitlines()


def head(git_cmd: Git, repo: Path) -> str:
    return git_cmd(repo, "rev-parse", "HEAD").strip()


def line(vault: Path, kind: str, effective_status: str, path: str, title: str, width: int = 0) -> str:
    """One `notes list` line as printed; `width` pads the kind column when the listing mixes kinds."""
    return f"{kind.ljust(width)} {effective_status} [{path}]({vault / path}) - {title}"


def test_work_manually_without_an_agent(runner: CliRunner, home: Path, git_cmd: Git, tmp_path: Path) -> None:
    vault = home / ".notes"
    assert runner.invoke(cli, ["init", "--auto-push"]).exit_code == 0

    # A note written by hand is checked, listed, and committed; with no remote configured nothing is pushed.
    write(vault, KAFKA, KAFKA_TEXT)
    checked = runner.invoke(cli, ["check"])
    assert checked.exit_code == 0, checked.output
    assert checked.output == "ok: 1 note indexed, no invalid files\n"
    listed = runner.invoke(cli, ["list"])
    assert listed.exit_code == 0, listed.output
    assert listed.stdout == line(vault, "decision", "active", KAFKA, KAFKA_TITLE) + "\n"
    assert listed.stderr == ""
    assert subjects(git_cmd, vault) == [f"notes: update {KAFKA}", "notes: initialize vault"]
    assert status(git_cmd, vault) == []

    # A remote that cannot be reached: the next note is committed, the push fails as a warning.
    remote = tmp_path / "remote.git"
    git_cmd(vault, "remote", "add", "origin", str(remote))
    write(vault, PROXY, PROXY_TEXT)
    offline = runner.invoke(cli, ["list"])
    assert offline.exit_code == 0, offline.output
    assert offline.stdout.splitlines() == [
        line(vault, "fact", "active", PROXY, PROXY_TITLE, width=len("decision")),
        line(vault, "decision", "active", KAFKA, KAFKA_TITLE),
    ]
    assert offline.stderr.startswith("warning: push to origin failed: ")
    assert offline.stderr.rstrip().endswith("; the commit is kept and the next command retries the push")
    assert subjects(git_cmd, vault)[0] == f"notes: update {PROXY}"
    assert status(git_cmd, vault) == []

    # The remote becomes reachable: the next command pushes everything and sets the upstream.
    git_cmd(tmp_path, "init", "-q", "--bare", "-b", "master", str(remote))
    online = runner.invoke(cli, ["check"])
    assert online.exit_code == 0, online.output
    assert online.stderr == ""
    assert git_cmd(vault, "rev-parse", "--abbrev-ref", "@{upstream}").strip() == "origin/master"
    assert git_cmd(remote, "rev-parse", "refs/heads/master").strip() == head(git_cmd, vault)


def test_an_invalid_file_stays_out_of_the_index_and_out_of_git_until_it_is_fixed_by_hand(
    runner: CliRunner, vault: Path, git_cmd: Git
) -> None:
    write(vault, KAFKA, KAFKA_TEXT)
    write(vault, STANDUP, BROKEN_TEXT)

    checked = runner.invoke(cli, ["check"])
    listed = runner.invoke(cli, ["list"])
    searched = runner.invoke(cli, ["search", "standup"])

    assert checked.exit_code == 1
    assert checked.stdout.splitlines() == [
        f"{vault / STANDUP}:1: kind is required",
        "1 invalid file, 1 valid note indexed",
    ]
    assert listed.stdout == line(vault, "decision", "active", KAFKA, KAFKA_TITLE) + "\n"
    assert searched.stdout == ""
    assert indexed_paths(vault) == [KAFKA]
    assert subjects(git_cmd, vault) == [f"notes: update {KAFKA}", "notes: initialize vault"]
    assert status(git_cmd, vault) == [f"?? {STANDUP}"]

    write(vault, STANDUP, FIXED_TEXT)
    listed_again = runner.invoke(cli, ["list"])
    checked_again = runner.invoke(cli, ["check"])
    searched_again = runner.invoke(cli, ["search", "standup"])

    assert listed_again.stdout.splitlines() == [
        line(vault, "fact", "active", STANDUP, STANDUP_TITLE, width=len("decision")),
        line(vault, "decision", "active", KAFKA, KAFKA_TITLE),
    ]
    assert checked_again.output == "ok: 2 notes indexed, no invalid files\n"
    assert searched_again.stdout == f"[text] [{STANDUP}]({vault / STANDUP}) - {STANDUP_TITLE}\n"
    assert indexed_paths(vault) == [KAFKA, STANDUP]
    assert subjects(git_cmd, vault)[0] == f"notes: update {STANDUP}"
    assert status(git_cmd, vault) == []


def test_a_deleted_index_is_rebuilt_from_the_markdown_without_a_commit(
    runner: CliRunner, vault: Path, git_cmd: Git
) -> None:
    write(vault, KAFKA, KAFKA_TEXT)
    write(vault, PROXY, PROXY_TEXT)
    assert runner.invoke(cli, ["sync"]).exit_code == 0
    committed_before = subjects(git_cmd, vault)
    for suffix in ("", "-wal", "-shm"):
        (vault / f"index.sqlite{suffix}").unlink(missing_ok=True)

    listed = runner.invoke(cli, ["list"])
    searched = runner.invoke(cli, ["search", "Kafka"])

    assert listed.exit_code == 0, listed.output
    assert listed.stdout.splitlines() == [
        line(vault, "fact", "active", PROXY, PROXY_TITLE, width=len("decision")),
        line(vault, "decision", "active", KAFKA, KAFKA_TITLE),
    ]
    assert listed.stderr == ""
    assert searched.stdout == f"[text] [{KAFKA}]({vault / KAFKA}) - {KAFKA_TITLE}\n"
    assert (vault / "index.sqlite").is_file()
    assert indexed_paths(vault) == [KAFKA, PROXY]
    assert subjects(git_cmd, vault) == committed_before
    assert status(git_cmd, vault) == []
