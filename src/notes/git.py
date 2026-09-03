"""The Git layer: every `git` invocation in the program goes through `run_git`, with `cwd` set to the vault.

Two kinds of function live here. The low-level operations (`init_repo`, `stage`, `commit`, `push`, `pull_ff_only`,
`mv`, ...) raise `GitError` when Git fails, so a command that must succeed reports the failure as an error.
`commit_after_sync` is the auto-commit step that every command on an existing vault runs after its sync: it stages
the valid and the removed notes Git reports as changed plus the other versioned files, commits them with a message
derived from what was staged, and pushes when `git.auto_push` is on. Its failures are warnings, never errors,
because a broken push must not stop a local command; the local commit stays and the next command retries.

A vault without a `.git` directory is left alone: the auto-commit step does nothing there, and `notes push` and
`notes pull` report it.
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from notes import proc
from notes.config import Config
from notes.errors import GitError
from notes.proc import CommandResult
from notes.sync import SyncReport

DEFAULT_BRANCH = "master"
PUSH_TIMEOUT = 15.0
"""Seconds an automatic push may take; a hanging network must not block an interactive command for longer."""

LOCK_RETRY_DELAY = 0.2
"""Seconds to wait before the single retry of a command that lost the race for `.git/index.lock`."""

VERSIONED_EXTRAS = ("types", "prompt.md", "config.toml", ".gitignore")
"""The versioned files outside `notes/` that the auto-commit step stages when Git reports them changed."""


def is_repo(vault: Path) -> bool:
    """Whether the vault carries its own repository (`.git` is a directory, or a file for a worktree)."""
    return (vault / ".git").exists()


def run_git(args: Sequence[str | Path], *, cwd: Path | None, timeout: float | None = None) -> CommandResult:
    """Run one `git` command and return its result; a non-zero exit is the caller's to interpret.

    A command that failed because another process held `.git/index.lock` is retried once after a short delay.
    A missing `git` binary and an exceeded timeout are `GitError`s.
    """
    command = ["git", *args]
    try:
        result = proc.run_command(command, cwd=cwd, timeout=timeout)
        if not result.ok and "index.lock" in result.stderr:
            time.sleep(LOCK_RETRY_DELAY)
            result = proc.run_command(command, cwd=cwd, timeout=timeout)
    except proc.CommandNotFound as error:
        raise GitError("git is not installed or not on PATH") from error
    except proc.CommandTimeout as error:
        raise GitError(f"git {args[0]} timed out after {error.timeout:g} seconds") from error
    return result


def _checked(result: CommandResult, what: str) -> CommandResult:
    if not result.ok:
        raise GitError(f"{what}: {_tail(result.stderr) or f'git exited with status {result.returncode}'}")
    return result


def _tail(text: str, lines: int = 3) -> str:
    """The last few non-empty lines of Git's stderr, minus its `hint:` chatter, joined into one line."""
    kept = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("hint:")]
    return " ".join(kept[-lines:])


# Repository setup


def init_repo(vault: Path, branch: str = DEFAULT_BRANCH) -> None:
    """`git init` with the given initial branch."""
    _checked(run_git(["init", "-q", "-b", branch], cwd=vault), "git init failed")


def add_remote(vault: Path, name: str, url: str) -> None:
    _checked(run_git(["remote", "add", name, url], cwd=vault), f"adding remote {name} failed")


def ls_remote(url: str) -> list[str]:
    """The ref names the remote advertises; an empty list for an empty repository, `GitError` when unreachable."""
    result = _checked(run_git(["ls-remote", "--", url], cwd=None), f"cannot inspect remote {url}")
    return [line.split("\t", 1)[1] for line in result.stdout.splitlines() if "\t" in line]


def clone(url: str, dest: Path) -> None:
    _checked(run_git(["clone", "-q", "--", url, dest], cwd=None), f"cloning {url} failed")


# Working tree and index


def stage(vault: Path, paths: Sequence[str]) -> None:
    """`git add -A` limited to the given paths: additions, modifications, and deletions of exactly those."""
    if paths:
        _checked(run_git(["add", "-A", "--", *paths], cwd=vault), "git add failed")


