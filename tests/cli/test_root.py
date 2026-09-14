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
from notes.cli import SECTIONS, SectionedGroup, cli, json_option, summary
from notes.errors import GitError
from notes.output import emit


def test_help_lists_root_group(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "Usage: notes [OPTIONS] COMMAND [ARGS]..." in result.output
    assert "--version" in result.output


def test_root_help_groups_commands_under_section_headings(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"], terminal_width=80)

    assert result.exit_code == 0
    assert "Commands:" not in result.output
    headings = [line for line in result.output.splitlines() if line and not line.startswith(" ")]
    assert headings == [
        "Usage: notes [OPTIONS] COMMAND [ARGS]...",
        "Options:",
        "Notes:",
        "Reminders:",
        "Drafting:",
        "Vault:",
    ]
    vault = result.output.split("Vault:", 1)[1]
    assert vault.splitlines()[1:] == [
        "  init           Create the vault, or clone it from a remote",
        "  sync           Index changed notes",
        "  check          Validate every note",
        "  push           Push the vault to its remote",
        "  pull           Fast-forward the vault from its remote",
    ]


def test_every_root_command_sits_in_exactly_one_section() -> None:
    sectioned = [name for _, names in SECTIONS for name in names]

    assert sorted(sectioned) == sorted(cli.commands)
    assert len(sectioned) == len(set(sectioned))


def walk(group: click.Group, prefix: str = "") -> Iterator[tuple[str, click.Command]]:
    """Every command of the tree with its full name, `notes draft save` as `draft save`."""
    for name, command in group.commands.items():
        yield f"{prefix}{name}", command
        if isinstance(command, click.Group):
            yield from walk(command, f"{prefix}{name} ")


@pytest.mark.parametrize(("name", "command"), list(walk(cli)), ids=[name for name, _ in walk(cli)])
def test_each_command_has_a_short_summary_that_fits_one_help_line(name: str, command: click.Command) -> None:
    """A group lists each command on one line, the way `git` and `uv` do; the summary is a few words, never cut."""
    assert command.short_help, f"{name} has no short_help"
    assert len(command.short_help) <= 50, f"{name}: short_help is {len(command.short_help)} characters"
    assert not command.short_help.endswith(".")
    assert command.get_short_help_str(limit=45) == command.short_help


@pytest.mark.parametrize("args", [["draft", "--help"], ["notifications", "--help"]], ids=["draft", "notifications"])
def test_sub_group_help_lists_commands_on_one_line_each(runner: CliRunner, args: list[str]) -> None:
    result = runner.invoke(cli, args, terminal_width=80)

    assert result.exit_code == 0
    commands = result.output.split("Commands:", 1)[1].strip("\n").splitlines()
    assert commands
    assert all(line.startswith("  ") and not line.startswith("   ") and "..." not in line for line in commands)


def test_sectioned_group_lists_unplaced_commands_under_other(runner: CliRunner) -> None:
    group = SectionedGroup("probe", sections=(("First", ("a",)),))
    group.add_command(click.Command("a", short_help="The a"))
    group.add_command(click.Command("b", short_help="The b"))
    group.add_command(click.Command("hidden", short_help="Never shown", hidden=True))

    result = runner.invoke(group, ["--help"])

    assert result.exit_code == 0
    assert result.output.split("Options:", 1)[1].endswith("First:\n  a  The a\n\nOther:\n  b  The b\n")
    assert "hidden" not in result.output


def test_summary_prefers_short_help_and_joins_the_first_paragraph() -> None:
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
