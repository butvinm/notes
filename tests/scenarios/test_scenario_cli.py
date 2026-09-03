"""Scenario 1 from the plan, end to end through the CLI: create and search.

`notes init --auto-push` makes the vault; `notes new decision <title>` with the fake editor suggests a short name,
creates the dated file, validates, indexes, and commits it; `notes search` answers a natural-language question with
the title and the clickable absolute path, ranked by tag, keyword, and text matches. The module also holds the checks
that concern the CLI as a whole rather than one scenario: every command of the plan's surface exists, is listed in
`notes --help`, and answers in JSON under `--json`; and a Cyrillic title makes a Cyrillic slug that search finds
through another inflection of its words.
"""

import json
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from notes.cli import cli
from notes.cli import new as cli_new
from tests.conftest import FakeEditor, RecordedCommands, indexed_paths

Git = Callable[..., str]

QUESTION = "why doesn't Project Atlas communicate with the backend over WebSocket"
KAFKA_TITLE = "Project Atlas task updates over Kafka"
KAFKA = "notes/2026-09-02-project-atlas-task-updates-over-kafka.md"
PROXY_TITLE = "Legacy proxy drops idle connections"
PROXY = "notes/2026-09-02-legacy-proxy.md"
RUSSIAN_TITLE = "Обновления задач через Kafka"
RUSSIAN = "notes/2026-09-02-обновления-задач-через-kafka.md"

KAFKA_TEXT = """\
---
kind: decision
status: active
paths: [~/Dev/exampleco/project-atlas]
tags: [ATLAS-27, project-atlas, sync-worker]
keywords: [Kafka, tasks, runs, launches, jobs, Atlas, Project Atlas, proxy]
references:
  - CLAUDE.md
  - https://tracker.example/browse/ATLAS-27
---

# Project Atlas task updates over Kafka

**Decision:** Task and run updates flow from the backend to Project Atlas over Kafka instead of WebSocket.

**Rationale:** The proxy terminates WebSocket connections and drops them under load; Kafka gives durable delivery.
"""

PROXY_TEXT = """\
---
kind: fact
status: active
paths: []
tags: [legacy, proxy]
keywords: []
references: []
---

# Legacy proxy drops idle connections

**Fact:** The legacy proxy closes idle WebSocket connections after sixty seconds.

**Source:** The ops runbook.

**Context:** Seen while debugging slow reconnects.
"""

RUSSIAN_TEXT = """\
---
kind: fact
status: active
paths: [~/Dev/exampleco/project-atlas]
tags: [ATLAS-31, kafka]
keywords: [Кафка, очередь, задачи]
references: []
---

# Обновления задач через Kafka

**Fact:** Бэкенд публикует обновления задач в топик Kafka, а не отдает их по HTTP.

**Source:** Обсуждение в треде ATLAS-31.

**Context:** Прокси обрывает долгие соединения под нагрузкой.
"""

SURFACE: tuple[tuple[str, ...], ...] = (
    ("init",),
    ("new",),
    ("draft", "create"),
    ("draft", "list"),
    ("draft", "show"),
    ("draft", "revise"),
    ("draft", "save"),
    ("draft", "discard"),
    ("edit",),
    ("show",),
    ("search",),
    ("recall",),
    ("list",),
    ("read",),
    ("relate",),
    ("move",),
    ("sync",),
    ("check",),
    ("tick",),
    ("push",),
    ("pull",),
    ("prompt",),
    ("notifications", "enable"),
    ("notifications", "disable"),
    ("notifications", "status"),
)
"""The CLI surface of the plan, groups spelled out to their subcommands."""

ARGUMENTS: dict[tuple[str, ...], list[str]] = {
    ("new",): ["decision", "A title"],
    ("draft", "create"): ["decision"],
    ("draft", "show"): ["20260902-120000-decision"],
    ("draft", "revise"): ["20260902-120000-decision", "feedback"],
    ("draft", "save"): ["20260902-120000-decision"],
    ("draft", "discard"): ["20260902-120000-decision"],
    ("edit",): ["notes/2026-09-02-x.md"],
    ("show",): ["notes/2026-09-02-x.md"],
    ("search",): ["query"],
    ("read",): ["notes/2026-09-02-x.md"],
    ("relate",): ["notes/2026-09-02-x.md", "related", "notes/2026-09-02-y.md"],
    ("move",): ["notes/2026-09-02-x.md", "notes/2026-09-02-y.md"],
    ("prompt",): ["decision"],
}
"""Placeholder arguments for the commands that require some, enough to get past click's own parsing."""

