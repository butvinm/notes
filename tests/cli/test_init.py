"""Tests for `notes init`: the fresh vault, the remote and the initial push, the clone flow, the timer, the prompts,
and the refusals that leave nothing behind."""

import json
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner

from notes import config
from notes.cli import cli
from notes.cli import init as cli_init
from notes.systemd import SERVICE_NAME, TIMER_NAME
from notes.vault import is_vault
from tests.conftest import RecordedCommands, indexed_paths

Git = Callable[..., str]
KINDS = ("decision", "event", "fact", "idea", "promise", "reminder")
SYSTEMCTL = ("systemctl", "--user")
NOTE = "---\nkind: decision\nstatus: active\n---\n\n# From the first device\n"
NO_KIND = "---\nstatus: active\n---\n\n# No kind\n"
ENABLE_CALLS = [(*SYSTEMCTL, "daemon-reload"), (*SYSTEMCTL, "enable", "--now", TIMER_NAME)]


def subjects(git_cmd: Git, repo: Path) -> list[str]:
    return git_cmd(repo, "log", "--format=%s").splitlines()


def tracked(git_cmd: Git, repo: Path) -> list[str]:
    return git_cmd(repo, "ls-files").splitlines()


def head(git_cmd: Git, repo: Path) -> str:
    return git_cmd(repo, "rev-parse", "HEAD").strip()


def remote_head(git_cmd: Git, remote: Path) -> str | None:
    return git_cmd(remote, "for-each-ref", "--format=%(objectname)", "refs/heads/master").strip() or None


def upstream(git_cmd: Git, repo: Path) -> str:
    return git_cmd(repo, "rev-parse", "--abbrev-ref", "@{upstream}").strip()


def remote_names(git_cmd: Git, repo: Path) -> list[str]:
    return git_cmd(repo, "remote").split()


def remote_url(git_cmd: Git, repo: Path, name: str = "origin") -> str:
    return git_cmd(repo, "remote", "get-url", name).strip()


def reject_pushes(remote: Path) -> Path:
    """A pre-receive hook that refuses every push, so `ls-remote` succeeds while `push` fails."""
    hook = remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'pushes are refused here' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    return hook


@pytest.fixture
def no_display(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)


@pytest.fixture
def no_git_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """No author identity anywhere, so `git commit` fails."""
    for variable in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(variable)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")


@pytest.fixture
def foreign_remote(tmp_path: Path, remote: Path, git_cmd: Git) -> Path:
    """The bare remote holding a commit that is not a vault: a README and nothing else."""
    work = tmp_path / "foreign"
    work.mkdir()
    (work / "README.md").write_text("# Not a vault\n", encoding="utf-8")
    git_cmd(work, "init", "-q", "-b", "master")
    git_cmd(work, "add", "--", "README.md")
    git_cmd(work, "commit", "-q", "-m", "readme")
    git_cmd(work, "push", "-q", str(remote), "master")
    return remote


@pytest.fixture
def populated_remote(runner: CliRunner, home: Path, remote: Path, git_cmd: Git) -> Path:
    """The bare remote after a first device initialized a vault against it (without auto-push) and pushed one note;
    `~/.notes` is removed again, as on a second device."""
    assert runner.invoke(cli, ["init", "--remote", str(remote)]).exit_code == 0
    vault = home / ".notes"
    (vault / "notes" / "2026-09-02-first.md").write_text(NOTE, encoding="utf-8")
    assert runner.invoke(cli, ["push"]).exit_code == 0
    assert remote_head(git_cmd, remote) == head(git_cmd, vault)
    shutil.rmtree(vault)
    return remote


# A fresh vault


def test_init_creates_the_layout_the_initial_commit_and_the_index(runner: CliRunner, home: Path, git_cmd: Git) -> None:
    vault = home / ".notes"

    result = runner.invoke(cli, ["init"])

    assert result.exit_code == 0
    assert result.output == f"initialized vault at {vault}\n"
    assert result.stderr == ""
    assert is_vault(vault)
    for kind in KINDS:
        assert (vault / "types" / kind / "prompt.md").is_file()
        assert (vault / "types" / kind / "template.md").is_file()
    for name in ("prompt.md", "config.toml", ".gitignore", ".defaults-version", "index.sqlite"):
        assert (vault / name).is_file(), name
    assert (vault / "notes" / ".gitkeep").is_file()
    assert (vault / ".git").is_dir()
    assert subjects(git_cmd, vault) == ["notes: initialize vault"]
    assert git_cmd(vault, "symbolic-ref", "--short", "HEAD").strip() == "master"
    assert git_cmd(vault, "status", "--porcelain") == ""
    committed = tracked(git_cmd, vault)
    for path in ("notes/.gitkeep", "config.toml", ".gitignore", ".defaults-version", "prompt.md"):
        assert path in committed, path
    assert "types/decision/template.md" in committed
    assert "index.sqlite" not in committed
    assert remote_names(git_cmd, vault) == []
    assert indexed_paths(vault) == []
    assert config.load(vault).git.auto_push is False


