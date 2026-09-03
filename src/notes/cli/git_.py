"""`notes push` and `notes pull`: the explicit network operations; the CLI never pulls on its own."""

from typing import Any

import click

from notes import config, git
from notes.cli import get_session, json_option, open_session, vault_command
from notes.cli.sync import render
from notes.errors import GitError
from notes.output import emit
from notes.sync import report_as_data
from notes.vault import require_vault


@vault_command("push", offline=True)
@click.pass_context
def push_command(ctx: click.Context) -> None:
    """Push the vault's commits to the configured remote, setting the upstream on the first push."""
    session = get_session(ctx)
    vault, remote = session.vault, session.config.git.remote
    if not git.is_repo(vault):
        raise GitError(f"{vault} is not a Git repository")
    outcome = git.push_pending(vault, remote, timeout=None)
    if outcome is None:
        raise GitError(f"remote {remote} is not configured; add it with `git remote add {remote} <url>` in {vault}")
    if not outcome.pushed:
        human = f"nothing to push, {remote} is up to date"
    elif outcome.set_upstream:
        human = f"pushed to {remote} and set it as the upstream"
    else:
        human = f"pushed to {remote}"
    emit(ctx, human, {"remote": remote, "pushed": outcome.pushed, "set_upstream": outcome.set_upstream})


@click.command("pull")
@json_option
@click.pass_context
def pull_command(ctx: click.Context) -> None:
    """Fast-forward the vault from the remote, then index what arrived; a diverged history is left to Git."""
    vault = require_vault()
    remote = config.load(vault).git.remote
    if not git.is_repo(vault):
        raise GitError(f"{vault} is not a Git repository")
    updated = git.pull_ff_only(vault, remote)
    session = open_session()
    ctx.call_on_close(session.close)
    first = f"pulled from {remote}" if updated else f"already up to date with {remote}"
    data: dict[str, Any] = {"remote": remote, "updated": updated, "sync": report_as_data(vault, session.report)}
    emit(ctx, f"{first}\n{render(session.report)}", data)