def commit(vault: Path, message: str) -> None:
    _checked(run_git(["commit", "-q", "-m", message], cwd=vault), "git commit failed")


def mv(vault: Path, old: str, new: str) -> None:
    """`git mv`: rename a tracked file and stage the rename; the parent directory of `new` must exist."""
    _checked(run_git(["mv", "--", old, new], cwd=vault), f"git mv {old} -> {new} failed")


def unstage(vault: Path, paths: Sequence[str]) -> None:
    """`git reset` limited to the given paths: their index entries go back to `HEAD`, the working tree is untouched."""
    if paths:
        _checked(run_git(["reset", "-q", "--", *paths], cwd=vault), "git reset failed")


def is_tracked(vault: Path, path: str) -> bool:
    """Whether `path` is in the index, which is what `git mv` needs of a file."""
    return run_git(["ls-files", "--error-unmatch", "--", path], cwd=vault).ok


@dataclass(frozen=True)
class StatusEntry:
    """One entry of `git status --porcelain`: the index and working-tree codes, the path, and where it came from.

    A staged rename is one entry under its new name, since `git add` would reject the old name as a pathspec
    matching neither the index nor the working tree. The old name is not lost, though: it is the entry's
    `orig_path`, which is what tells `_commit_changes` that a deletion is staged along with the new path.
    """

    index: str
    worktree: str
    path: str
    orig_path: str | None = None

    @property
    def staged(self) -> bool:
        """Whether the index differs from `HEAD` for this path (an untracked file is not staged)."""
        return self.index not in " ?"


def status_entries(vault: Path) -> list[StatusEntry]:
    """Every entry `git status` reports, changed or untracked, relative to the vault.

    Ignored files never appear, so the index database and the drafts are invisible here.
    """
    result = _checked(run_git(["status", "--porcelain", "-z", "-uall"], cwd=vault), "git status failed")
    return _parse_status(result.stdout)


def changed_versioned_files(vault: Path) -> list[str]:
    """The paths of `status_entries`: everything `git status` reports as changed or untracked."""
    return [entry.path for entry in status_entries(vault)]


def _parse_status(output: str) -> list[StatusEntry]:
    """`git status --porcelain -z` into entries; a rename or copy is followed by its origin in the next field."""
    fields = output.split("\0")
    entries = []
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if not entry:
            continue
        status, path = entry[:2], entry[3:]
        orig_path = None
        if "R" in status or "C" in status:
            orig_path = fields[index] if index < len(fields) else None
            index += 1
        entries.append(StatusEntry(status[0], status[1], path, orig_path))
    return entries


@dataclass(frozen=True)
class StagedChange:
    """One entry of the index relative to `HEAD`: status `A`, `M`, or `D` and the path."""

    status: str
    path: str


def staged_changes(vault: Path) -> list[StagedChange]:
    """What `git commit` would record right now, from `git diff --cached --name-status`.

    Rename detection is off, so a deleted note and a similar new one are two entries and the commit message
    stays deterministic; `notes move` names its rename through the explicit message instead.
    """
    result = _checked(
        run_git(["diff", "--cached", "--name-status", "-z", "--no-renames"], cwd=vault), "git diff failed"
    )
    fields = result.stdout.split("\0")
    pairs = zip(fields[0::2], fields[1::2], strict=False)
    return [StagedChange(status[0], path) for status, path in pairs if status]


def has_staged_changes(vault: Path) -> bool:
    return bool(staged_changes(vault))


def head(vault: Path) -> str | None:
    """The commit `HEAD` points at, or `None` before the first commit."""
    result = run_git(["rev-parse", "--verify", "-q", "HEAD"], cwd=vault)
    return result.stdout.strip() if result.ok else None


def current_branch(vault: Path) -> str:
    result = _checked(run_git(["symbolic-ref", "--short", "-q", "HEAD"], cwd=vault), "not on a branch")
    return result.stdout.strip()


# Remotes


def remote_exists(vault: Path, name: str) -> bool:
    return run_git(["remote", "get-url", name], cwd=vault).ok


def has_upstream(vault: Path) -> bool:
    return run_git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"], cwd=vault).ok


