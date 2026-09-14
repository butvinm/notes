"""Tests for the root CLI group: help, version, the shared `--json` option, and `NotesError` handling."""

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from notes import __version__
from notes.cli import cli, json_option
from notes.errors import GitError
from notes.output import emit


def test_help_lists_root_group(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "Usage: notes [OPTIONS] COMMAND [ARGS]..." in result.output
    assert "--version" in result.output


WHOLE_SUMMARIES = {
    "root": (
        ["--help"],
        "Validate every note and list the invalid files with line numbers; exit 1 when any file is invalid.",
    ),
    "draft": (
        ["draft", "--help"],
        "Turn the draft into a note: validate it, write it under notes/, index and commit it, delete the draft.",
    ),
    "notifications": (
        ["notifications", "--help"],
        "Report whether the timer is installed, enabled, and active, its next run, and the recent service journal.",
    ),
}


@pytest.mark.parametrize(("args", "sentence"), WHOLE_SUMMARIES.values(), ids=WHOLE_SUMMARIES.keys())
def test_group_help_shows_whole_command_summaries(runner: CliRunner, args: list[str], sentence: str) -> None:
    """click would cut each summary at 45 characters with `...`; the groups print the whole first paragraph, wrapped."""
    result = runner.invoke(cli, args, terminal_width=80)

    assert result.exit_code == 0
    commands = result.output.split("Commands:", 1)[1]
    assert " ".join(commands.split()).count(sentence) == 1
    assert "..." not in commands
    assert all(len(line) <= 80 for line in commands.splitlines())


def test_summary_prefers_short_help_and_joins_the_first_paragraph() -> None:
    from notes.cli import summary

    with_short = click.Command("a", help="Long text.\n\nMore.", short_help="Short.")
    wrapped = click.Command("b", help="First line\ncontinues here.\n\nSecond paragraph.")
    bare = click.Command("c")

    assert summary(with_short) == "Short."
    assert summary(wrapped) == "First line continues here."
    assert summary(bare) == ""


def test_version_prints_package_version(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == f"notes, version {__version__}"


def test_module_entry_point_runs(home: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "notes", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == f"notes, version {__version__}"


@pytest.fixture
def probe_commands() -> Iterator[None]:
    """Register two throwaway subcommands on the root group: one that emits output, one that raises a `NotesError`."""

    @click.command("greet")
    @json_option
    @click.pass_context
    def greet(ctx: click.Context) -> None:
        emit(ctx, "hello, human", {"greeting": "hello"})

    @click.command("boom")
    @json_option
    def boom() -> None:
        raise GitError("history diverged, resolve it with git")

    cli.add_command(greet)
    cli.add_command(boom)
    yield
    del cli.commands["greet"]
    del cli.commands["boom"]


def test_json_option_is_listed_in_command_help(runner: CliRunner, probe_commands: None) -> None:
    result = runner.invoke(cli, ["greet", "--help"])

    assert result.exit_code == 0
    assert "--json" in result.output


def test_command_prints_human_output_by_default(runner: CliRunner, probe_commands: None) -> None:
    result = runner.invoke(cli, ["greet"])

    assert result.exit_code == 0
    assert result.output == "hello, human\n"


def test_json_option_switches_to_machine_output(runner: CliRunner, probe_commands: None) -> None:
    result = runner.invoke(cli, ["greet", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"greeting": "hello"}
    assert result.stderr == ""


def test_notes_error_exits_1_with_human_message(runner: CliRunner, probe_commands: None) -> None:
    result = runner.invoke(cli, ["boom"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "Error: history diverged, resolve it with git\n"


def test_notes_error_json_envelope_on_stderr(runner: CliRunner, probe_commands: None) -> None:
    result = runner.invoke(cli, ["boom", "--json"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "error": {"type": "git_error", "message": "history diverged, resolve it with git"},
    }


def test_click_usage_error_keeps_exit_code_2(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["no-such-command"])

    assert result.exit_code == 2
