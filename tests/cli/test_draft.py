"""Tests for `notes draft`: creating a draft from a context packet through the fake generator, listing and showing
it, revising it with feedback, saving it as a note (validation, schedule normalization, filename, index, commit),
discarding it, and the guarantee that a draft never reaches search, list, recall, or Git."""

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import click
import pytest
from click.testing import CliRunner, Result

from notes import clock
from notes.cli import cli
from tests.conftest import FROZEN_NOW, FakeGenerator, deliver, indexed_paths

Git = Callable[..., str]

TITLE = "Project Atlas task updates over Kafka"
SHORT_NAME = "Project Atlas Kafka task updates"
NOTE = "notes/2026-09-02-project-atlas-kafka-task-updates.md"
DRAFT_ID = "20260902-120000-decision"
CREATED = "2026-09-02T12:00:00+03:00"
LATER = FROZEN_NOW + timedelta(hours=1)
LATER_STAMP = "2026-09-02T13:00:00+03:00"
PACKET = {
    "facts": ["Task updates go over Kafka", "WebSocket was dropped"],
    "cwd": "~/Dev/exampleco",
    "tags": ["ATLAS-27"],
}
FEEDBACK = "Shorter, please."
UNKNOWN = "no draft `nope` (see `notes draft list`)"


def decision_markdown(
    title: str = TITLE, *, kind_line: str = "kind: decision\n", body: str = "**Decision:** Kafka.\n"
) -> str:
    frontmatter = (
        f"---\n{kind_line}status: active\npaths: [~/Dev/exampleco]\ntags: [ATLAS-27]\nkeywords: [Kafka]\n---\n"
    )
    return f"{frontmatter}\n# {title}\n\n{body}"


def reminder_markdown(schedule: str, title: str = "Water the plants") -> str:
    return f"---\nkind: reminder\nstatus: active\nschedule: {schedule}\n---\n\n# {title}\n\n**Remind me:** Water.\n"


def draft_dir(vault: Path, draft_id: str = DRAFT_ID) -> Path:
    return vault / "drafts" / draft_id


def draft_line(vault: Path, draft_id: str, short_name: str) -> str:
    return f"[{draft_id}]({draft_dir(vault, draft_id) / 'draft.md'}) - {short_name}"


def draft_ids(vault: Path) -> list[str]:
    directory = vault / "drafts"
    return sorted(child.name for child in directory.iterdir()) if directory.is_dir() else []


def meta(vault: Path, draft_id: str = DRAFT_ID) -> dict[str, object]:
    return json.loads((draft_dir(vault, draft_id) / "draft.json").read_text(encoding="utf-8"))


def subjects(git_cmd: Git, repo: Path) -> list[str]:
    return git_cmd(repo, "log", "--format=%s").splitlines()


def status(git_cmd: Git, repo: Path) -> list[str]:
    return git_cmd(repo, "status", "--porcelain").splitlines()


def create(runner: CliRunner, kind: str = "decision", packet: object = PACKET, *args: str) -> Result:
    return runner.invoke(cli, ["draft", "create", kind, *args], input=json.dumps(packet))


def error_json(result: Result) -> dict[str, str]:
    assert result.stdout == ""
    return json.loads(result.stderr)["error"]


@pytest.fixture
def draft(runner: CliRunner, vault: Path, fake_generator: FakeGenerator, frozen_now: datetime) -> str:
    """A decision draft made from `PACKET`; the fake generator is left scripted with the same reply."""
    fake_generator.reply(SHORT_NAME, decision_markdown())
    result = create(runner)
    assert result.exit_code == 0, result.output
    return DRAFT_ID