WITHOUT_VAULT = {("init",), ("notifications", "disable"), ("notifications", "status")}
"""The commands that work without a vault; every other one reports the missing vault."""


def subjects(git_cmd: Git, repo: Path) -> list[str]:
    return git_cmd(repo, "log", "--format=%s").splitlines()


def status(git_cmd: Git, repo: Path) -> list[str]:
    return git_cmd(repo, "status", "--porcelain").splitlines()


def script_uninstalled_timer(recorded: RecordedCommands) -> None:
    """Answer the `systemctl` queries of `notifications status` the way a user manager without the timer would."""
    systemctl = ("systemctl", "--user")
    recorded.script(*systemctl, "is-enabled", returncode=1, stdout="not-found\n")
    recorded.script(*systemctl, "is-active", returncode=3, stdout="inactive\n")
    recorded.script(*systemctl, "list-timers", stdout="[]")


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch, fake_editor: FakeEditor) -> FakeEditor:
    """`notes new` believes it runs in a terminal, so it offers the short name and opens the editor; the fake editor
    is installed first, so the patched terminal can never reach the editor of the machine running the tests."""
    monkeypatch.setattr(cli_new, "interactive", lambda: True)
    return fake_editor


# Scenario 1: create and search from the CLI


def test_create_and_search_from_the_cli(
    runner: CliRunner, home: Path, git_cmd: Git, terminal: FakeEditor, frozen_now: datetime
) -> None:
    vault = home / ".notes"

    # A vault with auto-push on and no remote yet: there is nothing to push to, so nothing is attempted or warned.
    init = runner.invoke(cli, ["init", "--auto-push"])
    assert init.exit_code == 0, init.output
    assert init.stdout == f"initialized vault at {vault}\n"
    assert init.stderr == ""
    assert "auto_push = true" in (vault / "config.toml").read_text(encoding="utf-8")

    # The decision: the suggested short name is accepted with Enter, the fake editor fills the note in.
    terminal.write(KAFKA_TEXT)
    new = runner.invoke(cli, ["new", "decision", KAFKA_TITLE], input="\n")
    assert new.exit_code == 0, new.output
    assert new.stdout == (
        f"Short name for the filename [{KAFKA_TITLE}]: \n[{KAFKA}]({vault / KAFKA}) - {KAFKA_TITLE}\n"
    )
    assert new.stderr == ""
    assert terminal.paths == [str(vault / KAFKA)]
    assert (vault / KAFKA).read_text(encoding="utf-8") == KAFKA_TEXT
    assert subjects(git_cmd, vault) == [f"notes: update {KAFKA}", "notes: initialize vault"]
    assert status(git_cmd, vault) == []
    assert indexed_paths(vault) == [KAFKA]

    # A fact that mentions WebSocket only in its body, so the ranking has something to put second.
    terminal.write(PROXY_TEXT)
    fact = runner.invoke(cli, ["new", "fact", PROXY_TITLE, "--name", "legacy proxy"])
    assert fact.exit_code == 0, fact.output
    assert fact.stdout == f"[{PROXY}]({vault / PROXY}) - {PROXY_TITLE}\n"
    assert subjects(git_cmd, vault)[0] == f"notes: update {PROXY}"

    # The question from the design: the decision first, on its title, keywords, and body; the fact after it.
    by_question = runner.invoke(cli, ["search", QUESTION])
    assert by_question.exit_code == 0, by_question.output
    assert by_question.stdout.splitlines() == [
        f"[text] [{KAFKA}]({vault / KAFKA}) - {KAFKA_TITLE}",
        f"[text] [{PROXY}]({vault / PROXY}) - {PROXY_TITLE}",
    ]
    assert by_question.stderr == ""

    # The issue ID and a tag are exact matches with their own reasons; a keyword matches without being in the body.
    by_issue = runner.invoke(cli, ["search", "ATLAS-27 sync-worker"])
    assert by_issue.stdout == (
        f"[issue: ATLAS-27, tag: sync-worker, text] [{KAFKA}]({vault / KAFKA}) - {KAFKA_TITLE}\n"
    )
    by_keyword = runner.invoke(cli, ["search", "Atlas"])
    assert by_keyword.stdout == f"[text] [{KAFKA}]({vault / KAFKA}) - {KAFKA_TITLE}\n"

    # The same answer as machine output.
    as_json = runner.invoke(cli, ["search", "ATLAS-27", "--json"])
    assert as_json.exit_code == 0, as_json.output
    (entry,) = json.loads(as_json.stdout)
    assert entry["path"] == KAFKA
    assert entry["abs_path"] == str(vault / KAFKA)
    assert entry["title"] == KAFKA_TITLE
    assert entry["effective_status"] == "active"
    assert entry["reasons"] == ["issue: ATLAS-27", "text"]
    assert entry["score"] >= 10.0

    # Everything is committed and nothing was pushed, since no remote was ever configured.
    assert status(git_cmd, vault) == []
    assert git_cmd(vault, "remote") == ""


