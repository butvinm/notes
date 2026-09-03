"""Tests for `notes new` and `notes edit`: scaffolding, prompts, the editor, schedule normalization, validation, Git.

Prompts and the editor need a terminal, which `CliRunner` is not, so the `terminal` fixture patches the check in both
command modules; the fake editor from `tests.conftest` then runs for real through `run_command`.
"""

import json
import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from notes import document
from notes.cli import cli
from notes.cli import edit as cli_edit
from notes.cli import new as cli_new
from tests.conftest import FakeEditor, deliver, indexed_paths

Git = Callable[..., str]
TITLE = "Project Atlas task updates over Kafka"
DECISION = "notes/2026-09-02-project-atlas-task-updates-over-kafka.md"
NO_TITLE_ERROR = "a title is required: notes new <kind> <title> (prompts are shown only in a terminal without --json)"


def decision_text(title: str = TITLE, body: str = "**Decision:** Kafka.\n") -> str:
    return f"---\nkind: decision\nstatus: active\ntags: [ATLAS-27]\n---\n\n# {title}\n\n{body}"


def reminder_text(schedule: str, title: str = "Water the plants") -> str:
    return f"---\nkind: reminder\nstatus: active\nschedule: {schedule}\n---\n\n# {title}\n"


def subjects(git_cmd: Git, repo: Path) -> list[str]:
    return git_cmd(repo, "log", "--format=%s").splitlines()


def status(git_cmd: Git, repo: Path) -> list[str]:
    return git_cmd(repo, "status", "--porcelain").splitlines()


def note_files(vault: Path) -> list[str]:
    return sorted(path.name for path in (vault / "notes").glob("*.md"))


