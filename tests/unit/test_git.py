"""Tests for the Git layer: the auto-commit step after a sync, auto-push, the low-level operations, and the retry."""

import shutil
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pytest

from notes import db, git, proc, sync
from notes.config import Config, GitConfig
from notes.errors import GitError
from notes.git import CommitOutcome, StagedChange, commit_message, move_message
from notes.proc import CommandNotFound, CommandResult, CommandTimeout
from notes.sync import SyncReport

Git = Callable[..., str]
NO_KIND = "---\nstatus: active\n---\n\n# No kind\n"


def note_text(title: str = "A title") -> str:
    return f"---\nkind: decision\nstatus: active\n---\n\n# {title}\n"


def write(vault: Path, name: str, text: str = note_text()) -> str:
    """Write `<vault>/notes/<name>`; a clone of an empty vault has no `notes/` yet, Git tracks no empty directory."""
    target = vault / "notes" / name
    target.parent.mkdir(exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return f"notes/{name}"


def run_sync(vault: Path) -> SyncReport:
    conn = db.open_index(vault)
    try:
        return sync.run(vault, conn)
    finally:
        conn.close()


def sync_and_commit(
    vault: Path, config: Config | None = None, *, allow_push: bool = True, message: str | None = None
) -> CommitOutcome:
    return git.commit_after_sync(vault, run_sync(vault), config or Config(), allow_push=allow_push, message=message)


def with_auto_push(remote: str = "origin") -> Config:
    return Config(git=GitConfig(auto_push=True, remote=remote))


def subjects(git_cmd: Git, repo: Path) -> list[str]:
    return git_cmd(repo, "log", "--format=%s").splitlines()


def tracked(git_cmd: Git, repo: Path) -> list[str]:
    return git_cmd(repo, "ls-files").splitlines()


def status(git_cmd: Git, repo: Path) -> str:
    return git_cmd(repo, "status", "--porcelain", "-uall")


def remote_head(git_cmd: Git, remote: Path) -> str | None:
    """The commit `master` points at in the remote, or `None` while the remote is empty."""
    return git_cmd(remote, "for-each-ref", "--format=%(objectname)", "refs/heads/master").strip() or None


def clone_of(git_cmd: Git, remote: Path, dest: Path) -> Path:
    git_cmd(dest.parent, "clone", "-q", str(remote), str(dest))
    return dest


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


# Commit messages and the staged set


def test_commit_message_rules() -> None:
    assert commit_message([StagedChange("A", "notes/2026-09-02-a.md")]) == "notes: update notes/2026-09-02-a.md"
    assert commit_message([StagedChange("M", "config.toml")]) == "notes: update config.toml"
    assert commit_message([StagedChange("D", "notes/2026-09-02-a.md")]) == "notes: remove notes/2026-09-02-a.md"
    assert commit_message([StagedChange("A", "notes/a.md"), StagedChange("D", "notes/b.md")]) == "notes: sync 2 files"
    assert move_message("notes/a.md", "notes/b.md") == "notes: move notes/a.md -> notes/b.md"


def test_changed_versioned_files_lists_every_kind_of_change(vault: Path, git_cmd: Git) -> None:
    modified = write(vault, "2026-09-02-modified.md")
    deleted = write(vault, "2026-09-02-deleted.md")
    moved = write(vault, "2026-09-02-moved.md")
    git_cmd(vault, "add", "-A", "--", "notes")
    git_cmd(vault, "commit", "-q", "-m", "three notes")
    (vault / modified).write_text(note_text("Changed"), encoding="utf-8")
    (vault / deleted).unlink()
    git_cmd(vault, "mv", "--", moved, "notes/2026-09-02-target.md")
    untracked = write(vault, "2026-09-02-новая-заметка.md")
    (vault / "index.sqlite").write_bytes(b"ignored")

    assert sorted(git.changed_versioned_files(vault)) == sorted(
        [modified, deleted, "notes/2026-09-02-target.md", untracked]
    )
    assert sorted(git.status_entries(vault), key=lambda entry: entry.path) == [
        git.StatusEntry(" ", "D", deleted),
        git.StatusEntry(" ", "M", modified),
        git.StatusEntry("R", " ", "notes/2026-09-02-target.md", moved),
        git.StatusEntry("?", "?", untracked),
    ]
    assert {entry.path for entry in git.status_entries(vault) if entry.staged} == {"notes/2026-09-02-target.md"}


def test_staged_changes_reports_the_status_letters(vault: Path, git_cmd: Git) -> None:
    kept = write(vault, "2026-09-02-kept.md", note_text("Kept"))
    gone = write(vault, "2026-09-02-gone.md", note_text("Gone"))
    git_cmd(vault, "add", "-A", "--", "notes")
    git_cmd(vault, "commit", "-q", "-m", "two notes")
    (vault / kept).write_text(note_text("Changed"), encoding="utf-8")
    (vault / gone).unlink()
    added = write(vault, "2026-09-02-added.md", note_text("Added"))
    git_cmd(vault, "add", "-A", "--", "notes")

    assert git.staged_changes(vault) == [StagedChange("A", added), StagedChange("D", gone), StagedChange("M", kept)]
    assert git.has_staged_changes(vault)


def test_staged_changes_reports_a_rename_as_a_deletion_and_an_addition(vault: Path, git_cmd: Git) -> None:
    old = write(vault, "2026-09-02-old.md")
    git_cmd(vault, "add", "-A", "--", "notes")
    git_cmd(vault, "commit", "-q", "-m", "one note")
    git_cmd(vault, "mv", "--", old, "notes/2026-09-02-new.md")

    assert git.staged_changes(vault) == [StagedChange("A", "notes/2026-09-02-new.md"), StagedChange("D", old)]


def test_similar_deleted_and_added_notes_are_not_reported_as_a_move(vault: Path, git_cmd: Git) -> None:
    gone = write(vault, "2026-09-02-gone.md")
    sync_and_commit(vault)
    (vault / gone).unlink()
    added = write(vault, "2026-09-02-added.md")

    assert sync_and_commit(vault).message == "notes: sync 2 files"
    assert subjects(git_cmd, vault)[0] == "notes: sync 2 files"
    assert added in tracked(git_cmd, vault)
    assert gone not in tracked(git_cmd, vault)


# The auto-commit step


def test_single_new_note_is_committed_with_the_update_message(vault: Path, git_cmd: Git) -> None:
    path = write(vault, "2026-09-02-a.md")

    outcome = sync_and_commit(vault)

    assert outcome == CommitOutcome(f"notes: update {path}", False)
    assert subjects(git_cmd, vault)[0] == f"notes: update {path}"
    assert status(git_cmd, vault) == ""


def test_note_indexed_by_an_earlier_index_only_sync_is_still_committed(vault: Path, git_cmd: Git) -> None:
    # `notes tick` (the minutely timer) and `notes read` index without committing; the next command must not treat
    # the note they indexed as unchanged and leave it out of the commit forever.
    path = write(vault, "2026-09-02-a.md")
    gone = write(vault, "2026-09-02-gone.md")
    sync_and_commit(vault)
    (vault / gone).unlink()
    (vault / path).write_text(note_text("Edited by hand"), encoding="utf-8")
    report = run_sync(vault)
    assert (report.changed, report.removed) == ((path,), (gone,))

    outcome = sync_and_commit(vault)

    assert outcome.message == "notes: sync 2 files"
    assert status(git_cmd, vault) == ""
    assert path in tracked(git_cmd, vault)
    assert gone not in tracked(git_cmd, vault)


def test_several_files_are_committed_with_the_sync_message(vault: Path, git_cmd: Git) -> None:
    write(vault, "2026-09-02-a.md")
    write(vault, "2026-09-02-b.md")
    write(vault, "2026-09-02-c.md")

    outcome = sync_and_commit(vault)

    assert outcome.message == "notes: sync 3 files"
    assert subjects(git_cmd, vault)[0] == "notes: sync 3 files"


def test_removal_is_committed_with_the_remove_message(vault: Path, git_cmd: Git) -> None:
    path = write(vault, "2026-09-02-a.md")
    sync_and_commit(vault)
    (vault / path).unlink()

    outcome = sync_and_commit(vault)

    assert outcome.message == f"notes: remove {path}"
    assert subjects(git_cmd, vault)[0] == f"notes: remove {path}"
    assert path not in tracked(git_cmd, vault)


def test_invalid_file_is_left_unstaged_while_its_valid_sibling_is_committed(vault: Path, git_cmd: Git) -> None:
    good = write(vault, "2026-09-02-good.md")
    bad = write(vault, "2026-09-02-bad.md", NO_KIND)

    outcome = sync_and_commit(vault)

    assert outcome.message == f"notes: update {good}"
    assert good in tracked(git_cmd, vault)
    assert bad not in tracked(git_cmd, vault)
    assert status(git_cmd, vault) == f"?? {bad}\n"


def test_invalid_edit_of_a_committed_note_stays_uncommitted_until_fixed(vault: Path, git_cmd: Git) -> None:
    path = write(vault, "2026-09-02-a.md")
    sync_and_commit(vault)
    (vault / path).write_text(NO_KIND, encoding="utf-8")

    assert sync_and_commit(vault).message is None
    assert status(git_cmd, vault) == f" M {path}\n"

    (vault / path).write_text(note_text("Fixed"), encoding="utf-8")
    assert sync_and_commit(vault).message == f"notes: update {path}"
    assert status(git_cmd, vault) == ""


def test_invalid_file_that_reached_the_index_is_unstaged_before_the_commit(vault: Path, git_cmd: Git) -> None:
    good = write(vault, "2026-09-02-good.md")
    bad = write(vault, "2026-09-02-bad.md", NO_KIND)
    git_cmd(vault, "add", "-A", "--", "notes")
    assert status(git_cmd, vault) == f"A  {bad}\nA  {good}\n"

    outcome = sync_and_commit(vault)

    assert outcome.message == f"notes: update {good}"
    assert status(git_cmd, vault) == f"?? {bad}\n"
    assert good in tracked(git_cmd, vault)
    assert bad not in tracked(git_cmd, vault)


def test_moved_file_that_is_invalid_keeps_the_note_at_head(vault: Path, git_cmd: Git) -> None:
    """The staged rename is unstaged whole: an invalid move commits neither the arrival nor the departure."""
    old = write(vault, "2026-09-02-old.md")
    new = "notes/2026-09-02-new.md"
    sync_and_commit(vault)
    (vault / old).write_text(NO_KIND, encoding="utf-8")
    git.mv(vault, old, new)

    outcome = sync_and_commit(vault, message=move_message(old, new))

    assert outcome.message is None
    assert status(git_cmd, vault) == f" D {old}\n?? {new}\n"
    assert [path for path in tracked(git_cmd, vault) if path.startswith("notes/")] == ["notes/.gitkeep", old]

    (vault / new).write_text(note_text("Fixed"), encoding="utf-8")

    assert sync_and_commit(vault, message=move_message(old, new)).message == f"notes: move {old} -> {new}"
    assert status(git_cmd, vault) == ""
    assert [path for path in tracked(git_cmd, vault) if path.startswith("notes/")] == ["notes/.gitkeep", new]


def test_deletion_staged_by_hand_is_committed(vault: Path, git_cmd: Git) -> None:
    gone = write(vault, "2026-09-02-gone.md")
    sync_and_commit(vault)
    git_cmd(vault, "rm", "-q", "--", gone)

    outcome = sync_and_commit(vault)

    assert outcome.message == f"notes: remove {gone}"
    assert status(git_cmd, vault) == ""
    assert gone not in tracked(git_cmd, vault)


def test_nothing_to_commit_makes_no_commit(vault: Path, git_cmd: Git) -> None:
    before = git.head(vault)

    outcome = sync_and_commit(vault)

    assert outcome == CommitOutcome(None, False)
    assert git.head(vault) == before


def test_config_and_types_changes_are_committed(vault: Path, git_cmd: Git) -> None:
    (vault / "config.toml").write_text("[git]\nauto_push = false\n", encoding="utf-8")
    (vault / "types" / "custom").mkdir()
    (vault / "types" / "custom" / "template.md").write_text("---\nkind: custom\n---\n# {title}\n", encoding="utf-8")
    (vault / "prompt.md").write_text("Shared prompt\n", encoding="utf-8")

    outcome = sync_and_commit(vault)

    assert outcome.message == "notes: sync 3 files"
    assert {"config.toml", "types/custom/template.md", "prompt.md"} <= set(tracked(git_cmd, vault))


def test_files_outside_the_versioned_set_are_never_staged(vault: Path, git_cmd: Git) -> None:
    (vault / "scratch.txt").write_text("not versioned by the CLI\n", encoding="utf-8")
    (vault / "notes" / "diagram.png").write_bytes(b"\x89PNG")

    outcome = sync_and_commit(vault)

    assert outcome.message is None
    assert status(git_cmd, vault) == "?? notes/diagram.png\n?? scratch.txt\n"


def test_explicit_message_overrides_the_derived_one(vault: Path, git_cmd: Git) -> None:
    write(vault, "2026-09-02-a.md")

    outcome = sync_and_commit(vault, message=move_message("notes/old.md", "notes/2026-09-02-a.md"))

    assert outcome.message == "notes: move notes/old.md -> notes/2026-09-02-a.md"
    assert subjects(git_cmd, vault)[0] == outcome.message


def test_vault_without_a_repository_is_left_alone(vault: Path, git_calls: list[tuple[str, ...]]) -> None:
    shutil.rmtree(vault / ".git")
    write(vault, "2026-09-02-a.md")

    outcome = sync_and_commit(vault, with_auto_push())

    assert outcome == CommitOutcome(None, False)
    assert git_calls == []


def test_commit_failure_is_a_warning_and_leaves_the_files_staged(
    vault: Path, git_cmd: Git, monkeypatch: pytest.MonkeyPatch
) -> None:
    for variable in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(variable)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    path = write(vault, "2026-09-02-a.md")

    outcome = sync_and_commit(vault)

    assert outcome.message is None
    assert len(outcome.warnings) == 1
    assert outcome.warnings[0].startswith("git commit failed: ")
    assert outcome.warnings[0].endswith("; the change stays uncommitted")
    assert status(git_cmd, vault) == f"A  {path}\n"


# Auto-push


def test_auto_push_disabled_leaves_commits_unpushed(
    vault: Path, remote: Path, git_cmd: Git, git_calls: list[tuple[str, ...]]
) -> None:
    git_cmd(vault, "remote", "add", "origin", str(remote))
    write(vault, "2026-09-02-a.md")

    outcome = sync_and_commit(vault)

    assert outcome.pushed is False
    assert pushes(git_calls) == []
    assert remote_head(git_cmd, remote) is None


def test_auto_push_without_a_configured_remote_attempts_nothing(vault: Path, git_calls: list[tuple[str, ...]]) -> None:
    write(vault, "2026-09-02-a.md")

    outcome = sync_and_commit(vault, with_auto_push())

    assert outcome.pushed is False
    assert outcome.warnings == ()
    assert pushes(git_calls) == []


def test_auto_push_sets_the_upstream_first_and_pushes_plainly_afterwards(
    vault: Path, remote: Path, git_cmd: Git, git_calls: list[tuple[str, ...]]
) -> None:
    git_cmd(vault, "remote", "add", "origin", str(remote))
    write(vault, "2026-09-02-a.md")

    first = sync_and_commit(vault, with_auto_push())

    assert first.pushed is True
    assert first.warnings == ()
    assert pushes(git_calls) == [("git", "push", "-q", "--set-upstream", "origin", "master")]
    assert git_cmd(vault, "rev-parse", "--abbrev-ref", "@{upstream}").strip() == "origin/master"
    assert remote_head(git_cmd, remote) == git.head(vault)

    write(vault, "2026-09-02-b.md")
    second = sync_and_commit(vault, with_auto_push())

    assert second.pushed is True
    assert pushes(git_calls)[1:] == [("git", "push", "-q", "origin", "master")]
    assert remote_head(git_cmd, remote) == git.head(vault)


def test_auto_push_with_nothing_to_push_skips_the_network(
    vault: Path, remote: Path, git_cmd: Git, git_calls: list[tuple[str, ...]]
) -> None:
    git_cmd(vault, "remote", "add", "origin", str(remote))
    git_cmd(vault, "push", "-q", "--set-upstream", "origin", "master")

    outcome = sync_and_commit(vault, with_auto_push())

    assert outcome == CommitOutcome(None, False)
    assert pushes(git_calls) == []


def test_auto_push_honours_the_configured_remote_name(vault: Path, remote: Path, git_cmd: Git) -> None:
    git_cmd(vault, "remote", "add", "backup", str(remote))
    write(vault, "2026-09-02-a.md")

    outcome = sync_and_commit(vault, with_auto_push(remote="backup"))

    assert outcome.pushed is True
    assert git_cmd(vault, "rev-parse", "--abbrev-ref", "@{upstream}").strip() == "backup/master"


def test_offline_step_commits_but_never_pushes(
    vault: Path, remote: Path, git_cmd: Git, git_calls: list[tuple[str, ...]]
) -> None:
    git_cmd(vault, "remote", "add", "origin", str(remote))
    path = write(vault, "2026-09-02-a.md")

    outcome = sync_and_commit(vault, with_auto_push(), allow_push=False)

    assert outcome == CommitOutcome(f"notes: update {path}", False)
    assert pushes(git_calls) == []
    assert remote_head(git_cmd, remote) is None


def test_unreachable_remote_is_a_warning_and_a_later_sync_retries(vault: Path, git_cmd: Git, tmp_path: Path) -> None:
    later = tmp_path / "later.git"
    git_cmd(vault, "remote", "add", "origin", str(later))
    path = write(vault, "2026-09-02-a.md")

    first = sync_and_commit(vault, with_auto_push())

    assert first.message == f"notes: update {path}"
    assert first.pushed is False
    assert len(first.warnings) == 1
    assert first.warnings[0].startswith("push to origin failed: ")
    assert first.warnings[0].endswith("; the commit is kept and the next command retries the push")
    assert subjects(git_cmd, vault)[0] == f"notes: update {path}"

    git_cmd(tmp_path, "init", "-q", "--bare", "-b", "master", str(later))
    second = sync_and_commit(vault, with_auto_push())

    assert second == CommitOutcome(None, True)
    assert remote_head(git_cmd, later) == git.head(vault)


# Failure modes of the seam: timeouts, locks, a missing binary


class ScriptedRunner:
    """A `proc.runner` that answers every call from a script and records what was asked."""

    def __init__(self, script: Callable[[tuple[str, ...]], CommandResult]) -> None:
        self.script = script
        self.calls: list[tuple[str, ...]] = []

    def __call__(
        self,
        args: Sequence[str | Path],
        *,
        cwd: Path | None = None,
        input: str | None = None,
        timeout: float | None = None,
        capture: bool = True,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        command = tuple(str(arg) for arg in args)
        self.calls.append(command)
        return self.script(command)


def ok(command: tuple[str, ...], stdout: str = "") -> CommandResult:
    return CommandResult(command, 0, stdout, "")


def test_push_timeout_is_a_warning(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def script(command: tuple[str, ...]) -> CommandResult:
        if command[1] == "push":
            raise CommandTimeout(command, 15)
        if command[1] == "rev-list":
            return ok(command, "1\n")
        return ok(command)

    monkeypatch.setattr(proc, "runner", ScriptedRunner(script))

    outcome = git.commit_after_sync(vault, sync.EMPTY_REPORT, with_auto_push())

    assert outcome.pushed is False
    assert outcome.warnings == (
        "git push timed out after 15 seconds; the commit is kept and the next command retries the push",
    )


def test_index_lock_collision_is_retried_once(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []

    def script(command: tuple[str, ...]) -> CommandResult:
        attempts.append(1)
        if len(attempts) == 1:
            return CommandResult(command, 128, "", "fatal: Unable to create '.git/index.lock': File exists.\n")
        return ok(command)

    runner = ScriptedRunner(script)
    monkeypatch.setattr(proc, "runner", runner)
    monkeypatch.setattr(git, "LOCK_RETRY_DELAY", 0)

    git.stage(vault, ["notes/2026-09-02-a.md"])

    assert runner.calls == [("git", "add", "-A", "--", "notes/2026-09-02-a.md")] * 2


def test_persistent_index_lock_fails_after_the_single_retry(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def script(command: tuple[str, ...]) -> CommandResult:
        return CommandResult(command, 128, "", "fatal: Unable to create '.git/index.lock': File exists.\n")

    runner = ScriptedRunner(script)
    monkeypatch.setattr(proc, "runner", runner)
    monkeypatch.setattr(git, "LOCK_RETRY_DELAY", 0)

    with pytest.raises(GitError, match="git commit failed: fatal: Unable to create '.git/index.lock'"):
        git.commit(vault, "notes: update x")

    assert len(runner.calls) == 2


def test_missing_git_binary_is_a_git_error_and_a_warning(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def script(command: tuple[str, ...]) -> CommandResult:
        raise CommandNotFound(command)

    monkeypatch.setattr(proc, "runner", ScriptedRunner(script))

    with pytest.raises(GitError, match="git is not installed or not on PATH"):
        git.changed_versioned_files(vault)

    outcome = git.commit_after_sync(vault, sync.EMPTY_REPORT, Config())
    assert outcome.warnings == ("git is not installed or not on PATH; the change stays uncommitted",)


def test_error_messages_carry_the_tail_of_stderr_without_hints(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def script(command: tuple[str, ...]) -> CommandResult:
        stderr = "hint: try harder\nerror: first line\nfatal: second line\n\n"
        return CommandResult(command, 1, "", stderr)

    monkeypatch.setattr(proc, "runner", ScriptedRunner(script))

    with pytest.raises(GitError, match="^git add failed: error: first line fatal: second line$"):
        git.stage(vault, ["x"])


def test_silent_failure_reports_the_exit_status(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proc, "runner", ScriptedRunner(lambda command: CommandResult(command, 3)))

    with pytest.raises(GitError, match="^git commit failed: git exited with status 3$"):
        git.commit(vault, "x")


# Low-level operations against real repositories


def test_init_repo_creates_a_repository_on_the_given_branch(tmp_path: Path, git_cmd: Git) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    git.init_repo(repo)

    assert git.is_repo(repo)
    assert git.current_branch(repo) == "master"
    assert git.head(repo) is None


def test_add_remote_and_remote_exists(vault: Path, remote: Path) -> None:
    assert git.remote_exists(vault, "origin") is False

    git.add_remote(vault, "origin", str(remote))

    assert git.remote_exists(vault, "origin") is True
    with pytest.raises(GitError, match="adding remote origin failed"):
        git.add_remote(vault, "origin", str(remote))


def test_ls_remote_lists_refs_and_fails_for_an_unreachable_url(
    vault: Path, remote: Path, git_cmd: Git, tmp_path: Path
) -> None:
    assert git.ls_remote(str(remote)) == []

    git_cmd(vault, "remote", "add", "origin", str(remote))
    git_cmd(vault, "push", "-q", "origin", "master")
    assert git.ls_remote(str(remote)) == ["HEAD", "refs/heads/master"]

    with pytest.raises(GitError, match="cannot inspect remote"):
        git.ls_remote(str(tmp_path / "missing.git"))


def test_clone_copies_the_remote(vault: Path, remote: Path, git_cmd: Git, tmp_path: Path) -> None:
    git_cmd(vault, "remote", "add", "origin", str(remote))
    git_cmd(vault, "push", "-q", "origin", "master")
    dest = tmp_path / "second"

    git.clone(str(remote), dest)

    assert (dest / "config.toml").is_file()
    assert git.head(dest) == git.head(vault)
    assert git.has_upstream(dest)
    with pytest.raises(GitError, match="cloning .* failed"):
        git.clone(str(tmp_path / "missing.git"), tmp_path / "third")


def test_mv_renames_a_tracked_file(vault: Path, git_cmd: Git) -> None:
    old = write(vault, "2026-09-02-old.md")
    git_cmd(vault, "add", "-A", "--", "notes")
    git_cmd(vault, "commit", "-q", "-m", "one note")

    git.mv(vault, old, "notes/2026-09-02-new.md")

    assert not (vault / old).exists()
    assert (vault / "notes" / "2026-09-02-new.md").is_file()
    with pytest.raises(GitError, match="git mv notes/missing.md -> notes/x.md failed"):
        git.mv(vault, "notes/missing.md", "notes/x.md")


def test_is_tracked_and_unstage(vault: Path, git_cmd: Git) -> None:
    committed = write(vault, "2026-09-02-committed.md")
    git_cmd(vault, "add", "-A", "--", "notes")
    git_cmd(vault, "commit", "-q", "-m", "one note")
    added = write(vault, "2026-09-02-added.md")
    untracked = write(vault, "2026-09-02-untracked.md")
    (vault / committed).write_text(note_text("Changed"), encoding="utf-8")
    git_cmd(vault, "add", "-A", "--", committed, added)

    assert [git.is_tracked(vault, path) for path in (committed, added, untracked, "notes/missing.md")] == [
        True,
        True,
        False,
        False,
    ]
    assert status(git_cmd, vault) == f"A  {added}\nM  {committed}\n?? {untracked}\n"

    git.unstage(vault, [added, committed])
    git.unstage(vault, [])

    assert status(git_cmd, vault) == f" M {committed}\n?? {added}\n?? {untracked}\n"
    assert (vault / added).is_file()
    assert (vault / committed).read_text(encoding="utf-8") == note_text("Changed")


def test_has_upstream_and_unpushed_count(vault: Path, remote: Path, git_cmd: Git) -> None:
    assert git.has_upstream(vault) is False

    git_cmd(vault, "remote", "add", "origin", str(remote))
    git_cmd(vault, "push", "-q", "--set-upstream", "origin", "master")
    assert git.has_upstream(vault) is True
    assert git.unpushed_count(vault, "origin", "master") == 0

    write(vault, "2026-09-02-a.md")
    git_cmd(vault, "add", "-A", "--", "notes")
    git_cmd(vault, "commit", "-q", "-m", "one note")
    assert git.unpushed_count(vault, "origin", "master") == 1


def test_unpushed_count_of_a_remote_never_pushed_to_is_every_commit(vault: Path, remote: Path, git_cmd: Git) -> None:
    """Without a `<remote>/<branch>` ref nothing is known to be there, so the whole branch counts as unpushed."""
    git_cmd(vault, "remote", "add", "origin", str(remote))
    git_cmd(vault, "push", "-q", "--set-upstream", "origin", "master")
    git_cmd(vault, "remote", "add", "backup", str(remote))

    assert git.remote_branch_known(vault, "origin", "master") is True
    assert git.remote_branch_known(vault, "backup", "master") is False
    assert git.unpushed_count(vault, "origin", "master") == 0
    assert git.unpushed_count(vault, "backup", "master") == len(subjects(git_cmd, vault))


def test_push_pending_pushes_to_the_configured_remote_not_to_the_upstream(
    vault: Path, remote: Path, git_cmd: Git, tmp_path: Path
) -> None:
    """`[git] remote` names the destination; a branch tracking another remote never redirects the push."""
    backup = tmp_path / "backup.git"
    git_cmd(tmp_path, "init", "-q", "--bare", "-b", "master", str(backup))
    git_cmd(vault, "remote", "add", "origin", str(remote))
    git_cmd(vault, "remote", "add", "backup", str(backup))
    git_cmd(vault, "push", "-q", "--set-upstream", "origin", "master")
    write(vault, "2026-09-02-a.md")
    git_cmd(vault, "add", "-A", "--", "notes")
    git_cmd(vault, "commit", "-q", "-m", "one note")

    assert git.push_pending(vault, "backup") == git.PushOutcome(True)
    assert remote_head(git_cmd, backup) == git.head(vault)
    assert remote_head(git_cmd, remote) != git.head(vault)

    assert git.push_pending(vault, "backup") == git.PushOutcome(False)


def test_push_pending_skips_a_remote_that_is_not_configured_although_a_branch_has_an_upstream(
    vault: Path, remote: Path, git_cmd: Git
) -> None:
    git_cmd(vault, "remote", "add", "origin", str(remote))
    git_cmd(vault, "push", "-q", "--set-upstream", "origin", "master")
    write(vault, "2026-09-02-a.md")
    git_cmd(vault, "add", "-A", "--", "notes")
    git_cmd(vault, "commit", "-q", "-m", "one note")

    assert git.push_pending(vault, "backup") is None
    assert remote_head(git_cmd, remote) != git.head(vault)


def test_push_pending_reports_what_it_did(vault: Path, remote: Path, git_cmd: Git) -> None:
    assert git.push_pending(vault, "origin") is None

    git_cmd(vault, "remote", "add", "origin", str(remote))
    assert git.push_pending(vault, "origin") == git.PushOutcome(True, set_upstream=True)
    assert git.push_pending(vault, "origin") == git.PushOutcome(False)

    write(vault, "2026-09-02-a.md")
    git_cmd(vault, "add", "-A", "--", "notes")
    git_cmd(vault, "commit", "-q", "-m", "one note")
    assert git.push_pending(vault, "origin") == git.PushOutcome(True)
    assert remote_head(git_cmd, remote) == git.head(vault)


def test_pull_ff_only_fast_forwards_and_reports_whether_head_moved(
    vault: Path, remote: Path, git_cmd: Git, tmp_path: Path
) -> None:
    git_cmd(vault, "remote", "add", "origin", str(remote))
    git_cmd(vault, "push", "-q", "--set-upstream", "origin", "master")
    other = clone_of(git_cmd, remote, tmp_path / "other")
    write(other, "2026-09-02-from-other.md")
    git_cmd(other, "add", "-A", "--", "notes")
    git_cmd(other, "commit", "-q", "-m", "from the other device")
    git_cmd(other, "push", "-q")

    assert git.pull_ff_only(vault, "origin") is True
    assert (vault / "notes" / "2026-09-02-from-other.md").is_file()
    assert git.head(vault) == git.head(other)

    assert git.pull_ff_only(vault, "origin") is False


def test_pull_ff_only_works_without_an_upstream(vault: Path, remote: Path, git_cmd: Git, tmp_path: Path) -> None:
    git_cmd(vault, "remote", "add", "origin", str(remote))
    git_cmd(vault, "push", "-q", "origin", "master")
    other = clone_of(git_cmd, remote, tmp_path / "other")
    write(other, "2026-09-02-from-other.md")
    git_cmd(other, "add", "-A", "--", "notes")
    git_cmd(other, "commit", "-q", "-m", "from the other device")
    git_cmd(other, "push", "-q")

    assert git.has_upstream(vault) is False
    assert git.pull_ff_only(vault, "origin") is True
    assert git.head(vault) == git.head(other)


def test_pull_ff_only_refuses_a_diverged_history_without_merging(
    vault: Path, remote: Path, git_cmd: Git, tmp_path: Path
) -> None:
    git_cmd(vault, "remote", "add", "origin", str(remote))
    git_cmd(vault, "push", "-q", "--set-upstream", "origin", "master")
    other = clone_of(git_cmd, remote, tmp_path / "other")
    write(other, "2026-09-02-from-other.md")
    git_cmd(other, "add", "-A", "--", "notes")
    git_cmd(other, "commit", "-q", "-m", "from the other device")
    git_cmd(other, "push", "-q")
    write(vault, "2026-09-02-local.md")
    git_cmd(vault, "add", "-A", "--", "notes")
    git_cmd(vault, "commit", "-q", "-m", "local")
    before = git.head(vault)

    with pytest.raises(GitError, match="history diverged from origin: .* must be reconciled with git"):
        git.pull_ff_only(vault, "origin")

    assert git.head(vault) == before
    assert not (vault / ".git" / "MERGE_HEAD").exists()
    assert status(git_cmd, vault) == ""


def test_pull_ff_only_reports_an_unreachable_remote(vault: Path, git_cmd: Git, tmp_path: Path) -> None:
    git_cmd(vault, "remote", "add", "origin", str(tmp_path / "missing.git"))

    with pytest.raises(GitError, match="^pull from origin failed: "):
        git.pull_ff_only(vault, "origin")
