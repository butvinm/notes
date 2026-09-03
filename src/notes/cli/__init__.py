"""Command-line interface: the root `notes` group, the shared `--json` option, `NotesError` handling,
and the sync that runs before every command on an existing vault.

Commands come in two shapes. `init`, `notifications`, `prompt`, and `pull` are plain click commands that manage
the vault from the outside. Every other command is a `VaultCommand`: before its callback runs, the vault is
resolved, its config loaded, the index opened, and the files under `notes/` synced into it; the result is a
`Session` in `ctx.obj["session"]`, which `notes sync` and `notes check` print instead of syncing a second time.

Every sync of a session ends with the Git auto-commit step, except for the index-only commands (`tick`, `read`),
and the offline commands (`recall`, `push`) commit but never auto-push.

`interactive` is the one test of whether a command may ask questions or open the editor: both stdin and stdout
must be terminals. Commands import it by name, so a test replaces it in the command's module.
"""

import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

import click

from notes import __version__, config, db, git
from notes import sync as sync_engine
from notes.config import Config
from notes.errors import NotesError
from notes.output import emit_error, json_enabled
from notes.sync import SyncReport
from notes.vault import require_vault


def interactive() -> bool:
    """Whether prompts may be shown and the editor opened: both stdin and stdout are terminals."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def _store_json_flag(ctx: click.Context, _param: click.Parameter, value: bool) -> bool:
    ctx.ensure_object(dict)["json"] = value
    return value


def json_option[C: Callable[..., Any] | click.Command](command: C) -> C:
    """The shared `--json` flag: stored in `ctx.obj["json"]` for `output.emit`, never passed to the command function."""
    return click.option(
        "--json",
        "json_",
        is_flag=True,
        expose_value=False,
        callback=_store_json_flag,
        help="Print a stable machine-readable representation instead of the human output.",
    )(command)


class CliError(click.ClickException):
    """A `NotesError` on its way out: click calls `show()` and exits with `exit_code`."""

    exit_code = 1

    def __init__(self, error: NotesError, *, as_json: bool) -> None:
        super().__init__(error.message)
        self.error = error
        self.as_json = as_json

    def show(self, file: IO[Any] | None = None) -> None:
        emit_error(self.error, as_json=self.as_json, file=file)


class NotesGroup(click.Group):
    """The root group class: a `NotesError` raised by any command becomes exit code 1 with human or JSON output."""

    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except NotesError as error:
            raise CliError(error, as_json=json_enabled(ctx)) from error


@dataclass
class Session:
    """What a command on an existing vault works with: the vault, its config, the open index, and the sync report.

    `commit` is False for the index-only commands (`tick` and `read`): they change only SQLite, so their sync
    indexes but never commits or pushes. `push` is False for the offline commands (`recall`, which must stay
    inside the hook's time budget, and `push`, which pushes explicitly): their sync commits but never auto-pushes.
    """

    vault: Path
    config: Config
    conn: sqlite3.Connection
    report: SyncReport
    commit: bool = True
    push: bool = True

    def sync(self, *, message: str | None = None) -> SyncReport:
        """Index the vault, keep the report, and run the Git auto-commit step when this session commits.

        A command that wrote Markdown calls it again to pick up its change; `message` then overrides the commit
        message derived from the staged files. Commit and push failures are printed as warnings on stderr.
        """
        self.report = sync_engine.run(self.vault, self.conn)
        if self.commit:
            outcome = git.commit_after_sync(self.vault, self.report, self.config, allow_push=self.push, message=message)
            for warning in outcome.warnings:
                click.echo(f"warning: {warning}", err=True)
        return self.report

    def close(self) -> None:
        self.conn.close()


def open_session(*, commit: bool = True, push: bool = True) -> Session:
    """Resolve the vault, load its config, open the index, and run the pre-command sync."""
    vault = require_vault()
    loaded = config.load(vault)
    conn = db.open_index(vault)
    try:
        session = Session(vault, loaded, conn, sync_engine.EMPTY_REPORT, commit, push)
        session.sync()
    except BaseException:
        conn.close()
        raise
    return session


class VaultCommand(click.Command):
    """A command on the existing vault: the vault is synced before the callback runs and the session is in `ctx.obj`.

    The sync starts from `invoke`, after the command parsed its own arguments, so `--help` never touches the vault
    and `--json` is already known when a missing vault is reported. `index_only` marks the commands whose sync must
    never commit or push; `offline` marks the ones whose sync commits but never auto-pushes.
    """

    def __init__(self, *args: Any, index_only: bool = False, offline: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.index_only = index_only
        self.offline = offline

    def invoke(self, ctx: click.Context) -> Any:
        session = open_session(commit=not self.index_only, push=not (self.index_only or self.offline))
        ctx.call_on_close(session.close)
        ctx.ensure_object(dict)["session"] = session
        return super().invoke(ctx)


def vault_command(
    name: str | None = None, *, index_only: bool = False, offline: bool = False, **attrs: Any
) -> Callable[[Callable[..., Any]], VaultCommand]:
    """Declare a command that runs on the existing vault: sync first, `--json` accepted, session via `get_session`."""

    def decorator(function: Callable[..., Any]) -> VaultCommand:
        command = click.command(name, cls=VaultCommand, index_only=index_only, offline=offline, **attrs)(function)
        return json_option(command)

    return decorator


def get_session(ctx: click.Context) -> Session:
    """The session prepared by `VaultCommand.invoke` for the running command."""
    obj = ctx.find_object(dict)
    session = obj.get("session") if obj else None
    if not isinstance(session, Session):
        raise RuntimeError("no vault session: declare the command with `vault_command`")
    return session


@click.group("notes", cls=NotesGroup, context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="notes")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Personal notes: Markdown files in ~/.notes, indexed in SQLite, searchable and schedulable."""
    ctx.ensure_object(dict)


def main() -> None:
    """Console-script entry point."""
    cli()


def _register_commands() -> None:
    """Attach the subcommands to the root group; imported here because the command modules import this module."""
    from notes.cli import (
        check,
        draft,
        edit,
        git_,
        init,
        list_,
        move,
        new,
        notifications,
        prompt,
        read,
        recall,
        relate,
        search,
        show,
        sync,
        tick,
    )

    cli.add_command(init.init_command)
    cli.add_command(new.new_command)
    cli.add_command(edit.edit_command)
    cli.add_command(show.show_command)
    cli.add_command(search.search_command)
    cli.add_command(recall.recall_command)
    cli.add_command(list_.list_command)
    cli.add_command(relate.relate_command)
    cli.add_command(move.move_command)
    cli.add_command(sync.sync_command)
    cli.add_command(check.check_command)
    cli.add_command(tick.tick_command)
    cli.add_command(read.read_command)
    cli.add_command(git_.push_command)
    cli.add_command(git_.pull_command)
    cli.add_command(prompt.prompt_command)
    cli.add_command(notifications.notifications_group)
    cli.add_command(draft.draft_group)


_register_commands()
