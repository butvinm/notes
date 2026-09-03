"""Scenario 2 from the plan, end to end through the CLI: a decision through the draft flow.

A context packet on stdin to `notes draft create decision` with the fake generator produces a draft, `notes draft
revise` applies feedback, and `notes draft save` writes the note, indexes it, commits it, and removes the draft; a
failing generator leaves no draft and no note. Until it is saved, a draft never reaches search, list, or recall.
"""

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from notes import clock
from notes.cli import cli
from tests.conftest import FROZEN_NOW, FakeGenerator, indexed_paths

Git = Callable[..., str]

DRAFT_ID = "20260902-120000-decision"
TITLE = "Project Atlas task updates over Kafka"
SHORT_NAME = "Project Atlas Kafka task updates"
REVISED_SHORT_NAME = "Project Atlas task updates over Kafka"
NOTE = "notes/2026-09-02-project-atlas-task-updates-over-kafka.md"
FEEDBACK = "Say why WebSocket was dropped: the proxy terminates idle connections."
PACKET = {
    "facts": [
        "Task updates flow from the backend to Project Atlas over Kafka",
        "WebSocket was dropped because the proxy terminates idle connections",
    ],
    "cwd": "~/Dev/exampleco/project-atlas",
    "tags": ["ATLAS-27", "project-atlas"],
    "references": ["CLAUDE.md"],
}


def markdown(rationale: str) -> str:
    """The Markdown the fake generator returns: a valid decision whose rationale is the only part that changes."""
    return (
        "---\nkind: decision\nstatus: active\npaths: [~/Dev/exampleco/project-atlas]\n"
        "tags: [ATLAS-27, project-atlas]\nkeywords: [Kafka, Project Atlas]\nreferences: [CLAUDE.md]\n---\n\n"
        f"# {TITLE}\n\n**Decision:** Task updates flow from the backend to Project Atlas over Kafka.\n\n"
        f"**Rationale:** {rationale}\n"
    )


FIRST = markdown("Kafka gives durable delivery.")
REVISED = markdown("Kafka gives durable delivery; the proxy terminates idle WebSocket connections.")


def subjects(git_cmd: Git, repo: Path) -> list[str]:
    return git_cmd(repo, "log", "--format=%s").splitlines()


def status(git_cmd: Git, repo: Path) -> list[str]:
    return git_cmd(repo, "status", "--porcelain").splitlines()


def draft_ids(vault: Path) -> list[str]:
    directory = vault / "drafts"
    return sorted(child.name for child in directory.iterdir()) if directory.is_dir() else []


def note_files(vault: Path) -> list[str]:
    return sorted(path.name for path in (vault / "notes").glob("*.md"))