@pytest.fixture
def later(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """Move the frozen clock one hour on, so a revision or a save gets a later timestamp than the creation."""
    monkeypatch.setattr(clock, "now", lambda: LATER)
    return LATER


# create


def test_create_stores_three_files_and_prints_the_id(
    runner: CliRunner, vault: Path, fake_generator: FakeGenerator, frozen_now: datetime
) -> None:
    fake_generator.reply(SHORT_NAME, decision_markdown())

    result = create(runner)

    assert result.exit_code == 0, result.output
    assert result.stdout == draft_line(vault, DRAFT_ID, SHORT_NAME) + "\n"
    assert result.stderr == ""
    directory = draft_dir(vault)
    assert sorted(child.name for child in directory.iterdir()) == ["context.json", "draft.json", "draft.md"]
    assert (directory / "draft.md").read_text(encoding="utf-8") == decision_markdown()
    assert json.loads((directory / "context.json").read_text(encoding="utf-8")) == PACKET
    assert meta(vault) == {
        "id": DRAFT_ID,
        "kind": "decision",
        "short_name": SHORT_NAME,
        "created_at": CREATED,
        "updated_at": CREATED,
        "revisions": [],
    }


def test_create_sends_the_composed_prompt_with_the_packet(
    runner: CliRunner, vault: Path, fake_generator: FakeGenerator, frozen_now: datetime
) -> None:
    fake_generator.reply(SHORT_NAME, decision_markdown())
    shared = (vault / "prompt.md").read_text(encoding="utf-8").strip()
    kind_prompt = (vault / "types" / "decision" / "prompt.md").read_text(encoding="utf-8").strip()

    create(runner)

    (prompt,) = fake_generator.prompts
    assert prompt.startswith(shared)
    assert kind_prompt in prompt
    assert "# Template\n\n```markdown\n---\nkind: decision\n" in prompt
    assert "# Existing tags\n\nThe vault has no tags yet." in prompt
    assert "# Context\n\n```json\n" + json.dumps(PACKET, indent=2) + "\n```" in prompt
    assert "# Previous draft" not in prompt
    assert "# Feedback" not in prompt


def test_create_json(runner: CliRunner, vault: Path, fake_generator: FakeGenerator, frozen_now: datetime) -> None:
    fake_generator.reply(SHORT_NAME, decision_markdown())

    result = create(runner, "decision", PACKET, "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "id": DRAFT_ID,
        "kind": "decision",
        "short_name": SHORT_NAME,
        "created_at": CREATED,
        "updated_at": CREATED,
        "revisions": [],
        "path": f"drafts/{DRAFT_ID}/draft.md",
        "abs_path": str(draft_dir(vault) / "draft.md"),
    }


def test_create_ids_get_a_suffix_at_the_same_moment(
    runner: CliRunner, vault: Path, fake_generator: FakeGenerator, frozen_now: datetime
) -> None:
    fake_generator.reply(SHORT_NAME, decision_markdown())

    first = create(runner)
    second = create(runner)

    assert first.exit_code == 0 and second.exit_code == 0
    assert second.stdout == draft_line(vault, f"{DRAFT_ID}-2", SHORT_NAME) + "\n"
    assert draft_ids(vault) == [DRAFT_ID, f"{DRAFT_ID}-2"]


def test_create_with_a_failing_generator_leaves_no_draft(
    runner: CliRunner, vault: Path, fake_generator: FakeGenerator, frozen_now: datetime
) -> None:
    fake_generator.fail(2, "model unavailable\n")

    result = create(runner)

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.endswith("exited with status 2: model unavailable\n")
    assert draft_ids(vault) == []


def test_create_with_a_failing_generator_json(
    runner: CliRunner, vault: Path, fake_generator: FakeGenerator, frozen_now: datetime
) -> None:
    fake_generator.fail(2, "model unavailable\n")

    result = create(runner, "decision", PACKET, "--json")

    assert result.exit_code == 1
    forwarded = "model unavailable\n"
    assert result.stderr.startswith(forwarded)
    error = json.loads(result.stderr[len(forwarded) :])["error"]
    assert error["type"] == "generator_error"
    assert error["message"].endswith("exited with status 2: model unavailable")
    assert draft_ids(vault) == []


def test_create_with_an_unusable_envelope_leaves_no_draft(
    runner: CliRunner, vault: Path, fake_generator: FakeGenerator, frozen_now: datetime
) -> None:
    fake_generator.print("I could not draft this note.")

    result = create(runner)

    assert result.exit_code == 1
    assert result.stderr == "Error: the generator output contains no JSON object with a `markdown` field\n"
    assert draft_ids(vault) == []


@pytest.mark.parametrize(
    ("stdin", "message"),
    [
        ("", "the context packet on stdin is empty: expected a JSON object"),
        ("  \n", "the context packet on stdin is empty: expected a JSON object"),
        ("{not json", "the context packet on stdin is not valid JSON: Expecting property name"),
        ("[1, 2]", "the context packet must be a JSON object, not a JSON array"),
        ('"facts"', "the context packet must be a JSON object, not a JSON string"),
        ("null", "the context packet must be a JSON object, not null"),
    ],
)
def test_create_rejects_a_packet_that_is_not_a_json_object(
    runner: CliRunner, vault: Path, fake_generator: FakeGenerator, frozen_now: datetime, stdin: str, message: str
) -> None:
    fake_generator.reply(SHORT_NAME, decision_markdown())

    result = runner.invoke(cli, ["draft", "create", "decision"], input=stdin)

    assert result.exit_code == 1
    assert result.stderr.startswith(f"Error: {message}")
    assert fake_generator.prompts == []
    assert draft_ids(vault) == []


def test_create_rejects_an_unknown_kind_before_reading_stdin(
    runner: CliRunner, vault: Path, fake_generator: FakeGenerator, frozen_now: datetime
) -> None:
    fake_generator.reply(SHORT_NAME, decision_markdown())

    result = create(runner, "recipe", PACKET, "--json")

    assert result.exit_code == 1
    assert error_json(result) == {
        "type": "usage_error",
        "message": "unknown kind `recipe` (known kinds: decision, fact, idea, reminder)",
    }
    assert fake_generator.prompts == []
    assert draft_ids(vault) == []


# list and show


def test_list_prints_nothing_without_drafts(runner: CliRunner, vault: Path) -> None:
    result = runner.invoke(cli, ["draft", "list"])
    assert result.exit_code == 0, result.output
    assert result.output == ""

    result = runner.invoke(cli, ["draft", "list", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == []


def test_list_on_a_terminal_shows_the_short_name_first_hyperlinked_to_the_draft(
    runner: CliRunner, vault: Path, draft: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")

    result = runner.invoke(cli, ["draft", "list"], color=True)

    assert result.exit_code == 0, result.output
    uri = (draft_dir(vault) / "draft.md").as_uri()
    assert result.stdout == (
        f"\x1b]8;;{uri}\x1b\\{click.style(SHORT_NAME, bold=True)}\x1b]8;;\x1b\\"
        f"  \x1b]8;;{uri}\x1b\\{click.style(DRAFT_ID, fg='bright_black')}\x1b]8;;\x1b\\\n"
    )


def test_list_shows_every_draft_in_id_order(
    runner: CliRunner, vault: Path, fake_generator: FakeGenerator, draft: str, later: datetime
) -> None:
    fake_generator.reply("porter stems latin", "---\nkind: fact\nstatus: active\n---\n\n# Porter\n")
    assert create(runner, "fact", {"facts": ["porter"]}).exit_code == 0

    result = runner.invoke(cli, ["draft", "list"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        draft_line(vault, DRAFT_ID, SHORT_NAME),
        draft_line(vault, "20260902-130000-fact", "porter stems latin"),
    ]
    listed = json.loads(runner.invoke(cli, ["draft", "list", "--json"]).stdout)
    assert [(item["id"], item["kind"], item["short_name"]) for item in listed] == [
        (DRAFT_ID, "decision", SHORT_NAME),
        ("20260902-130000-fact", "fact", "porter stems latin"),
    ]
    assert listed[1]["created_at"] == LATER_STAMP


def test_show_prints_the_markdown_verbatim(runner: CliRunner, vault: Path, draft: str) -> None:
    result = runner.invoke(cli, ["draft", "show", draft])

    assert result.exit_code == 0, result.output
    assert result.stdout == decision_markdown()


def test_show_json_carries_the_metadata_markdown_and_context(runner: CliRunner, vault: Path, draft: str) -> None:
    result = runner.invoke(cli, ["draft", "show", draft, "--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["id"] == DRAFT_ID
    assert data["short_name"] == SHORT_NAME
    assert data["markdown"] == decision_markdown()
    assert data["context"] == PACKET
    assert data["abs_path"] == str(draft_dir(vault) / "draft.md")


# revise


def test_revise_sends_the_previous_markdown_and_feedback_and_records_the_revision(
    runner: CliRunner, vault: Path, fake_generator: FakeGenerator, draft: str, later: datetime
) -> None:
    revised = decision_markdown("Kafka carries the task updates", body="**Decision:** Kafka, not WebSocket.\n")
    fake_generator.reply("Kafka carries task updates", revised)

    result = runner.invoke(cli, ["draft", "revise", draft, FEEDBACK])

    assert result.exit_code == 0, result.output
    assert result.stdout == draft_line(vault, DRAFT_ID, "Kafka carries task updates") + "\n"
    _, prompt = fake_generator.prompts
    assert "# Context\n\n```json\n" + json.dumps(PACKET, indent=2) + "\n```" in prompt
    assert "# Previous draft\n\n" in prompt
    assert "```markdown\n" + decision_markdown().rstrip() + "\n```" in prompt
    assert prompt.endswith(f"# Feedback\n\n{FEEDBACK}")
    assert (draft_dir(vault) / "draft.md").read_text(encoding="utf-8") == revised
    assert meta(vault) == {
        "id": DRAFT_ID,
        "kind": "decision",
        "short_name": "Kafka carries task updates",
        "created_at": CREATED,
        "updated_at": LATER_STAMP,
        "revisions": [FEEDBACK],
    }
    assert json.loads((draft_dir(vault) / "context.json").read_text(encoding="utf-8")) == PACKET


def test_revise_reads_the_feedback_from_stdin_and_accumulates_revisions(
    runner: CliRunner, vault: Path, fake_generator: FakeGenerator, draft: str, later: datetime
) -> None:
    fake_generator.reply(SHORT_NAME, decision_markdown(body="**Decision:** Kafka. Rationale: replay.\n"))
    assert runner.invoke(cli, ["draft", "revise", draft, "Add the rationale."]).exit_code == 0

    result = runner.invoke(cli, ["draft", "revise", draft, "--json"], input="Mention replay.\n")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["revisions"] == ["Add the rationale.", "Mention replay."]
    assert fake_generator.prompts[-1].endswith("# Feedback\n\nMention replay.")
    assert meta(vault)["revisions"] == ["Add the rationale.", "Mention replay."]


@pytest.mark.parametrize("args", [[], ["   "]])
def test_revise_without_feedback_fails_before_running_the_generator(
    runner: CliRunner, vault: Path, fake_generator: FakeGenerator, draft: str, args: list[str]
) -> None:
    result = runner.invoke(cli, ["draft", "revise", draft, *args], input="")

    assert result.exit_code == 1
    assert result.stderr == "Error: the feedback must not be empty\n"
    assert len(fake_generator.prompts) == 1
    assert meta(vault)["revisions"] == []


def test_revise_with_a_failing_generator_keeps_the_draft_unchanged(
    runner: CliRunner, vault: Path, fake_generator: FakeGenerator, draft: str, later: datetime
) -> None:
    fake_generator.fail(1, "boom\n")

    result = runner.invoke(cli, ["draft", "revise", draft, FEEDBACK])

    assert result.exit_code == 1
    assert "exited with status 1: boom" in result.stderr
    assert (draft_dir(vault) / "draft.md").read_text(encoding="utf-8") == decision_markdown()
    assert meta(vault)["revisions"] == []
    assert meta(vault)["updated_at"] == CREATED


# save


def test_save_writes_indexes_commits_the_note_and_removes_the_draft(
    runner: CliRunner, vault: Path, git_cmd: Git, draft: str
) -> None:
    result = runner.invoke(cli, ["draft", "save", draft])

    assert result.exit_code == 0, result.output
    assert result.stdout == f"[{NOTE}]({vault / NOTE}) - {TITLE}\n"
    assert result.stderr == ""
    assert (vault / NOTE).read_text(encoding="utf-8") == decision_markdown()
    assert subjects(git_cmd, vault)[0] == f"notes: update {NOTE}"
    assert status(git_cmd, vault) == []
    assert indexed_paths(vault) == [NOTE]
    assert draft_ids(vault) == []


def test_save_json(runner: CliRunner, vault: Path, draft: str) -> None:
    result = runner.invoke(cli, ["draft", "save", draft, "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "path": NOTE,
        "abs_path": str(vault / NOTE),
        "title": TITLE,
        "valid": True,
        "errors": [],
        "draft": DRAFT_ID,
    }


def test_save_names_the_file_after_the_save_date_and_the_short_name(
    runner: CliRunner, vault: Path, fake_generator: FakeGenerator, draft: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(clock, "now", lambda: FROZEN_NOW + timedelta(days=5))

    result = runner.invoke(cli, ["draft", "save", draft])

    assert result.exit_code == 0, result.output
    assert indexed_paths(vault) == ["notes/2026-09-07-project-atlas-kafka-task-updates.md"]


def test_name_overrides_the_short_name(runner: CliRunner, vault: Path, draft: str) -> None:
    result = runner.invoke(cli, ["draft", "save", draft, "--name", "Kafka decision"])

    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("[notes/2026-09-02-kafka-decision.md](")
    assert indexed_paths(vault) == ["notes/2026-09-02-kafka-decision.md"]


def test_save_appends_a_suffix_when_the_filename_is_taken(runner: CliRunner, vault: Path, draft: str) -> None:
    (vault / NOTE).write_text(decision_markdown("An earlier note"), encoding="utf-8")

    result = runner.invoke(cli, ["draft", "save", draft])

    assert result.exit_code == 0, result.output
    taken = NOTE.replace(".md", "-2.md")
    assert result.stdout == f"[{taken}]({vault / taken}) - {TITLE}\n"
    assert (vault / NOTE).read_text(encoding="utf-8") == decision_markdown("An earlier note")
    assert indexed_paths(vault) == sorted([NOTE, taken])


def test_save_at_a_freed_path_does_not_inherit_its_deliveries(runner: CliRunner, vault: Path, draft: str) -> None:
    """A note deleted by hand leaves its delivery rows behind; the note saved onto that path starts without them."""
    (vault / NOTE).write_text(decision_markdown("An earlier note"), encoding="utf-8")
    assert runner.invoke(cli, ["sync"]).exit_code == 0
    deliver(vault, NOTE, "2026-09-02T09:00:00+03:00")
    (vault / NOTE).unlink()
    assert runner.invoke(cli, ["sync"]).exit_code == 0

    result = runner.invoke(cli, ["draft", "save", draft])

    assert result.exit_code == 0, result.output
    assert result.stdout == f"[{NOTE}]({vault / NOTE}) - {TITLE}\n"
    assert runner.invoke(cli, ["list", "--unread"]).stdout == ""


def test_save_of_an_invalid_draft_reports_errors_and_keeps_it(
    runner: CliRunner, vault: Path, git_cmd: Git, fake_generator: FakeGenerator, frozen_now: datetime
) -> None:
    fake_generator.reply("broken draft", "---\nkind: decision\nstatus: pending\n---\n\nNo heading here.\n")
    assert create(runner).exit_code == 0
    before = subjects(git_cmd, vault)
    draft_md = draft_dir(vault) / "draft.md"

    result = runner.invoke(cli, ["draft", "save", DRAFT_ID])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.splitlines() == [
        f"{draft_md}:3: status must be active or archived",
        f"{draft_md}:5: missing H1 title: the body needs exactly one line starting with `# `",
        f"Error: draft {DRAFT_ID} is not a valid note and is kept: line 3: status must be active or archived; "
        f"line 5: missing H1 title: the body needs exactly one line starting with `# `; revise it or edit {draft_md}",
    ]
    assert sorted(path.name for path in (vault / "notes").iterdir()) == [".gitkeep"]
    assert draft_ids(vault) == [DRAFT_ID]
    assert draft_md.read_text(encoding="utf-8") == "---\nkind: decision\nstatus: pending\n---\n\nNo heading here.\n"
    assert subjects(git_cmd, vault) == before
    assert indexed_paths(vault) == []


def test_save_of_an_invalid_draft_json(
    runner: CliRunner, vault: Path, fake_generator: FakeGenerator, frozen_now: datetime
) -> None:
    fake_generator.reply("broken draft", "---\nkind: decision\nstatus: active\n---\n\nNo heading here.\n")
    assert create(runner).exit_code == 0

    result = runner.invoke(cli, ["draft", "save", DRAFT_ID, "--json"])

    assert result.exit_code == 1
    error = error_json(result)
    assert error["type"] == "validation_failed"
    assert error["message"].startswith(f"draft {DRAFT_ID} is not a valid note and is kept: line 5: missing H1 title")
    assert draft_ids(vault) == [DRAFT_ID]


def test_save_normalizes_a_relative_schedule(
    runner: CliRunner, vault: Path, fake_generator: FakeGenerator, frozen_now: datetime
) -> None:
    fake_generator.reply("water the plants", reminder_markdown("in 3 days"))
    assert create(runner, "reminder", {"facts": ["water the plants"], "schedule": "in 3 days"}).exit_code == 0

    result = runner.invoke(cli, ["draft", "save", "20260902-120000-reminder"])

    path = "notes/2026-09-02-water-the-plants.md"
    assert result.exit_code == 0, result.output
    assert (vault / path).read_text(encoding="utf-8") == reminder_markdown("at 2026-09-05T12:00:00+03:00")
    assert indexed_paths(vault) == [path]
    listed = json.loads(runner.invoke(cli, ["list", "--json"]).stdout)
    assert listed[0]["schedule"] == "at 2026-09-05T12:00:00+03:00"


@pytest.mark.parametrize("kind_line", ["", "kind:\n"])
def test_save_fills_a_missing_kind(
    runner: CliRunner, vault: Path, fake_generator: FakeGenerator, frozen_now: datetime, kind_line: str
) -> None:
    fake_generator.reply(SHORT_NAME, decision_markdown(kind_line=kind_line))
    assert create(runner).exit_code == 0

    result = runner.invoke(cli, ["draft", "save", DRAFT_ID])

    assert result.exit_code == 0, result.output
    assert (vault / NOTE).read_text(encoding="utf-8") == decision_markdown()
    listed = json.loads(runner.invoke(cli, ["list", "--json"]).stdout)
    assert [(item["path"], item["kind"]) for item in listed] == [(NOTE, "decision")]


def test_save_keeps_a_kind_the_draft_spells_itself(
    runner: CliRunner, vault: Path, fake_generator: FakeGenerator, frozen_now: datetime
) -> None:
    fake_generator.reply("porter stems latin", "---\nkind: fact\nstatus: active\n---\n\n# Porter\n")
    assert create(runner, "decision", {"facts": ["porter"]}).exit_code == 0

    result = runner.invoke(cli, ["draft", "save", DRAFT_ID])

    assert result.exit_code == 0, result.output
    listed = json.loads(runner.invoke(cli, ["list", "--json"]).stdout)
    assert [item["kind"] for item in listed] == ["fact"]


def test_save_with_a_short_name_without_letters_fails_and_keeps_the_draft(
    runner: CliRunner, vault: Path, draft: str
) -> None:
    result = runner.invoke(cli, ["draft", "save", draft, "--name", "!!!"])

    assert result.exit_code == 1
    assert result.stderr == "Error: cannot build a filename: `!!!` has no letters or digits to build a filename from\n"
    assert draft_ids(vault) == [DRAFT_ID]
    assert indexed_paths(vault) == []


# discard


def test_discard_removes_the_directory(runner: CliRunner, vault: Path, git_cmd: Git, draft: str) -> None:
    before = subjects(git_cmd, vault)

    result = runner.invoke(cli, ["draft", "discard", draft])

    assert result.exit_code == 0, result.output
    assert result.stdout == f"discarded draft {DRAFT_ID}\n"
    assert draft_ids(vault) == []
    assert subjects(git_cmd, vault) == before
    assert indexed_paths(vault) == []


def test_discard_json(runner: CliRunner, vault: Path, draft: str) -> None:
    result = runner.invoke(cli, ["draft", "discard", draft, "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "id": DRAFT_ID,
        "kind": "decision",
        "short_name": SHORT_NAME,
        "discarded": True,
    }


# isolation and errors


def test_drafts_never_appear_in_list_search_recall_or_git(
    runner: CliRunner, vault: Path, git_cmd: Git, draft: str
) -> None:
    assert status(git_cmd, vault) == []
    assert indexed_paths(vault) == []
    for command in (["list"], ["search", "ATLAS-27"], ["search", "Kafka", "--all"], ["recall", "ATLAS-27"]):
        result = runner.invoke(cli, command)
        assert result.exit_code == 0, result.output
        assert result.output == "", command
    assert (vault / "drafts" / DRAFT_ID).is_dir()

    assert runner.invoke(cli, ["draft", "save", draft]).exit_code == 0

    found = json.loads(runner.invoke(cli, ["search", "ATLAS-27", "--json"]).stdout)
    assert [item["path"] for item in found] == [NOTE]


@pytest.mark.parametrize(
    "args",
    [["show", "nope"], ["revise", "nope", FEEDBACK], ["save", "nope"], ["discard", "nope"], ["show", "../x"]],
)
def test_an_unknown_draft_id_fails(
    runner: CliRunner, vault: Path, fake_generator: FakeGenerator, draft: str, args: list[str]
) -> None:
    result = runner.invoke(cli, ["draft", *args, "--json"])

    assert result.exit_code == 1
    error = error_json(result)
    assert error["type"] == "usage_error"
    assert error["message"].startswith("no draft `")
    assert len(fake_generator.prompts) == 1
    assert draft_ids(vault) == [DRAFT_ID]


def test_draft_commands_need_a_vault(runner: CliRunner, home: Path) -> None:
    result = runner.invoke(cli, ["draft", "list", "--json"])

    assert result.exit_code == 1
    assert error_json(result) == {"type": "no_vault", "message": "no vault at ~/.notes, run `notes init`"}


def test_draft_help_lists_the_subcommands(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["draft", "--help"])

    assert result.exit_code == 0, result.output
    for name in ("create", "discard", "list", "revise", "save", "show"):
        assert f"\n  {name} " in result.output
