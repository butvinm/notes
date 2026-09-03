"""The `notes init` flow: create the single vault at `~/.notes`, or clone it from a remote that already holds one.

A fresh vault gets the packaged defaults, a placeholder in `notes/` so Git keeps the directory, a repository on
`master` with the initial commit, the remote when one was given (pushed with `--set-upstream` when auto-push is on),
and the SQLite index. A remote that already has commits is cloned instead, which is also the restore and
second-device flow; the clone must be a vault, or it is removed again.

Nothing is left half-made: `git ls-remote` runs before anything is created, so a remote that cannot be inspected
aborts the command, and a Git failure while making the fresh vault removes what was written. A failed initial push
and a refused systemd timer are warnings, because the vault is complete by then and both are retried by later
commands.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path

from notes import config, db, defaults_io, git, systemd
from notes import sync as sync_engine
from notes.errors import GitError, SystemdError, UsageError
from notes.sync import SyncReport
from notes.systemd import EnableOutcome
from notes.vault import is_vault, notes_dir, root

INITIAL_COMMIT_MESSAGE = "notes: initialize vault"
KEEP_FILE = ".gitkeep"
"""Committed inside `notes/`, so a clone of a vault that has no notes yet still arrives with the directory."""


@dataclass(frozen=True)
class InitOutcome:
    """What `run` did: where the vault is, whether it was cloned, the remote it was given, whether the initial
    commit was pushed, the report of the first index build, the timer enablement, and the warnings collected."""

    vault: Path
    cloned: bool
    remote: str | None
    remote_name: str
    pushed: bool
    report: SyncReport
    notifications: EnableOutcome | None
    warnings: tuple[str, ...] = ()


def check_location(vault: Path) -> None:
    """Refuse an existing vault and any non-empty or non-directory `~/.notes`; an empty directory is fine."""
    if is_vault(vault):
        raise UsageError(f"a vault already exists at {vault}")
    if not vault.exists():
        return
    if not vault.is_dir():
        raise UsageError(f"{vault} exists and is not a directory; move it away before `notes init`")
    if any(vault.iterdir()):
        raise UsageError(f"{vault} exists and is not a vault; move it away or empty it before `notes init`")


def run(*, remote: str | None, auto_push: bool, enable_notifications: bool) -> InitOutcome:
    """Create or clone the vault at `~/.notes`, build its index, and enable the timer when asked."""
    vault = root()
    check_location(vault)
    existed = vault.is_dir()
    warnings: list[str] = []
    pushed = False
    if remote is not None and git.ls_remote(remote):
        cloned = True
        _clone(remote, vault, existed)
        loaded = config.load(vault)
        if auto_push and not loaded.git.auto_push:
            warnings.append("config.toml came with the clone and keeps auto_push = false; edit it to turn auto-push on")
    else:
        cloned = False
        loaded = _create(vault, existed, auto_push=auto_push, remote=remote)
        if remote is not None and auto_push:
            try:
                git.push(vault, loaded.git.remote, set_upstream=True)
                pushed = True
            except GitError as error:
                warnings.append(f"{error.message}; the vault is complete and the next command retries the push")
    report = _index(vault)
    if report.invalid:
        count = len(report.invalid)
        noun = "file" if count == 1 else "files"
        warnings.append(f"{count} invalid {noun} in the cloned vault, `notes check` lists them")
    notifications = None
    if enable_notifications:
        try:
            notifications = systemd.enable(systemd.exec_command())
            warnings.extend(notifications.warnings)
        except SystemdError as error:
            warnings.append(f"{error.message}; the vault is complete, run `notes notifications enable` later")
    return InitOutcome(vault, cloned, remote, loaded.git.remote, pushed, report, notifications, tuple(warnings))


def _create(vault: Path, existed: bool, *, auto_push: bool, remote: str | None) -> config.Config:
    """Install the defaults, make the repository with its initial commit, and add the remote; returns the config.

    A Git failure removes everything written here, so the user can fix the cause (a missing identity, say)
    and run `notes init` again without first cleaning up.
    """
    try:
        defaults_io.install(vault, auto_push=auto_push)
        (notes_dir(vault) / KEEP_FILE).write_text("", encoding="utf-8")
        loaded = config.load(vault)
        git.init_repo(vault)
        git.stage(vault, ["."])
        git.commit(vault, INITIAL_COMMIT_MESSAGE)
        if remote is not None:
            git.add_remote(vault, loaded.git.remote, remote)
    except GitError:
        _discard(vault, existed)
        raise
    return loaded


def _clone(url: str, vault: Path, existed: bool) -> None:
    """Clone the remote into the vault location and make sure what arrived is a vault."""
    git.clone(url, vault)
    if not is_vault(vault):
        _discard(vault, existed)
        raise UsageError(
            f"{url} does not hold a vault (no config.toml with a notes/ directory); the clone was removed again"
        )


def _discard(vault: Path, existed: bool) -> None:
    """Remove what was written; a directory that existed before `notes init` is left behind empty, as it was."""
    shutil.rmtree(vault, ignore_errors=True)
    if existed:
        vault.mkdir(parents=True, exist_ok=True)


def _index(vault: Path) -> SyncReport:
    """Create `index.sqlite` and fill it from the files, so the first command after init finds a warm index."""
    conn = db.open_index(vault)
    try:
        return sync_engine.run(vault, conn)
    finally:
        conn.close()