def test_a_decision_through_the_draft_flow(
    runner: CliRunner,
    vault: Path,
    git_cmd: Git,
    fake_generator: FakeGenerator,
    frozen_now: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft_dir = vault / "drafts" / DRAFT_ID
    draft_md = draft_dir / "draft.md"
    committed_before = subjects(git_cmd, vault)

    # Create: the packet goes in on stdin, the generator gets the composed prompt, the draft is stored.
    fake_generator.reply(SHORT_NAME, FIRST)
    created = runner.invoke(cli, ["draft", "create", "decision"], input=json.dumps(PACKET))
    assert created.exit_code == 0, created.output
    assert created.stdout == f"[{DRAFT_ID}]({draft_md}) - {SHORT_NAME}\n"
    assert created.stderr == ""
    assert sorted(child.name for child in draft_dir.iterdir()) == ["context.json", "draft.json", "draft.md"]
    assert draft_md.read_text(encoding="utf-8") == FIRST
    assert json.loads((draft_dir / "context.json").read_text(encoding="utf-8")) == PACKET
    (prompt,) = fake_generator.prompts
    assert (vault / "prompt.md").read_text(encoding="utf-8").strip() in prompt
    assert (vault / "types" / "decision" / "prompt.md").read_text(encoding="utf-8").strip() in prompt
    assert json.dumps(PACKET, indent=2) in prompt

    # An unconfirmed draft is visible to the draft commands only.
    assert runner.invoke(cli, ["draft", "list"]).stdout == f"[{DRAFT_ID}]({draft_md}) - {SHORT_NAME}\n"
    assert runner.invoke(cli, ["draft", "show", DRAFT_ID]).stdout == FIRST
    assert runner.invoke(cli, ["list"]).stdout == ""
    assert runner.invoke(cli, ["search", "ATLAS-27"]).stdout == ""
    assert runner.invoke(cli, ["recall", "ATLAS-27"]).stdout == ""
    assert note_files(vault) == []
    assert subjects(git_cmd, vault) == committed_before

    # Revise: the generator sees the packet again, the previous Markdown, and the feedback; the draft is replaced.
    monkeypatch.setattr(clock, "now", lambda: FROZEN_NOW + timedelta(hours=1))
    fake_generator.reply(REVISED_SHORT_NAME, REVISED)
    revised = runner.invoke(cli, ["draft", "revise", DRAFT_ID, FEEDBACK])
    assert revised.exit_code == 0, revised.output
    assert revised.stdout == f"[{DRAFT_ID}]({draft_md}) - {REVISED_SHORT_NAME}\n"
    _, revision_prompt = fake_generator.prompts
    assert json.dumps(PACKET, indent=2) in revision_prompt
    assert "# Previous draft\n\n" in revision_prompt
    assert "```markdown\n" + FIRST.rstrip() + "\n```" in revision_prompt
    assert revision_prompt.endswith(f"# Feedback\n\n{FEEDBACK}")
    assert draft_md.read_text(encoding="utf-8") == REVISED
    meta = json.loads((draft_dir / "draft.json").read_text(encoding="utf-8"))
    assert meta["short_name"] == REVISED_SHORT_NAME
    assert meta["revisions"] == [FEEDBACK]
    assert meta["updated_at"] == "2026-09-02T13:00:00+03:00"

    # Save: the note is named after the save date and the short name, indexed, committed; the draft is gone.
    saved = runner.invoke(cli, ["draft", "save", DRAFT_ID])
    assert saved.exit_code == 0, saved.output
    assert saved.stdout == f"[{NOTE}]({vault / NOTE}) - {TITLE}\n"
    assert saved.stderr == ""
    assert (vault / NOTE).read_text(encoding="utf-8") == REVISED
    assert subjects(git_cmd, vault) == [f"notes: update {NOTE}", *committed_before]
    assert status(git_cmd, vault) == []
    assert indexed_paths(vault) == [NOTE]
    assert not draft_dir.exists()
    assert runner.invoke(cli, ["draft", "list"]).stdout == ""

    # The saved note is a regular note from here on.
    assert runner.invoke(cli, ["list"]).stdout == f"decision active [{NOTE}]({vault / NOTE}) - {TITLE}\n"
    assert runner.invoke(cli, ["search", "ATLAS-27"]).stdout == (
        f"[issue: ATLAS-27, text] [{NOTE}]({vault / NOTE}) - {TITLE}\n"
    )
    assert runner.invoke(cli, ["show", NOTE]).stdout == REVISED


def test_a_failing_generator_leaves_no_draft_and_no_note(
    runner: CliRunner, vault: Path, git_cmd: Git, fake_generator: FakeGenerator, frozen_now: datetime
) -> None:
    committed_before = subjects(git_cmd, vault)
    fake_generator.fail(2, "model unavailable\n")

    result = runner.invoke(cli, ["draft", "create", "decision"], input=json.dumps(PACKET))

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.endswith("exited with status 2: model unavailable\n")
    assert draft_ids(vault) == []
    assert note_files(vault) == []
    assert indexed_paths(vault) == []
    assert subjects(git_cmd, vault) == committed_before
    assert status(git_cmd, vault) == []
    assert runner.invoke(cli, ["draft", "list"]).stdout == ""
    assert runner.invoke(cli, ["list"]).stdout == ""