def index_value(vault: Path, sql: str, path: str) -> str | None:
    conn = sqlite3.connect(vault / "index.sqlite")
    try:
        row = conn.execute(sql, (path,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def invalid_paths(vault: Path) -> list[str]:
    conn = sqlite3.connect(vault / "index.sqlite")
    try:
        return [row[0] for row in conn.execute("SELECT path FROM invalid_files ORDER BY path")]
    finally:
        conn.close()


def delivery_paths(vault: Path) -> list[str]:
    conn = sqlite3.connect(vault / "index.sqlite")
    try:
        return [row[0] for row in conn.execute("SELECT path FROM deliveries ORDER BY path, occurrence_at")]
    finally:
        conn.close()


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch, fake_editor: FakeEditor) -> None:
    """Make both commands believe they run in a terminal, so they prompt and open the editor.

    The fake editor is installed first, so a patched terminal can never reach the editor of the machine running the
    tests; a test about a missing editor removes the variables itself.
    """
    monkeypatch.setattr(cli_new, "interactive", lambda: True)
    monkeypatch.setattr(cli_edit, "interactive", lambda: True)


@pytest.fixture
def existing(runner: CliRunner, vault: Path) -> str:
    """The Kafka decision, written by hand and committed by `notes sync`: no prompt and no editor involved."""
    (vault / DECISION).write_text(decision_text(), encoding="utf-8")
    result = runner.invoke(cli, ["sync"])
    assert result.exit_code == 0, result.output
    return DECISION


# notes new: the terminal flow


def test_new_creates_a_dated_note_opens_the_editor_and_commits(
    runner: CliRunner, vault: Path, git_cmd: Git, fake_editor: FakeEditor, frozen_now: datetime, terminal: None
) -> None:
    fake_editor.write(decision_text())

    result = runner.invoke(cli, ["new", "decision", TITLE], input="\n")

    assert result.exit_code == 0, result.output
    assert result.stdout == (f"Short name for the filename [{TITLE}]: \n[{DECISION}]({vault / DECISION}) - {TITLE}\n")
    assert result.stderr == ""
    assert fake_editor.paths == [str(vault / DECISION)]
    assert (vault / DECISION).read_text(encoding="utf-8") == decision_text()
    assert subjects(git_cmd, vault) == [f"notes: update {DECISION}", "notes: initialize vault"]
    assert status(git_cmd, vault) == []
    assert indexed_paths(vault) == [DECISION]
    assert index_value(vault, "SELECT tags FROM notes WHERE path = ?", DECISION) == '["ATLAS-27"]'


def test_new_renders_the_kind_template_when_the_editor_changes_nothing(
    runner: CliRunner, vault: Path, git_cmd: Git, fake_editor: FakeEditor, frozen_now: datetime, terminal: None
) -> None:
    result = runner.invoke(cli, ["new", "decision", TITLE, "--name", "kafka"])

    path = "notes/2026-09-02-kafka.md"
    template = (vault / "types" / "decision" / "template.md").read_text(encoding="utf-8")
    assert result.exit_code == 0, result.output
    assert (vault / path).read_text(encoding="utf-8") == document.render_template(template, TITLE)
    assert f"# {TITLE}\n" in (vault / path).read_text(encoding="utf-8")
    assert subjects(git_cmd, vault)[0] == f"notes: update {path}"


def test_name_overrides_the_suggestion_and_is_not_asked(
    runner: CliRunner, vault: Path, fake_editor: FakeEditor, frozen_now: datetime, terminal: None
) -> None:
    result = runner.invoke(cli, ["new", "decision", TITLE, "--name", "Kafka updates"])

    assert result.exit_code == 0, result.output
    assert "Short name" not in result.output
    assert note_files(vault) == ["2026-09-02-kafka-updates.md"]


def test_short_name_answer_replaces_the_suggestion(
    runner: CliRunner, vault: Path, fake_editor: FakeEditor, frozen_now: datetime, terminal: None
) -> None:
    result = runner.invoke(cli, ["new", "decision", TITLE], input="Project Atlas Kafka\n")

    assert result.exit_code == 0, result.output
    assert note_files(vault) == ["2026-09-02-project-atlas-kafka.md"]


def test_title_is_asked_in_a_terminal_and_whitespace_is_collapsed(
    runner: CliRunner, vault: Path, fake_editor: FakeEditor, frozen_now: datetime, terminal: None
) -> None:
    result = runner.invoke(cli, ["new", "decision"], input="  Kafka   over \t WebSocket \n\n")

    path = "notes/2026-09-02-kafka-over-websocket.md"
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("Title: ")
    assert "# Kafka over WebSocket\n" in (vault / path).read_text(encoding="utf-8")


def test_schedule_is_asked_for_a_reminder_and_relative_input_is_normalized(
    runner: CliRunner, vault: Path, fake_editor: FakeEditor, frozen_now: datetime, terminal: None
) -> None:
    result = runner.invoke(cli, ["new", "reminder", "Water the plants", "--name", "plants"], input="in 3 days\n")

    path = "notes/2026-09-02-plants.md"
    assert result.exit_code == 0, result.output
    assert "Schedule (`in 3 days`, `tomorrow 09:00`, `2026-10-01 18:00`, `every 2 weeks`): " in result.stdout
    assert "schedule: at 2026-09-05T12:00:00+03:00\n" in (vault / path).read_text(encoding="utf-8")
    assert indexed_paths(vault) == [path]
    assert index_value(vault, "SELECT schedule FROM notes WHERE path = ?", path) == "at 2026-09-05T12:00:00+03:00"


def test_schedule_prompt_repeats_until_the_answer_is_acceptable(
    runner: CliRunner, vault: Path, fake_editor: FakeEditor, frozen_now: datetime, terminal: None
) -> None:
    result = runner.invoke(cli, ["new", "reminder", "Water", "--name", "plants"], input="whenever\ntomorrow 09:30\n")

    assert result.exit_code == 0, result.output
    assert "Error: `whenever` is not a schedule: write `at <timestamp>`" in result.stdout
    assert result.stdout.count("Schedule (") == 2
    text = (vault / "notes/2026-09-02-plants.md").read_text(encoding="utf-8")
    assert "schedule: at 2026-09-03T09:30:00+03:00\n" in text


def test_promise_prompt_rejects_a_recurring_schedule(
    runner: CliRunner, vault: Path, fake_editor: FakeEditor, frozen_now: datetime, terminal: None
) -> None:
    result = runner.invoke(
        cli, ["new", "promise", "Send the report", "--name", "report"], input="every 2 weeks\n2026-10-01 18:00\n"
    )

    assert result.exit_code == 0, result.output
    assert "Error: a promise needs a one-shot `at <timestamp>` schedule, its due date" in result.stdout
    text = (vault / "notes/2026-09-02-report.md").read_text(encoding="utf-8")
    assert "schedule: at 2026-10-01T18:00:00+03:00\n" in text
    assert "**Due:** at 2026-10-01T18:00:00+03:00\n" in text
    assert indexed_paths(vault) == ["notes/2026-09-02-report.md"]


def test_relative_schedule_written_by_the_editor_is_normalized_in_the_file(
    runner: CliRunner, vault: Path, git_cmd: Git, fake_editor: FakeEditor, frozen_now: datetime, terminal: None
) -> None:
    fake_editor.write(reminder_text("in a week"))

    result = runner.invoke(cli, ["new", "reminder", "Water the plants", "--name", "plants"], input="tomorrow\n")

    path = "notes/2026-09-02-plants.md"
    assert result.exit_code == 0, result.output
    assert (vault / path).read_text(encoding="utf-8") == reminder_text("at 2026-09-09T12:00:00+03:00")
    assert subjects(git_cmd, vault)[0] == f"notes: update {path}"
    assert status(git_cmd, vault) == []
    assert index_value(vault, "SELECT schedule FROM notes WHERE path = ?", path) == "at 2026-09-09T12:00:00+03:00"


def test_json_never_prompts_but_still_opens_the_editor(
    runner: CliRunner, vault: Path, fake_editor: FakeEditor, frozen_now: datetime, terminal: None
) -> None:
    fake_editor.write(decision_text())

    result = runner.invoke(cli, ["new", "decision", TITLE, "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "path": DECISION,
        "abs_path": str(vault / DECISION),
        "title": TITLE,
        "valid": True,
        "errors": [],
    }
    assert result.stderr == ""
    assert fake_editor.paths == [str(vault / DECISION)]


def test_json_without_a_title_is_an_error_even_in_a_terminal(
    runner: CliRunner, vault: Path, fake_editor: FakeEditor, frozen_now: datetime, terminal: None
) -> None:
    result = runner.invoke(cli, ["new", "decision", "--json"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert json.loads(result.stderr) == {"error": {"type": "usage_error", "message": NO_TITLE_ERROR}}
    assert note_files(vault) == []


# notes new: the editor


def test_editor_is_skipped_outside_a_terminal(
    runner: CliRunner, vault: Path, git_cmd: Git, fake_editor: FakeEditor, frozen_now: datetime
) -> None:
    fake_editor.write(decision_text("Should not be written"))

    result = runner.invoke(cli, ["new", "decision", TITLE])

    assert result.exit_code == 0, result.output
    assert result.stdout == f"[{DECISION}]({vault / DECISION}) - {TITLE}\n"
    assert fake_editor.paths == []
    assert f"# {TITLE}\n" in (vault / DECISION).read_text(encoding="utf-8")
    assert subjects(git_cmd, vault)[0] == f"notes: update {DECISION}"


def test_missing_editor_in_a_terminal_is_an_error_before_anything_is_created(
    runner: CliRunner, vault: Path, git_cmd: Git, frozen_now: datetime, terminal: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)

    result = runner.invoke(cli, ["new", "decision", TITLE], input="\n")

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "Error: no editor configured: set $VISUAL or $EDITOR\n"
    assert note_files(vault) == []
    assert status(git_cmd, vault) == []


def test_editor_failure_is_reported_and_the_file_is_kept_uncommitted(
    runner: CliRunner, vault: Path, git_cmd: Git, fake_editor: FakeEditor, frozen_now: datetime, terminal: None
) -> None:
    fake_editor.fail(3)

    result = runner.invoke(cli, ["new", "decision", TITLE, "--name", "kafka"])

    path = "notes/2026-09-02-kafka.md"
    assert result.exit_code == 1
    assert result.stderr == (
        f"Error: editor `{fake_editor.script}` exited with status 3; {vault / path} is left as it is\n"
    )
    assert (vault / path).is_file()
    assert status(git_cmd, vault) == [f"?? {path}"]
    assert indexed_paths(vault) == []


def test_editor_failure_json(
    runner: CliRunner, vault: Path, fake_editor: FakeEditor, frozen_now: datetime, terminal: None
) -> None:
    fake_editor.fail(1)

    result = runner.invoke(cli, ["new", "decision", TITLE, "--name", "kafka", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.stderr)["error"]["type"] == "editor_error"


# notes new: filenames and arguments


def test_collision_appends_a_suffix(runner: CliRunner, vault: Path, frozen_now: datetime) -> None:
    first = runner.invoke(cli, ["new", "decision", TITLE])
    second = runner.invoke(cli, ["new", "decision", TITLE])
    third = runner.invoke(cli, ["new", "decision", TITLE])

    assert (first.exit_code, second.exit_code, third.exit_code) == (0, 0, 0)
    assert second.stdout.startswith(f"[{DECISION[:-3]}-2.md](")
    assert third.stdout.startswith(f"[{DECISION[:-3]}-3.md](")
    assert indexed_paths(vault) == sorted([DECISION, DECISION[:-3] + "-2.md", DECISION[:-3] + "-3.md"])


def test_note_created_at_a_freed_path_does_not_inherit_its_deliveries(
    runner: CliRunner, vault: Path, frozen_now: datetime
) -> None:
    """Sync never touches `deliveries`, so a note deleted by hand leaves rows behind; the next note there is clean."""
    assert runner.invoke(cli, ["new", "decision", TITLE]).exit_code == 0
    deliver(vault, DECISION, "2026-09-02T09:00:00+03:00")
    (vault / DECISION).unlink()
    assert runner.invoke(cli, ["sync"]).exit_code == 0

    result = runner.invoke(cli, ["new", "decision", TITLE])

    assert result.exit_code == 0, result.output
    assert result.stdout.startswith(f"[{DECISION}](")
    assert delivery_paths(vault) == []
    assert runner.invoke(cli, ["list", "--unread"]).stdout == ""


def test_cyrillic_title_yields_a_cyrillic_slug(runner: CliRunner, vault: Path, frozen_now: datetime) -> None:
    result = runner.invoke(cli, ["new", "decision", "Обновления задач через Kafka"])

    path = "notes/2026-09-02-обновления-задач-через-kafka.md"
    assert result.exit_code == 0, result.output
    assert result.stdout == f"[{path}]({vault / path}) - Обновления задач через Kafka\n"
    assert "# Обновления задач через Kafka\n" in (vault / path).read_text(encoding="utf-8")
    assert indexed_paths(vault) == [path]


def test_filename_date_comes_from_the_clock(runner: CliRunner, vault: Path, frozen_now: datetime) -> None:
    result = runner.invoke(cli, ["new", "fact", "Clock", "--name", "clock"])

    assert result.exit_code == 0, result.output
    assert note_files(vault) == ["2026-09-02-clock.md"]


def test_unknown_kind_fails_before_anything_is_created(
    runner: CliRunner, vault: Path, git_cmd: Git, frozen_now: datetime
) -> None:
    result = runner.invoke(cli, ["new", "recipe", "Borscht"])

    assert result.exit_code == 1
    assert result.stderr == (
        "Error: unknown kind `recipe` (known kinds: decision, event, fact, idea, promise, reminder)\n"
    )
    assert note_files(vault) == []
    assert status(git_cmd, vault) == []


def test_title_is_required_outside_a_terminal(runner: CliRunner, vault: Path, frozen_now: datetime) -> None:
    result = runner.invoke(cli, ["new", "decision"])

    assert result.exit_code == 1
    assert result.stderr == f"Error: {NO_TITLE_ERROR}\n"
    assert note_files(vault) == []


def test_blank_title_is_rejected(runner: CliRunner, vault: Path, frozen_now: datetime) -> None:
    result = runner.invoke(cli, ["new", "decision", "   "])

    assert result.exit_code == 1
    assert result.stderr == "Error: the title must not be empty\n"
    assert note_files(vault) == []


def test_short_name_without_letters_or_digits_is_rejected(runner: CliRunner, vault: Path, frozen_now: datetime) -> None:
    result = runner.invoke(cli, ["new", "decision", TITLE, "--name", "!!!"])

    assert result.exit_code == 1
    assert result.stderr == "Error: cannot build a filename: `!!!` has no letters or digits to build a filename from\n"
    assert note_files(vault) == []


# notes new: invalid results


def test_reminder_without_schedule_outside_a_terminal_is_created_invalid_and_uncommitted(
    runner: CliRunner, vault: Path, git_cmd: Git, frozen_now: datetime
) -> None:
    result = runner.invoke(cli, ["new", "reminder", "Water the plants"])

    path = "notes/2026-09-02-water-the-plants.md"
    assert result.exit_code == 1
    assert result.stdout == f"[{path}]({vault / path}) - Water the plants\n"
    assert result.stderr == (
        f"{vault / path}:8: schedule is required for a reminder\n"
        f"Error: {path} is not a valid note and stays uncommitted; fix it and run `notes sync`\n"
    )
    assert "schedule: \n" in (vault / path).read_text(encoding="utf-8")
    assert status(git_cmd, vault) == [f"?? {path}"]
    assert subjects(git_cmd, vault) == ["notes: initialize vault"]
    assert indexed_paths(vault) == []
    assert invalid_paths(vault) == [path]


def test_invalid_result_json(runner: CliRunner, vault: Path, frozen_now: datetime) -> None:
    result = runner.invoke(cli, ["new", "reminder", "Water the plants", "--json"])

    path = "notes/2026-09-02-water-the-plants.md"
    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "path": path,
        "abs_path": str(vault / path),
        "title": "Water the plants",
        "valid": False,
        "errors": [{"line": 8, "message": "schedule is required for a reminder"}],
    }
    assert result.stderr == ""


def test_fixing_the_note_by_hand_commits_it_on_the_next_command(
    runner: CliRunner, vault: Path, git_cmd: Git, frozen_now: datetime
) -> None:
    assert runner.invoke(cli, ["new", "reminder", "Water the plants"]).exit_code == 1
    path = "notes/2026-09-02-water-the-plants.md"
    (vault / path).write_text(reminder_text("at 2026-09-09T10:00:00+03:00"), encoding="utf-8")

    result = runner.invoke(cli, ["sync"])

    assert result.exit_code == 0
    assert subjects(git_cmd, vault)[0] == f"notes: update {path}"
    assert invalid_paths(vault) == []


def test_new_commits_only_its_own_file_when_an_invalid_sibling_exists(
    runner: CliRunner, vault: Path, git_cmd: Git, frozen_now: datetime
) -> None:
    bad = vault / "notes" / "2026-09-01-bad.md"
    bad.write_text("---\nstatus: active\n---\n\n# No kind\n", encoding="utf-8")

    result = runner.invoke(cli, ["new", "decision", TITLE])

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert subjects(git_cmd, vault)[0] == f"notes: update {DECISION}"
    assert status(git_cmd, vault) == ["?? notes/2026-09-01-bad.md"]


# notes edit


def test_edit_opens_the_note_and_commits_the_change(
    runner: CliRunner, vault: Path, git_cmd: Git, fake_editor: FakeEditor, terminal: None, existing: str
) -> None:
    fake_editor.write(decision_text("Kafka, not WebSocket"))

    result = runner.invoke(cli, ["edit", "2026-09-02-project-atlas-task-updates-over-kafka.md"])

    assert result.exit_code == 0, result.output
    assert result.stdout == f"[{DECISION}]({vault / DECISION}) - Kafka, not WebSocket\n"
    assert result.stderr == ""
    assert fake_editor.paths == [str(vault / DECISION)]
    assert subjects(git_cmd, vault) == [
        f"notes: update {DECISION}",
        f"notes: update {DECISION}",
        "notes: initialize vault",
    ]
    assert status(git_cmd, vault) == []
    assert index_value(vault, "SELECT title FROM notes WHERE path = ?", DECISION) == "Kafka, not WebSocket"


@pytest.mark.parametrize("form", [DECISION, "2026-09-02-project-atlas-task-updates-over-kafka.md", "absolute"])
def test_edit_accepts_every_id_form(
    runner: CliRunner, vault: Path, fake_editor: FakeEditor, terminal: None, existing: str, form: str
) -> None:
    note_id = str(vault / DECISION) if form == "absolute" else form

    result = runner.invoke(cli, ["edit", note_id])

    assert result.exit_code == 0, result.output
    assert result.stdout.startswith(f"[{DECISION}](")
    assert fake_editor.paths == [str(vault / DECISION)]


def test_edit_normalizes_a_relative_schedule(
    runner: CliRunner, vault: Path, git_cmd: Git, fake_editor: FakeEditor, frozen_now: datetime, terminal: None
) -> None:
    path = "notes/2026-09-02-plants.md"
    (vault / path).write_text(reminder_text("at 2026-09-09T10:00:00+03:00"), encoding="utf-8")
    assert runner.invoke(cli, ["sync"]).exit_code == 0
    fake_editor.write(reminder_text("every 3 days"))

    result = runner.invoke(cli, ["edit", path])

    assert result.exit_code == 0, result.output
    assert (vault / path).read_text(encoding="utf-8") == reminder_text("every 3 days from 2026-09-02T12:00:00+03:00")
    assert subjects(git_cmd, vault)[0] == f"notes: update {path}"
    assert status(git_cmd, vault) == []


def test_edit_of_an_unknown_id_fails(runner: CliRunner, vault: Path, fake_editor: FakeEditor, terminal: None) -> None:
    result = runner.invoke(cli, ["edit", "nope.md"])

    assert result.exit_code == 1
    assert result.stderr == "Error: no note at notes/nope.md\n"
    assert fake_editor.paths == []


def test_edit_reports_an_invalid_result_with_line_numbers_and_leaves_it_uncommitted(
    runner: CliRunner, vault: Path, git_cmd: Git, fake_editor: FakeEditor, terminal: None, existing: str
) -> None:
    fake_editor.write("---\nkind: decision\nstatus: active\n---\n\n# First\n\n# Second\n")

    result = runner.invoke(cli, ["edit", DECISION])

    assert result.exit_code == 1
    assert result.stdout == f"[{DECISION}]({vault / DECISION}) - First\n"
    assert result.stderr == (
        f"{vault / DECISION}:8: more than one H1: a note has exactly one title\n"
        f"Error: {DECISION} is not a valid note and stays uncommitted; fix it and run `notes sync`\n"
    )
    assert status(git_cmd, vault) == [f" M {DECISION}"]
    assert subjects(git_cmd, vault) == [f"notes: update {DECISION}", "notes: initialize vault"]
    assert indexed_paths(vault) == []
    assert invalid_paths(vault) == [DECISION]


def test_edit_of_a_note_without_a_title_prints_the_bare_link(
    runner: CliRunner, vault: Path, fake_editor: FakeEditor, terminal: None, existing: str
) -> None:
    fake_editor.write("---\nkind: decision\nstatus: active\n---\n\nno heading\n")

    result = runner.invoke(cli, ["edit", DECISION])

    assert result.exit_code == 1
    assert result.stdout == f"[{DECISION}]({vault / DECISION})\n"
    assert f"{vault / DECISION}:5: missing H1 title" in result.stderr


def test_edit_outside_a_terminal_checks_in_a_manual_change(
    runner: CliRunner, vault: Path, git_cmd: Git, fake_editor: FakeEditor, existing: str
) -> None:
    fake_editor.write(decision_text("Should not be written"))
    (vault / DECISION).write_text(decision_text("Edited by hand"), encoding="utf-8")

    result = runner.invoke(cli, ["edit", DECISION])

    assert result.exit_code == 0, result.output
    assert result.stdout == f"[{DECISION}]({vault / DECISION}) - Edited by hand\n"
    assert fake_editor.paths == []
    assert subjects(git_cmd, vault)[0] == f"notes: update {DECISION}"
    assert status(git_cmd, vault) == []


def test_edit_requires_an_editor_in_a_terminal(
    runner: CliRunner, vault: Path, terminal: None, existing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)

    result = runner.invoke(cli, ["edit", DECISION])

    assert result.exit_code == 1
    assert result.stderr == "Error: no editor configured: set $VISUAL or $EDITOR\n"


def test_edit_editor_failure_is_reported(
    runner: CliRunner, vault: Path, git_cmd: Git, fake_editor: FakeEditor, terminal: None, existing: str
) -> None:
    fake_editor.write(decision_text("Saved before the crash"))
    fake_editor.fail(2)

    result = runner.invoke(cli, ["edit", DECISION])

    assert result.exit_code == 1
    assert result.stderr == (
        f"Error: editor `{fake_editor.script}` exited with status 2; {vault / DECISION} is left as it is\n"
    )
    assert "# Saved before the crash\n" in (vault / DECISION).read_text(encoding="utf-8")
    assert status(git_cmd, vault) == [f" M {DECISION}"]


def test_edit_json(runner: CliRunner, vault: Path, fake_editor: FakeEditor, terminal: None, existing: str) -> None:
    fake_editor.write(decision_text("Kafka, not WebSocket"))

    result = runner.invoke(cli, ["edit", DECISION, "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "path": DECISION,
        "abs_path": str(vault / DECISION),
        "title": "Kafka, not WebSocket",
        "valid": True,
        "errors": [],
    }


# Registration


def test_new_and_edit_are_listed_in_root_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "  new " in result.output
    assert "  edit " in result.output


def test_new_help_needs_no_vault(runner: CliRunner, home: Path) -> None:
    result = runner.invoke(cli, ["new", "--help"])

    assert result.exit_code == 0
    assert "--name SHORT-NAME" in result.output
    assert not (home / ".notes").exists()


def test_new_without_a_vault_fails(runner: CliRunner, home: Path) -> None:
    result = runner.invoke(cli, ["new", "decision", TITLE])

    assert result.exit_code == 1
    assert result.stderr == "Error: no vault at ~/.notes, run `notes init`\n"