def test_a_cyrillic_title_makes_a_cyrillic_slug_that_search_finds_through_another_inflection(
    runner: CliRunner, home: Path, git_cmd: Git, terminal: FakeEditor, frozen_now: datetime
) -> None:
    vault = home / ".notes"
    assert runner.invoke(cli, ["init"]).exit_code == 0
    terminal.write(RUSSIAN_TEXT)

    new = runner.invoke(cli, ["new", "fact", RUSSIAN_TITLE], input="\n")

    assert new.exit_code == 0, new.output
    assert new.stdout == (
        f"Short name for the filename [{RUSSIAN_TITLE}]: \n[{RUSSIAN}]({vault / RUSSIAN}) - {RUSSIAN_TITLE}\n"
    )
    assert (vault / RUSSIAN).read_text(encoding="utf-8") == RUSSIAN_TEXT
    assert subjects(git_cmd, vault)[0] == f"notes: update {RUSSIAN}"
    assert status(git_cmd, vault) == []

    by_inflection = runner.invoke(cli, ["search", "задачами"])
    by_tag = runner.invoke(cli, ["search", "kafka"])
    by_prompt = runner.invoke(cli, ["recall", "--cwd", str(home), "--", "что мы решили в ATLAS-31?"])
    shown = runner.invoke(cli, ["show", RUSSIAN])
    listed = runner.invoke(cli, ["list", "--tag", "kafka"])

    assert by_inflection.stdout == f"[text] [{RUSSIAN}]({vault / RUSSIAN}) - {RUSSIAN_TITLE}\n"
    assert by_tag.stdout == f"[tag: kafka, text] [{RUSSIAN}]({vault / RUSSIAN}) - {RUSSIAN_TITLE}\n"
    assert by_prompt.stdout.splitlines() == [
        "Related notes:",
        f"- [issue: ATLAS-31, text] [{RUSSIAN}]({vault / RUSSIAN}) - {RUSSIAN_TITLE}",
    ]
    assert shown.stdout == RUSSIAN_TEXT
    assert listed.stdout == f"fact active [{RUSSIAN}]({vault / RUSSIAN}) - {RUSSIAN_TITLE}\n"


# The CLI surface


def test_every_command_of_the_surface_exists_and_is_listed_in_help(runner: CliRunner, home: Path) -> None:
    root = runner.invoke(cli, ["--help"])

    assert root.exit_code == 0
    assert set(cli.commands) == {command[0] for command in SURFACE}
    for name in cli.commands:
        assert re.search(rf"^  {re.escape(name)}\b", root.output, re.M), f"{name} is not listed in notes --help"
    for group in ("draft", "notifications"):
        command = cli.commands[group]
        assert isinstance(command, click.Group)
        assert set(command.commands) == {names[1] for names in SURFACE if names[0] == group}
        listing = runner.invoke(cli, [group, "--help"])
        assert listing.exit_code == 0
        for name in command.commands:
            assert re.search(rf"^  {re.escape(name)}\b", listing.output, re.M), f"{name} missing from {group} --help"


@pytest.mark.parametrize("command", SURFACE, ids=" ".join)
def test_every_command_accepts_json_and_answers_in_json(
    runner: CliRunner, home: Path, recorded_commands: RecordedCommands, command: tuple[str, ...]
) -> None:
    recorded_commands.passthrough("git")
    script_uninstalled_timer(recorded_commands)

    help_result = runner.invoke(cli, [*command, "--help"])
    result = runner.invoke(cli, [*command, *ARGUMENTS.get(command, []), "--json"])

    assert help_result.exit_code == 0, help_result.output
    assert "--json" in help_result.output
    if command in WITHOUT_VAULT:
        assert result.exit_code == 0, result.output
        assert isinstance(json.loads(result.stdout), dict)
    else:
        assert result.exit_code == 1, result.output
        assert result.stdout == ""
        error = json.loads(result.stderr)["error"]
        assert error["type"] == "no_vault"
        assert "notes init" in error["message"]
