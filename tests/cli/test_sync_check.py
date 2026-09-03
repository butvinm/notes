"""Tests for `notes sync`, `notes check`, and the pre-command sync that every vault command runs."""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from notes import document
from notes.cli import Session, cli, get_session, vault_command

NO_VAULT = "no vault at ~/.notes, run `notes init`"
NO_KIND = "---\nstatus: active\n---\n\n# No kind\n"


def note_text(title: str = "A title") -> str:
    return f"---\nkind: decision\nstatus: active\n---\n\n# {title}\n"


def write(vault: Path, name: str, text: str) -> str:
    target = vault / "notes" / name
    target.write_text(text, encoding="utf-8")
    return f"notes/{name}"


# notes sync


def test_sync_reports_new_notes(runner: CliRunner, vault: Path) -> None:
    first = write(vault, "2026-09-02-a.md", note_text())
    second = write(vault, "2026-09-02-b.md", note_text())

    result = runner.invoke(cli, ["sync"])

    assert result.exit_code == 0
    assert result.output == f"changed: {first}\nchanged: {second}\n2 changed, 0 removed, 0 unchanged, 0 invalid\n"


def test_sync_reports_unchanged_and_removed_notes(runner: CliRunner, vault: Path) -> None:
    write(vault, "2026-09-02-a.md", note_text())
    removed = write(vault, "2026-09-02-b.md", note_text())
    assert runner.invoke(cli, ["sync"]).exit_code == 0
    (vault / removed).unlink()

    result = runner.invoke(cli, ["sync"])

    assert result.exit_code == 0
    assert result.output == f"removed: {removed}\n0 changed, 1 removed, 1 unchanged, 0 invalid\n"


def test_sync_warns_about_invalid_files_and_exits_0(runner: CliRunner, vault: Path) -> None:
    bad = write(vault, "2026-09-02-bad.md", NO_KIND)

    result = runner.invoke(cli, ["sync"])

    assert result.exit_code == 0
    assert result.stdout == "0 changed, 0 removed, 0 unchanged, 1 invalid\n"
    assert result.stderr == f"warning: {vault / bad}:1: kind is required\n"


