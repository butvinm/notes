"""Tests for `notes push`, `notes pull`, and the auto-commit and auto-push that every vault command runs."""

import json
import shutil
import sqlite3
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from notes import proc
from notes.cli import cli, get_session, vault_command
from notes.proc import CommandResult

Git = Callable[..., str]
NO_KIND = "---\nstatus: active\n---\n\n# No kind\n"
AUTO_PUSH = "[git]\nauto_push = true\n"


def note_text(title: str = "A title") -> str:
    return f"---\nkind: decision\nstatus: active\n---\n\n# {title}\n"


def write(vault: Path, name: str, text: str = note_text()) -> str:
    target = vault / "notes" / name
    target.parent.mkdir(exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return f"notes/{name}"


def subjects(git_cmd: Git, repo: Path) -> list[str]:
    return git_cmd(repo, "log", "--format=%s").splitlines()


def remote_head(git_cmd: Git, remote: Path) -> str | None:
    return git_cmd(remote, "for-each-ref", "--format=%(objectname)", "refs/heads/master").strip() or None


def head(git_cmd: Git, repo: Path) -> str:
    return git_cmd(repo, "rev-parse", "HEAD").strip()


def upstream(git_cmd: Git, repo: Path) -> str:
    return git_cmd(repo, "rev-parse", "--abbrev-ref", "@{upstream}").strip()


def indexed_paths(vault: Path) -> list[str]:
    conn = sqlite3.connect(vault / "index.sqlite")
    try:
        return [row[0] for row in conn.execute("SELECT path FROM notes ORDER BY path")]
    finally:
        conn.close()


def push_from_other_device(git_cmd: Git, remote: Path, tmp_path: Path, name: str) -> str:
    """Clone the remote elsewhere, add a note there, and push it, as a second device would."""
    other = tmp_path / "other"
    if not other.exists():
        git_cmd(tmp_path, "clone", "-q", str(remote), str(other))
    path = write(other, name, note_text("From the other device"))
    git_cmd(other, "add", "-A", "--", "notes")
    git_cmd(other, "commit", "-q", "-m", "from the other device")
    git_cmd(other, "push", "-q")
    return path


@pytest.fixture
def git_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """Record every command that goes through `run_command` while still running it."""
    calls: list[tuple[str, ...]] = []
    real = proc.runner

    def recording(
        args: Sequence[str | Path],
        *,
        cwd: Path | None = None,
        input: str | None = None,
        timeout: float | None = None,
        capture: bool = True,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        calls.append(tuple(str(arg) for arg in args))
        return real(args, cwd=cwd, input=input, timeout=timeout, capture=capture, env=env)

    monkeypatch.setattr(proc, "runner", recording)
    return calls


def pushes(calls: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    return [call for call in calls if call[:2] == ("git", "push")]


@pytest.fixture
def with_remote(vault: Path, remote: Path, git_cmd: Git) -> Path:
    """The vault with `origin` pointing at the bare remote; nothing pushed yet."""
    git_cmd(vault, "remote", "add", "origin", str(remote))
    return vault


# The auto-commit step through the CLI


def test_sync_commits_a_new_note(runner: CliRunner, vault: Path, git_cmd: Git) -> None:
    path = write(vault, "2026-09-02-a.md")

    result = runner.invoke(cli, ["sync"])

    assert result.exit_code == 0
    assert result.stderr == ""
    assert subjects(git_cmd, vault) == [f"notes: update {path}", "notes: initialize vault"]


def test_any_vault_command_commits_a_pending_manual_edit(runner: CliRunner, vault: Path, git_cmd: Git) -> None:
    write(vault, "2026-09-02-a.md")
    write(vault, "2026-09-02-b.md")

    result = runner.invoke(cli, ["check"])

    assert result.exit_code == 0
    assert subjects(git_cmd, vault)[0] == "notes: sync 2 files"


def test_invalid_file_stays_uncommitted_while_the_valid_sibling_is_committed(
    runner: CliRunner, vault: Path, git_cmd: Git
) -> None:
    good = write(vault, "2026-09-02-good.md")
    bad = write(vault, "2026-09-02-bad.md", NO_KIND)

    result = runner.invoke(cli, ["sync"])

    assert result.exit_code == 0
    assert subjects(git_cmd, vault)[0] == f"notes: update {good}"
    assert git_cmd(vault, "status", "--porcelain") == f"?? {bad}\n"


def test_commit_warning_goes_to_stderr_and_the_command_still_succeeds(
    runner: CliRunner, vault: Path, git_cmd: Git, monkeypatch: pytest.MonkeyPatch
) -> None:
    for variable in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(variable)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    path = write(vault, "2026-09-02-a.md")

    result = runner.invoke(cli, ["sync"])

    assert result.exit_code == 0
    assert result.stdout == f"changed: {path}\n1 changed, 0 removed, 0 unchanged, 0 invalid\n"
    assert result.stderr.startswith("warning: git commit failed: ")
    assert subjects(git_cmd, vault) == ["notes: initialize vault"]


def test_auto_push_disabled_leaves_commits_unpushed(
    runner: CliRunner, with_remote: Path, remote: Path, git_cmd: Git, git_calls: list[tuple[str, ...]]
) -> None:
    write(with_remote, "2026-09-02-a.md")

    result = runner.invoke(cli, ["sync"])

    assert result.exit_code == 0
    assert pushes(git_calls) == []
    assert remote_head(git_cmd, remote) is None


def test_auto_push_without_a_remote_attempts_nothing_and_prints_no_warning(
    runner: CliRunner, vault: Path, git_calls: list[tuple[str, ...]]
) -> None:
    (vault / "config.toml").write_text(AUTO_PUSH, encoding="utf-8")
    write(vault, "2026-09-02-a.md")

    result = runner.invoke(cli, ["sync"])

    assert result.exit_code == 0
    assert result.stderr == ""
    assert pushes(git_calls) == []


def test_auto_push_sets_the_upstream_first_and_pushes_plainly_afterwards(
    runner: CliRunner, with_remote: Path, remote: Path, git_cmd: Git, git_calls: list[tuple[str, ...]]
) -> None:
    (with_remote / "config.toml").write_text(AUTO_PUSH, encoding="utf-8")
    write(with_remote, "2026-09-02-a.md")

    first = runner.invoke(cli, ["sync"])

    assert first.exit_code == 0
    assert first.stderr == ""
    assert pushes(git_calls) == [("git", "push", "-q", "--set-upstream", "origin", "master")]
    assert upstream(git_cmd, with_remote) == "origin/master"
    assert remote_head(git_cmd, remote) == head(git_cmd, with_remote)

    write(with_remote, "2026-09-02-b.md")
    second = runner.invoke(cli, ["check"])

    assert second.exit_code == 0
    assert pushes(git_calls)[1:] == [("git", "push", "-q", "origin", "master")]
    assert remote_head(git_cmd, remote) == head(git_cmd, with_remote)


def test_unreachable_remote_warns_and_a_later_command_retries(
    runner: CliRunner, vault: Path, git_cmd: Git, tmp_path: Path
) -> None:
    later = tmp_path / "later.git"
    git_cmd(vault, "remote", "add", "origin", str(later))
    (vault / "config.toml").write_text(AUTO_PUSH, encoding="utf-8")
    path = write(vault, "2026-09-02-a.md")

    first = runner.invoke(cli, ["sync"])

    assert first.exit_code == 0
    assert first.stderr.startswith("warning: push to origin failed: ")
    assert first.stderr.rstrip().endswith("; the commit is kept and the next command retries the push")
    assert subjects(git_cmd, vault)[0] == "notes: sync 2 files"
    assert path in git_cmd(vault, "ls-files").splitlines()

    git_cmd(tmp_path, "init", "-q", "--bare", "-b", "master", str(later))
    second = runner.invoke(cli, ["check"])

    assert second.exit_code == 0
    assert second.stderr == ""
    assert remote_head(git_cmd, later) == head(git_cmd, vault)


@pytest.fixture
def probe_commands() -> Iterator[dict[str, object]]:
    """Register throwaway vault commands that expose their session's flags, and remove them afterwards."""
    seen: dict[str, object] = {}

    @vault_command("offline-probe", offline=True)
    @click.pass_context
    def offline_probe(ctx: click.Context) -> None:
        session = get_session(ctx)
        seen["commit"] = session.commit
        seen["push"] = session.push

    @vault_command("index-probe", index_only=True)
    @click.pass_context
    def index_probe(ctx: click.Context) -> None:
        session = get_session(ctx)
        seen["commit"] = session.commit
        seen["push"] = session.push

    cli.add_command(offline_probe)
    cli.add_command(index_probe)
    yield seen
    del cli.commands["offline-probe"]
    del cli.commands["index-probe"]


def test_offline_command_commits_but_never_pushes(
    runner: CliRunner,
    with_remote: Path,
    remote: Path,
    git_cmd: Git,
    git_calls: list[tuple[str, ...]],
    probe_commands: dict[str, object],
) -> None:
    (with_remote / "config.toml").write_text(AUTO_PUSH, encoding="utf-8")
    path = write(with_remote, "2026-09-02-a.md")

    result = runner.invoke(cli, ["offline-probe"])

    assert result.exit_code == 0
    assert probe_commands == {"commit": True, "push": False}
    assert subjects(git_cmd, with_remote)[0] == "notes: sync 2 files"
    assert path in git_cmd(with_remote, "ls-files").splitlines()
    assert pushes(git_calls) == []
    assert remote_head(git_cmd, remote) is None


def test_index_only_command_neither_commits_nor_pushes(
    runner: CliRunner,
    with_remote: Path,
    git_cmd: Git,
    git_calls: list[tuple[str, ...]],
    probe_commands: dict[str, object],
) -> None:
    (with_remote / "config.toml").write_text(AUTO_PUSH, encoding="utf-8")
    write(with_remote, "2026-09-02-a.md")

    result = runner.invoke(cli, ["index-probe"])

    assert result.exit_code == 0
    assert probe_commands == {"commit": False, "push": False}
    assert subjects(git_cmd, with_remote) == ["notes: initialize vault"]
    assert git_calls == []


# notes push


def test_push_without_an_upstream_sets_it(runner: CliRunner, with_remote: Path, remote: Path, git_cmd: Git) -> None:
    path = write(with_remote, "2026-09-02-a.md")

    result = runner.invoke(cli, ["push"])

    assert result.exit_code == 0
    assert result.output == "pushed to origin and set it as the upstream\n"
    assert upstream(git_cmd, with_remote) == "origin/master"
    assert remote_head(git_cmd, remote) == head(git_cmd, with_remote)
    assert subjects(git_cmd, with_remote)[0] == f"notes: update {path}"


def test_push_with_an_upstream_pushes_plainly(
    runner: CliRunner, with_remote: Path, remote: Path, git_cmd: Git, git_calls: list[tuple[str, ...]]
) -> None:
    git_cmd(with_remote, "push", "-q", "--set-upstream", "origin", "master")
    write(with_remote, "2026-09-02-a.md")

    result = runner.invoke(cli, ["push"])

    assert result.exit_code == 0
    assert result.output == "pushed to origin\n"
    assert pushes(git_calls) == [("git", "push", "-q", "origin", "master")]
    assert remote_head(git_cmd, remote) == head(git_cmd, with_remote)


def test_push_with_nothing_to_push(
    runner: CliRunner, with_remote: Path, git_cmd: Git, git_calls: list[tuple[str, ...]]
) -> None:
    git_cmd(with_remote, "push", "-q", "--set-upstream", "origin", "master")

    result = runner.invoke(cli, ["push"])

    assert result.exit_code == 0
    assert result.output == "nothing to push, origin is up to date\n"
    assert pushes(git_calls) == []


def test_push_json(runner: CliRunner, with_remote: Path) -> None:
    result = runner.invoke(cli, ["push", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"remote": "origin", "pushed": True, "set_upstream": True}

    again = runner.invoke(cli, ["push", "--json"])

    assert json.loads(again.stdout) == {"remote": "origin", "pushed": False, "set_upstream": False}


def test_push_does_not_auto_push_twice(runner: CliRunner, with_remote: Path, git_calls: list[tuple[str, ...]]) -> None:
    (with_remote / "config.toml").write_text(AUTO_PUSH, encoding="utf-8")
    write(with_remote, "2026-09-02-a.md")

    result = runner.invoke(cli, ["push"])

    assert result.exit_code == 0
    assert result.output == "pushed to origin and set it as the upstream\n"
    assert len(pushes(git_calls)) == 1


def test_push_without_a_remote_is_an_error(runner: CliRunner, vault: Path) -> None:
    result = runner.invoke(cli, ["push"])

    assert result.exit_code == 1
    assert result.stderr == (
        f"Error: remote origin is not configured; add it with `git remote add origin <url>` in {vault}\n"
    )


def test_push_to_an_unreachable_remote_is_an_error(
    runner: CliRunner, vault: Path, git_cmd: Git, tmp_path: Path
) -> None:
    git_cmd(vault, "remote", "add", "origin", str(tmp_path / "missing.git"))

    result = runner.invoke(cli, ["push", "--json"])

    assert result.exit_code == 1
    error = json.loads(result.stderr)["error"]
    assert error["type"] == "git_error"
    assert error["message"].startswith("push to origin failed: ")


def test_push_uses_the_configured_remote_name(runner: CliRunner, vault: Path, remote: Path, git_cmd: Git) -> None:
    git_cmd(vault, "remote", "add", "backup", str(remote))
    (vault / "config.toml").write_text('[git]\nremote = "backup"\n', encoding="utf-8")

    result = runner.invoke(cli, ["push"])

    assert result.exit_code == 0
    assert result.output == "pushed to backup and set it as the upstream\n"
    assert upstream(git_cmd, vault) == "backup/master"


def test_push_reaches_the_configured_remote_when_the_branch_tracks_another_one(
    runner: CliRunner, with_remote: Path, remote: Path, git_cmd: Git, tmp_path: Path
) -> None:
    """The remote the command names is the remote that gets the commits, whatever the branch's upstream is."""
    backup = tmp_path / "backup.git"
    git_cmd(tmp_path, "init", "-q", "--bare", "-b", "master", str(backup))
    git_cmd(with_remote, "remote", "add", "backup", str(backup))
    git_cmd(with_remote, "push", "-q", "--set-upstream", "origin", "master")
    (with_remote / "config.toml").write_text('[git]\nremote = "backup"\n', encoding="utf-8")
    write(with_remote, "2026-09-02-a.md")

    result = runner.invoke(cli, ["push"])

    assert result.exit_code == 0
    assert result.output == "pushed to backup\n"
    assert remote_head(git_cmd, backup) == head(git_cmd, with_remote)
    assert remote_head(git_cmd, remote) != head(git_cmd, with_remote)
    assert upstream(git_cmd, with_remote) == "origin/master"

    again = runner.invoke(cli, ["push"])

    assert again.output == "nothing to push, backup is up to date\n"


# notes pull


def test_pull_fast_forwards_and_indexes_new_notes(
    runner: CliRunner, with_remote: Path, remote: Path, git_cmd: Git, tmp_path: Path
) -> None:
    git_cmd(with_remote, "push", "-q", "--set-upstream", "origin", "master")
    path = push_from_other_device(git_cmd, remote, tmp_path, "2026-09-02-from-other.md")

    result = runner.invoke(cli, ["pull"])

    assert result.exit_code == 0
    assert result.output == f"pulled from origin\nchanged: {path}\n1 changed, 0 removed, 0 unchanged, 0 invalid\n"
    assert head(git_cmd, with_remote) == remote_head(git_cmd, remote)
    assert indexed_paths(with_remote) == [path]


def test_pull_when_already_up_to_date(runner: CliRunner, with_remote: Path, git_cmd: Git) -> None:
    git_cmd(with_remote, "push", "-q", "--set-upstream", "origin", "master")

    result = runner.invoke(cli, ["pull"])

    assert result.exit_code == 0
    assert result.output == "already up to date with origin\n0 changed, 0 removed, 0 unchanged, 0 invalid\n"


def test_pull_json(runner: CliRunner, with_remote: Path, remote: Path, git_cmd: Git, tmp_path: Path) -> None:
    git_cmd(with_remote, "push", "-q", "--set-upstream", "origin", "master")
    path = push_from_other_device(git_cmd, remote, tmp_path, "2026-09-02-from-other.md")

    result = runner.invoke(cli, ["pull", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "remote": "origin",
        "updated": True,
        "sync": {"changed": [path], "removed": [], "invalid": [], "unchanged_count": 0},
    }


def test_pull_commits_pending_local_edits_after_fast_forwarding(
    runner: CliRunner, with_remote: Path, remote: Path, git_cmd: Git, tmp_path: Path
) -> None:
    git_cmd(with_remote, "push", "-q", "--set-upstream", "origin", "master")
    push_from_other_device(git_cmd, remote, tmp_path, "2026-09-02-from-other.md")
    local = write(with_remote, "2026-09-02-local.md", note_text("Local"))

    result = runner.invoke(cli, ["pull"])

    assert result.exit_code == 0
    assert subjects(git_cmd, with_remote)[:2] == [f"notes: update {local}", "from the other device"]


def test_pull_with_divergent_history_fails_clearly_without_merging(
    runner: CliRunner, with_remote: Path, remote: Path, git_cmd: Git, tmp_path: Path
) -> None:
    git_cmd(with_remote, "push", "-q", "--set-upstream", "origin", "master")
    push_from_other_device(git_cmd, remote, tmp_path, "2026-09-02-from-other.md")
    write(with_remote, "2026-09-02-local.md", note_text("Local"))
    assert runner.invoke(cli, ["sync"]).exit_code == 0
    before = head(git_cmd, with_remote)

    result = runner.invoke(cli, ["pull"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == (
        "Error: history diverged from origin: local and remote commits must be reconciled with git "
        f"(merge or rebase) in {with_remote}\n"
    )
    assert head(git_cmd, with_remote) == before
    assert not (with_remote / ".git" / "MERGE_HEAD").exists()
    assert git_cmd(with_remote, "status", "--porcelain") == ""
    assert not (with_remote / "notes" / "2026-09-02-from-other.md").exists()


def test_pull_from_an_unreachable_remote_is_an_error(
    runner: CliRunner, vault: Path, git_cmd: Git, tmp_path: Path
) -> None:
    git_cmd(vault, "remote", "add", "origin", str(tmp_path / "missing.git"))

    result = runner.invoke(cli, ["pull"])

    assert result.exit_code == 1
    assert result.stderr.startswith("Error: pull from origin failed: ")


@pytest.mark.parametrize("command", ["push", "pull"])
def test_push_and_pull_fail_without_a_vault(runner: CliRunner, home: Path, command: str) -> None:
    result = runner.invoke(cli, [command])

    assert result.exit_code == 1
    assert result.stderr == "Error: no vault at ~/.notes, run `notes init`\n"


@pytest.mark.parametrize("command", ["push", "pull"])
def test_push_and_pull_report_a_vault_without_a_repository(runner: CliRunner, vault: Path, command: str) -> None:
    shutil.rmtree(vault / ".git")

    result = runner.invoke(cli, [command])

    assert result.exit_code == 1
    assert result.stderr == f"Error: {vault} is not a Git repository\n"


@pytest.mark.parametrize("command", ["push", "pull"])
def test_push_and_pull_help_never_touch_the_vault(runner: CliRunner, home: Path, command: str) -> None:
    result = runner.invoke(cli, [command, "--help"])

    assert result.exit_code == 0
    assert "--json" in result.output
    assert not (home / ".notes").exists()


def test_push_and_pull_are_listed_in_root_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])

    assert "  push " in result.output
    assert "  pull " in result.output