def remote_branch_known(vault: Path, remote: str, branch: str) -> bool:
    """Whether this machine has a remote-tracking ref for `<remote>/<branch>`; a fetch or a push creates one."""
    return run_git(["rev-parse", "--verify", "-q", f"refs/remotes/{remote}/{branch}"], cwd=vault).ok


def unpushed_count(vault: Path, remote: str, branch: str) -> int:
    """Commits on `branch` that `<remote>/<branch>` does not have.

    The comparison names the remote the caller is about to push to, not `@{upstream}`, which can be a different
    remote. Without a remote-tracking ref for `<remote>/<branch>` this machine knows of nothing that ever arrived
    there, so every commit counts as unpushed and the push runs; Git then tells the truth about what is there.
    """
    known = remote_branch_known(vault, remote, branch)
    revisions = f"refs/remotes/{remote}/{branch}..HEAD" if known else "HEAD"
    result = _checked(run_git(["rev-list", "--count", revisions], cwd=vault), "git rev-list failed")
    return int(result.stdout.strip() or 0)


def push(
    vault: Path,
    remote: str,
    branch: str | None = None,
    *,
    set_upstream: bool = False,
    timeout: float | None = PUSH_TIMEOUT,
) -> None:
    """`git push <remote> <branch>`, the current branch by default; with `set_upstream`, record it as the upstream.

    The destination is always spelled out, so neither `branch.<name>.remote` nor a `remote.pushDefault` in the
    user's global configuration can send the commits somewhere other than the remote the command reports.
    """
    args = ["push", "-q"]
    if set_upstream:
        args.append("--set-upstream")
    args += [remote, branch or current_branch(vault)]
    _checked(run_git(args, cwd=vault, timeout=timeout), f"push to {remote} failed")


@dataclass(frozen=True)
class PushOutcome:
    """What `push_pending` did: nothing (`pushed` is False, the remote already had the branch's commits), a plain
    push, or the first push of a branch without an upstream (`set_upstream`)."""

    pushed: bool
    set_upstream: bool = False


def push_pending(vault: Path, remote: str, *, timeout: float | None = PUSH_TIMEOUT) -> PushOutcome | None:
    """Push to `remote` what it lacks of the current branch, setting the upstream on a branch that has none.

    Returns `None` without touching the network when `remote` is not configured in the vault, the state of a vault
    initialized with `--auto-push` but no `--remote`. Everything here names `remote` and the current branch: the
    commits go where `config.toml` says even when the branch's upstream is another remote, and the up-to-date
    answer is about that same destination.
    """
    if not remote_exists(vault, remote):
        return None
    branch = current_branch(vault)
    if not has_upstream(vault):
        push(vault, remote, branch, set_upstream=True, timeout=timeout)
        return PushOutcome(True, set_upstream=True)
    if unpushed_count(vault, remote, branch) == 0:
        return PushOutcome(False)
    push(vault, remote, branch, timeout=timeout)
    return PushOutcome(True)


def pull_ff_only(vault: Path, remote: str) -> bool:
    """`git pull --ff-only` from `remote`; returns whether `HEAD` moved.

    A history that cannot be fast-forwarded is a `GitError` naming Git as the way to resolve it; nothing is merged.
    """
    before = head(vault)
    result = run_git(["pull", "-q", "--ff-only", remote, current_branch(vault)], cwd=vault)
    if not result.ok:
        if "Not possible to fast-forward" in result.stderr:
            raise GitError(
                f"history diverged from {remote}: local and remote commits must be reconciled with git "
                f"(merge or rebase) in {vault}"
            )
        raise GitError(f"pull from {remote} failed: {_tail(result.stderr)}")
    return head(vault) != before


# The auto-commit step


@dataclass(frozen=True)
class CommitOutcome:
    """What `commit_after_sync` did: the message of the commit it made (or `None`), whether it pushed, and the
    warnings for the failures it swallowed."""

    message: str | None
    pushed: bool
    warnings: tuple[str, ...] = ()