def test_init_json(runner: CliRunner, home: Path) -> None:
    result = runner.invoke(cli, ["init", "--json"])

    assert result.exit_code == 0
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "vault": str(home / ".notes"),
        "cloned": False,
        "remote": None,
        "pushed": False,
        "notes_indexed": 0,
        "invalid_files": 0,
        "notifications_enabled": False,
        "warnings": [],
    }


def test_auto_push_is_recorded_in_config_and_pushes_nothing_without_a_remote(
    runner: CliRunner, home: Path, recorded_commands: RecordedCommands
) -> None:
    recorded_commands.passthrough("git")
    vault = home / ".notes"

    result = runner.invoke(cli, ["init", "--auto-push"])

    assert result.exit_code == 0
    assert result.output == f"initialized vault at {vault}\n"
    assert result.stderr == ""
    assert "auto_push = true" in (vault / "config.toml").read_text(encoding="utf-8")
    assert config.load(vault).git.auto_push is True
    assert recorded_commands.commands("git", "push") == []


def test_init_uses_an_existing_empty_directory(runner: CliRunner, home: Path) -> None:
    (home / ".notes").mkdir()

    result = runner.invoke(cli, ["init"])

    assert result.exit_code == 0
    assert is_vault(home / ".notes")


def test_existing_non_vault_directory_is_refused_and_left_untouched(runner: CliRunner, home: Path) -> None:
    directory = home / ".notes"
    directory.mkdir()
    (directory / "keep.txt").write_text("mine\n", encoding="utf-8")

    result = runner.invoke(cli, ["init"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert (
        result.stderr == f"Error: {directory} exists and is not a vault; move it away or empty it before `notes init`\n"
    )
    assert [path.name for path in directory.iterdir()] == ["keep.txt"]
    assert (directory / "keep.txt").read_text(encoding="utf-8") == "mine\n"


def test_existing_file_is_refused(runner: CliRunner, home: Path) -> None:
    path = home / ".notes"
    path.write_text("not a directory\n", encoding="utf-8")

    result = runner.invoke(cli, ["init"])

    assert result.exit_code == 1
    assert result.stderr == f"Error: {path} exists and is not a directory; move it away before `notes init`\n"
    assert path.read_text(encoding="utf-8") == "not a directory\n"


def test_existing_vault_is_refused(runner: CliRunner, vault: Path, git_cmd: Git) -> None:
    (vault / "notes" / "2026-09-02-mine.md").write_text(NOTE, encoding="utf-8")

    human = runner.invoke(cli, ["init"])
    machine = runner.invoke(cli, ["init", "--json", "--auto-push"])

    assert human.exit_code == 1
    assert human.stderr == f"Error: a vault already exists at {vault}\n"
    assert machine.exit_code == 1
    assert json.loads(machine.stderr) == {
        "error": {"type": "usage_error", "message": f"a vault already exists at {vault}"},
    }
    assert subjects(git_cmd, vault) == ["notes: initialize vault"]
    assert (vault / "notes" / "2026-09-02-mine.md").is_file()
    assert config.load(vault).git.auto_push is False


@pytest.mark.parametrize("pre_existing", [False, True])
def test_git_failure_removes_the_half_made_vault(
    runner: CliRunner, home: Path, no_git_identity: None, pre_existing: bool
) -> None:
    vault = home / ".notes"
    if pre_existing:
        vault.mkdir()

    result = runner.invoke(cli, ["init"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.startswith("Error: git commit failed: ")
    if pre_existing:
        assert vault.is_dir()
        assert list(vault.iterdir()) == []
    else:
        assert not vault.exists()


# Remotes


def test_remote_without_auto_push_adds_the_remote_and_pushes_nothing(
    runner: CliRunner, home: Path, remote: Path, git_cmd: Git, recorded_commands: RecordedCommands
) -> None:
    recorded_commands.passthrough("git")
    vault = home / ".notes"

    result = runner.invoke(cli, ["init", "--remote", str(remote)])

    assert result.exit_code == 0
    assert result.output == f"initialized vault at {vault}\nremote origin: {remote}\n"
    assert result.stderr == ""
    assert remote_url(git_cmd, vault) == str(remote)
    assert remote_head(git_cmd, remote) is None
    assert recorded_commands.commands("git", "push") == []
    assert config.load(vault).git.auto_push is False

    pushed = runner.invoke(cli, ["push"])

    assert pushed.exit_code == 0
    assert pushed.output == "pushed to origin and set it as the upstream\n"
    assert remote_head(git_cmd, remote) == head(git_cmd, vault)


def test_remote_with_auto_push_pushes_the_initial_commit_and_sets_the_upstream(
    runner: CliRunner, home: Path, remote: Path, git_cmd: Git
) -> None:
    vault = home / ".notes"

    result = runner.invoke(cli, ["init", "--remote", str(remote), "--auto-push"])

    assert result.exit_code == 0
    assert result.output == (
        f"initialized vault at {vault}\nremote origin: {remote}\npushed to origin and set it as the upstream\n"
    )
    assert result.stderr == ""
    assert upstream(git_cmd, vault) == "origin/master"
    assert remote_head(git_cmd, remote) == head(git_cmd, vault)
    assert config.load(vault).git.auto_push is True


def test_remote_with_auto_push_json(runner: CliRunner, home: Path, remote: Path) -> None:
    result = runner.invoke(cli, ["init", "--remote", str(remote), "--auto-push", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert (data["cloned"], data["remote"], data["pushed"], data["warnings"]) == (False, str(remote), True, [])


def test_unreachable_push_is_a_warning_and_a_later_command_retries(
    runner: CliRunner, home: Path, remote: Path, git_cmd: Git
) -> None:
    hook = reject_pushes(remote)
    vault = home / ".notes"

    first = runner.invoke(cli, ["init", "--remote", str(remote), "--auto-push"])

    assert first.exit_code == 0
    assert first.stdout == f"initialized vault at {vault}\nremote origin: {remote}\n"
    assert first.stderr.startswith("warning: push to origin failed: ")
    assert first.stderr.endswith("; the vault is complete and the next command retries the push\n")
    assert is_vault(vault)
    assert (vault / "index.sqlite").is_file()
    assert subjects(git_cmd, vault) == ["notes: initialize vault"]
    assert remote_url(git_cmd, vault) == str(remote)
    assert config.load(vault).git.auto_push is True
    assert remote_head(git_cmd, remote) is None

    hook.unlink()
    second = runner.invoke(cli, ["sync"])

    assert second.exit_code == 0
    assert second.stderr == ""
    assert upstream(git_cmd, vault) == "origin/master"
    assert remote_head(git_cmd, remote) == head(git_cmd, vault)


def test_unreachable_push_warning_in_json(runner: CliRunner, home: Path, remote: Path) -> None:
    reject_pushes(remote)

    result = runner.invoke(cli, ["init", "--remote", str(remote), "--auto-push", "--json"])

    assert result.exit_code == 0
    assert result.stderr == ""
    data = json.loads(result.stdout)
    assert data["pushed"] is False
    assert len(data["warnings"]) == 1
    assert data["warnings"][0].startswith("push to origin failed: ")


def test_remote_that_cannot_be_inspected_aborts_before_creating_anything(
    runner: CliRunner, home: Path, tmp_path: Path
) -> None:
    missing = tmp_path / "missing.git"

    result = runner.invoke(cli, ["init", "--remote", str(missing)])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.startswith(f"Error: cannot inspect remote {missing}: ")
    assert not (home / ".notes").exists()


# Clones


def test_populated_remote_is_cloned_and_indexed(
    runner: CliRunner, home: Path, populated_remote: Path, git_cmd: Git
) -> None:
    vault = home / ".notes"

    result = runner.invoke(cli, ["init", "--remote", str(populated_remote)])

    assert result.exit_code == 0
    assert result.output == f"cloned vault from {populated_remote} into {vault}\nindexed 1 note\n"
    assert result.stderr == ""
    assert is_vault(vault)
    assert (vault / "notes" / ".gitkeep").is_file()
    assert (vault / "notes" / "2026-09-02-first.md").read_text(encoding="utf-8") == NOTE
    assert indexed_paths(vault) == ["notes/2026-09-02-first.md"]
    assert remote_url(git_cmd, vault) == str(populated_remote)
    assert upstream(git_cmd, vault) == "origin/master"
    assert head(git_cmd, vault) == remote_head(git_cmd, populated_remote)
    assert config.load(vault).git.auto_push is False

    synced = runner.invoke(cli, ["sync"])

    assert synced.exit_code == 0
    assert synced.output == "0 changed, 0 removed, 1 unchanged, 0 invalid\n"
    assert head(git_cmd, vault) == remote_head(git_cmd, populated_remote)


def test_clone_json(runner: CliRunner, home: Path, populated_remote: Path) -> None:
    result = runner.invoke(cli, ["init", "--remote", str(populated_remote), "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "vault": str(home / ".notes"),
        "cloned": True,
        "remote": str(populated_remote),
        "pushed": False,
        "notes_indexed": 1,
        "invalid_files": 0,
        "notifications_enabled": False,
        "warnings": [],
    }


def test_clone_keeps_the_remote_config_and_warns_about_a_conflicting_auto_push_flag(
    runner: CliRunner, home: Path, populated_remote: Path
) -> None:
    vault = home / ".notes"

    result = runner.invoke(cli, ["init", "--remote", str(populated_remote), "--auto-push"])

    assert result.exit_code == 0
    assert result.stderr == (
        "warning: config.toml came with the clone and keeps auto_push = false; edit it to turn auto-push on\n"
    )
    assert config.load(vault).git.auto_push is False


def test_clone_reports_invalid_files_as_a_warning(
    runner: CliRunner, home: Path, populated_remote: Path, git_cmd: Git, tmp_path: Path
) -> None:
    other = tmp_path / "other"
    git_cmd(tmp_path, "clone", "-q", str(populated_remote), str(other))
    (other / "notes" / "2026-09-02-broken.md").write_text(NO_KIND, encoding="utf-8")
    git_cmd(other, "add", "-A", "--", "notes")
    git_cmd(other, "commit", "-q", "-m", "a broken note")
    git_cmd(other, "push", "-q")

    result = runner.invoke(cli, ["init", "--remote", str(populated_remote)])

    assert result.exit_code == 0
    assert result.stdout.endswith("indexed 1 note\n")
    assert result.stderr == "warning: 1 invalid file in the cloned vault, `notes check` lists them\n"
    assert indexed_paths(home / ".notes") == ["notes/2026-09-02-first.md"]


@pytest.mark.parametrize("pre_existing", [False, True])
def test_populated_remote_that_is_not_a_vault_is_rejected_and_removed(
    runner: CliRunner, home: Path, foreign_remote: Path, pre_existing: bool
) -> None:
    vault = home / ".notes"
    if pre_existing:
        vault.mkdir()

    result = runner.invoke(cli, ["init", "--remote", str(foreign_remote)])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == (
        f"Error: {foreign_remote} does not hold a vault (no config.toml with a notes/ directory); "
        "the clone was removed again\n"
    )
    if pre_existing:
        assert vault.is_dir()
        assert list(vault.iterdir()) == []
    else:
        assert not vault.exists()


# Notifications


def test_enable_notifications_installs_the_timer(
    runner: CliRunner, home: Path, recorded_commands: RecordedCommands, no_display: None
) -> None:
    recorded_commands.passthrough("git")
    vault = home / ".notes"

    result = runner.invoke(cli, ["init", "--enable-notifications"])

    assert result.exit_code == 0
    assert result.output == (
        f"initialized vault at {vault}\nenabled {TIMER_NAME}: `{sys.executable} -m notes tick` runs every minute\n"
    )
    assert result.stderr == ""
    assert recorded_commands.commands("systemctl") == ENABLE_CALLS
    units = home / ".config" / "systemd" / "user"
    assert (units / SERVICE_NAME).is_file()
    assert (units / TIMER_NAME).is_file()


def test_enable_notifications_json(
    runner: CliRunner, home: Path, recorded_commands: RecordedCommands, no_display: None
) -> None:
    recorded_commands.passthrough("git")

    result = runner.invoke(cli, ["init", "--enable-notifications", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert (data["notifications_enabled"], data["warnings"]) == (True, [])
    assert recorded_commands.commands("systemctl") == ENABLE_CALLS


def test_refused_timer_is_a_warning_and_the_vault_is_complete(
    runner: CliRunner, home: Path, recorded_commands: RecordedCommands, no_display: None
) -> None:
    recorded_commands.passthrough("git")
    recorded_commands.missing("systemctl")
    vault = home / ".notes"

    result = runner.invoke(cli, ["init", "--enable-notifications"])

    assert result.exit_code == 0
    assert result.stdout == f"initialized vault at {vault}\n"
    assert result.stderr == (
        "warning: systemctl is not installed or not on PATH; without systemd, run `notes tick` from cron every "
        "minute; the vault is complete, run `notes notifications enable` later\n"
    )
    assert is_vault(vault)
    assert (vault / "index.sqlite").is_file()


# Prompts


def test_non_interactive_run_skips_the_prompts(
    runner: CliRunner, home: Path, git_cmd: Git, recorded_commands: RecordedCommands
) -> None:
    recorded_commands.passthrough("git")
    vault = home / ".notes"

    result = runner.invoke(cli, ["init"], input="git@example.com:me/notes.git\ny\n")

    assert result.exit_code == 0
    assert result.output == f"initialized vault at {vault}\n"
    assert remote_names(git_cmd, vault) == []
    assert recorded_commands.commands("systemctl") == []


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `notes init` believe it runs in a terminal, so the prompts are shown and read from the runner's input."""
    monkeypatch.setattr(cli_init, "interactive", lambda: True)


def test_interactive_run_asks_for_the_remote_and_offers_the_timer(
    runner: CliRunner,
    home: Path,
    remote: Path,
    git_cmd: Git,
    recorded_commands: RecordedCommands,
    terminal: None,
    no_display: None,
) -> None:
    recorded_commands.passthrough("git")
    vault = home / ".notes"

    result = runner.invoke(cli, ["init"], input=f"{remote}\ny\n")

    assert result.exit_code == 0
    assert "Git remote URL (leave empty for a local-only vault): " in result.output
    assert "Enable desktop notifications through a systemd user timer? [y/N]: " in result.output
    assert result.output.endswith(
        f"remote origin: {remote}\nenabled {TIMER_NAME}: `{sys.executable} -m notes tick` runs every minute\n"
    )
    assert remote_url(git_cmd, vault) == str(remote)
    assert recorded_commands.commands("systemctl") == ENABLE_CALLS


def test_interactive_run_accepts_empty_answers(
    runner: CliRunner, home: Path, git_cmd: Git, recorded_commands: RecordedCommands, terminal: None
) -> None:
    recorded_commands.passthrough("git")
    vault = home / ".notes"

    result = runner.invoke(cli, ["init"], input="\n\n")

    assert result.exit_code == 0
    assert result.output.endswith(f"initialized vault at {vault}\n")
    assert remote_names(git_cmd, vault) == []
    assert recorded_commands.commands("systemctl") == []


def test_interactive_run_asks_only_for_what_the_options_left_open(
    runner: CliRunner, home: Path, remote: Path, recorded_commands: RecordedCommands, terminal: None
) -> None:
    recorded_commands.passthrough("git")

    result = runner.invoke(cli, ["init", "--remote", str(remote)], input="\n")

    assert result.exit_code == 0
    assert "Git remote URL" not in result.output
    assert "Enable desktop notifications" in result.output
    assert recorded_commands.commands("systemctl") == []


def test_json_never_prompts(runner: CliRunner, home: Path, terminal: None) -> None:
    result = runner.invoke(cli, ["init", "--json"], input="")

    assert result.exit_code == 0
    assert "Git remote URL" not in result.output
    assert json.loads(result.stdout)["cloned"] is False


def test_interactive_run_refuses_an_existing_vault_before_prompting(
    runner: CliRunner, vault: Path, terminal: None
) -> None:
    result = runner.invoke(cli, ["init"], input="git@example.com:me/notes.git\ny\n")

    assert result.exit_code == 1
    assert "Git remote URL" not in result.output
    assert result.stderr == f"Error: a vault already exists at {vault}\n"


# Help


def test_init_help_never_touches_the_home_directory(runner: CliRunner, home: Path) -> None:
    result = runner.invoke(cli, ["init", "--help"])

    assert result.exit_code == 0
    for option in ("--remote", "--auto-push", "--enable-notifications", "--json"):
        assert option in result.output
    assert not (home / ".notes").exists()


def test_init_is_listed_in_root_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])

    assert "  init " in result.output