def test_sync_json(runner: CliRunner, vault: Path) -> None:
    good = write(vault, "2026-09-02-a.md", note_text())
    bad = write(vault, "2026-09-02-bad.md", NO_KIND)

    result = runner.invoke(cli, ["sync", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "changed": [good],
        "removed": [],
        "invalid": [
            {"path": bad, "abs_path": str(vault / bad), "errors": [{"line": 1, "message": "kind is required"}]}
        ],
        "unchanged_count": 0,
    }
    assert result.stderr == ""


def test_sync_parses_a_new_file_exactly_once_per_invocation(
    runner: CliRunner, vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real = document.parse

    def counting(path: str, text: str) -> document.Document:
        calls.append(path)
        return real(path, text)

    monkeypatch.setattr(document, "parse", counting)
    path = write(vault, "2026-09-02-a.md", note_text())

    result = runner.invoke(cli, ["sync"])

    assert result.exit_code == 0
    assert calls == [path]


# notes check


def test_check_ok(runner: CliRunner, vault: Path) -> None:
    write(vault, "2026-09-02-a.md", note_text())

    result = runner.invoke(cli, ["check"])

    assert result.exit_code == 0
    assert result.output == "ok: 1 note indexed, no invalid files\n"


def test_check_ok_json(runner: CliRunner, vault: Path) -> None:
    write(vault, "2026-09-02-a.md", note_text())
    write(vault, "2026-09-02-b.md", note_text())

    result = runner.invoke(cli, ["check", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"ok": True, "note_count": 2, "invalid": [], "newer_defaults_available": False}
    assert result.stderr == ""


def test_check_lists_errors_with_line_numbers_and_exits_1(runner: CliRunner, vault: Path) -> None:
    write(vault, "2026-09-02-a.md", note_text())
    bad = write(vault, "2026-09-02-bad.md", "---\nkind: decision\nstatus: bogus\ntags: nope\n---\n\n# Bad\n")
    undated = write(vault, "kafka.md", note_text())

    result = runner.invoke(cli, ["check"])

    assert result.exit_code == 1
    assert result.stdout.splitlines() == [
        f"{vault / bad}:3: status must be active or archived",
        f"{vault / bad}:4: tags must be a list of strings",
        f"{vault / undated}: filename `kafka.md` must look like YYYY-MM-DD-<slug>.md; rename it with `notes move`",
        "2 invalid files, 1 valid note indexed",
    ]
    assert result.stderr == ""


def test_check_json_with_invalid_files(runner: CliRunner, vault: Path) -> None:
    bad = write(vault, "2026-09-02-bad.md", NO_KIND)

    result = runner.invoke(cli, ["check", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "ok": False,
        "note_count": 0,
        "invalid": [
            {"path": bad, "abs_path": str(vault / bad), "errors": [{"line": 1, "message": "kind is required"}]}
        ],
        "newer_defaults_available": False,
    }


def test_check_notices_newer_packaged_defaults(runner: CliRunner, vault: Path) -> None:
    (vault / ".defaults-version").write_text("0\n", encoding="utf-8")
    write(vault, "2026-09-02-a.md", note_text())

    result = runner.invoke(cli, ["check"])

    assert result.exit_code == 0
    assert result.stdout == "ok: 1 note indexed, no invalid files\n"
    assert result.stderr.startswith("notice: packaged defaults version ")
    assert "newer than the vault's (version 0)" in result.stderr
    assert result.stderr.count("\n") == 1


def test_check_notice_names_a_missing_stamp(runner: CliRunner, vault: Path) -> None:
    (vault / ".defaults-version").unlink()

    result = runner.invoke(cli, ["check"])

    assert result.exit_code == 0
    assert "newer than the vault's (no recorded version)" in result.stderr


def test_check_json_reports_newer_defaults_without_the_notice(runner: CliRunner, vault: Path) -> None:
    (vault / ".defaults-version").write_text("0\n", encoding="utf-8")

    result = runner.invoke(cli, ["check", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["newer_defaults_available"] is True
    assert result.stderr == ""


# The pre-command sync


@pytest.mark.parametrize("command", ["sync", "check"])
def test_vault_commands_fail_without_a_vault(runner: CliRunner, home: Path, command: str) -> None:
    result = runner.invoke(cli, [command])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == f"Error: {NO_VAULT}\n"


def test_missing_vault_error_as_json(runner: CliRunner, home: Path) -> None:
    result = runner.invoke(cli, ["sync", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.stderr) == {"error": {"type": "no_vault", "message": NO_VAULT}}


@pytest.mark.parametrize("command", ["sync", "check"])
def test_help_never_touches_the_vault(runner: CliRunner, home: Path, command: str) -> None:
    result = runner.invoke(cli, [command, "--help"])

    assert result.exit_code == 0
    assert "--json" in result.output
    assert not (home / ".notes").exists()


def test_check_is_listed_in_root_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])

    assert "  sync " in result.output
    assert "  check " in result.output


@pytest.fixture
def probe_commands() -> Iterator[dict[str, object]]:
    """Register throwaway vault commands that expose their session, and remove them afterwards."""
    seen: dict[str, object] = {}

    @vault_command("probe", index_only=True)
    @click.pass_context
    def probe(ctx: click.Context) -> None:
        session = get_session(ctx)
        seen["session"] = session
        seen["commit"] = session.commit
        seen["note_count"] = session.report.note_count
        seen["auto_push"] = session.config.git.auto_push

    @vault_command("writer")
    @click.pass_context
    def writer(ctx: click.Context) -> None:
        session = get_session(ctx)
        seen["session"] = session
        write(session.vault, "2026-09-02-written.md", note_text("Written by the command"))
        report = session.sync()
        click.echo(",".join(report.changed))

    cli.add_command(probe)
    cli.add_command(writer)
    yield seen
    del cli.commands["probe"]
    del cli.commands["writer"]


def test_index_only_command_gets_a_synced_session_that_never_commits(
    runner: CliRunner, vault: Path, probe_commands: dict[str, object]
) -> None:
    write(vault, "2026-09-02-a.md", note_text())

    result = runner.invoke(cli, ["probe"])

    assert result.exit_code == 0
    session = probe_commands["session"]
    assert isinstance(session, Session)
    assert session.vault == vault
    assert probe_commands["commit"] is False
    assert probe_commands["note_count"] == 1
    assert probe_commands["auto_push"] is False
    with pytest.raises(sqlite3.ProgrammingError):
        session.conn.execute("SELECT 1")


def test_second_sync_picks_up_a_note_written_by_the_command(
    runner: CliRunner, vault: Path, probe_commands: dict[str, object]
) -> None:
    write(vault, "2026-09-02-a.md", note_text())

    result = runner.invoke(cli, ["writer"])

    assert result.exit_code == 0
    assert result.output == "notes/2026-09-02-written.md\n"
    session = probe_commands["session"]
    assert isinstance(session, Session)
    assert session.commit is True
    assert session.report.note_count == 2