def commit_after_sync(
    vault: Path, report: SyncReport, config: Config, *, allow_push: bool = True, message: str | None = None
) -> CommitOutcome:
    """Commit the outcome of a sync and push it when configured; every failure is a warning in the outcome.

    Staged are the paths Git reports as changed that are either notes (`.md` files under `notes/`) not listed in
    `report.invalid`, which is every valid note whichever sync indexed it and every removed one, or versioned files
    outside `notes/` (`VERSIONED_EXTRAS`). A valid note is staged even when this sync found it unchanged: the
    index-only sync of `notes tick` (the minutely timer) or `notes read` may have indexed it first without
    committing, and the next command must still commit it. Invalid notes are never staged, so an invalid change
    stays uncommitted until it is corrected; one that reached the index anyway (the `git mv` of `notes move`) is
    unstaged first, together with the rename's origin, so the note stays at `HEAD` instead of being committed as
    a deletion. `message` overrides the derived commit message, for `notes move`. With `allow_push` False (the
    offline commands) the push step is skipped even when `git.auto_push` is on.
    """
    if not is_repo(vault):
        return CommitOutcome(None, False)
    warnings: list[str] = []
    committed = None
    try:
        committed = _commit_changes(vault, report, message)
    except GitError as error:
        warnings.append(f"{error.message}; the change stays uncommitted")
    pushed = False
    if allow_push and config.git.auto_push:
        try:
            outcome = push_pending(vault, config.git.remote)
            pushed = outcome is not None and outcome.pushed
        except GitError as error:
            warnings.append(f"{error.message}; the commit is kept and the next command retries the push")
    return CommitOutcome(committed, pushed, tuple(warnings))


def _commit_changes(vault: Path, report: SyncReport, message: str | None) -> str | None:
    invalid = set(report.invalid_paths)
    entries = status_entries(vault)
    # A `git mv` onto a path that is invalid is one staged rename, and the deletion of its origin belongs to the
    # same unit: unstaging only the new path would commit the note's disappearance under a move message. Both
    # halves go back to `HEAD`, so the note keeps its place there until it is valid again.
    excluded = invalid | {entry.orig_path for entry in entries if entry.path in invalid and entry.orig_path}
    staged_invalid = [
        path
        for entry in entries
        if entry.staged and entry.path in invalid
        for path in (entry.path, entry.orig_path)
        if path is not None
    ]
    if staged_invalid:
        unstage(vault, staged_invalid)
        entries = status_entries(vault)
    # An entry with nothing in the working tree beyond the index (a staged deletion, say) has nothing left to add,
    # and `git add` rejects a deleted path that is already gone from the index; what is staged is committed as is.
    to_stage = [entry.path for entry in entries if entry.worktree != " " and _stageable(entry.path, excluded)]
    if not to_stage and not any(entry.staged for entry in entries):
        return None
    stage(vault, to_stage)
    staged = staged_changes(vault)
    if not staged:
        return None
    text = message or commit_message(staged)
    commit(vault, text)
    return text


def _stageable(path: str, excluded: set[str]) -> bool:
    """Whether a path Git reports as changed belongs in the commit: a note that is not excluded, or a versioned extra.

    Every `.md` file under `notes/` is either indexed, invalid, or gone from disk, since the sync walks them all;
    so a note outside `excluded` (the invalid ones plus the origin of a rename onto an invalid path) is a valid
    note or a deletion, whichever sync indexed the change.
    """
    if _is_note(path):
        return path not in excluded
    return _is_extra(path)


def _is_note(path: str) -> bool:
    return path.startswith("notes/") and path.endswith(".md")


def _is_extra(path: str) -> bool:
    return path in VERSIONED_EXTRAS or path.startswith("types/")


def commit_message(staged: Sequence[StagedChange]) -> str:
    """`notes: update <path>` or `notes: remove <path>` for a single file, otherwise `notes: sync <n> files`."""
    if len(staged) == 1:
        change = staged[0]
        if change.status == "D":
            return f"notes: remove {change.path}"
        return f"notes: update {change.path}"
    return f"notes: sync {len(staged)} files"


def move_message(old: str, new: str) -> str:
    """The commit message of `notes move`, passed to `commit_after_sync` as the explicit `message`."""
    return f"notes: move {old} -> {new}"
