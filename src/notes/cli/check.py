"""`notes check`: list every invalid file as `path:line: message`, exit 1 when there is any, notice newer defaults."""

from pathlib import Path

import click

from notes import defaults_io
from notes.cli import get_session, vault_command
from notes.output import emit, json_enabled
from notes.sync import error_lines, invalid_as_data


@vault_command("check")
@click.pass_context
def check_command(ctx: click.Context) -> None:
    """Validate every note and list the invalid files with line numbers; exit 1 when any file is invalid."""
    session = get_session(ctx)
    report = session.report
    newer = defaults_io.newer_defaults_available(session.vault)
    lines = error_lines(session.vault, report.invalid)
    if lines:
        lines.append(
            f"{_count(len(report.invalid), 'invalid file')}, {_count(report.note_count, 'valid note')} indexed"
        )
    else:
        lines.append(f"ok: {_count(report.note_count, 'note')} indexed, no invalid files")
    data = {
        "ok": not report.invalid,
        "note_count": report.note_count,
        "invalid": invalid_as_data(session.vault, report.invalid),
        "newer_defaults_available": newer,
    }
    emit(ctx, "\n".join(lines), data)
    if newer and not json_enabled(ctx):
        click.echo(defaults_notice(session.vault), err=True)
    if report.invalid:
        ctx.exit(1)


def defaults_notice(vault: Path) -> str:
    """The one-line notice that the package ships newer prompts or templates than the vault recorded."""
    installed = defaults_io.installed_version(vault)
    recorded = f"version {installed}" if installed is not None else "no recorded version"
    return (
        f"notice: packaged defaults version {defaults_io.packaged_version()} is newer than the vault's ({recorded}); "
        "types/ and prompt.md are left as they are, compare them with the packaged files by hand"
    )


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"
