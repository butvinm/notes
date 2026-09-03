"""`notes sync`: print the report of the sync that already ran before the command."""

import click

from notes.cli import get_session, vault_command
from notes.output import emit, json_enabled
from notes.sync import SyncReport, error_lines, report_as_data


@vault_command("sync")
@click.pass_context
def sync_command(ctx: click.Context) -> None:
    """Index changed notes into SQLite and report the changes; invalid files are warnings, not errors."""
    session = get_session(ctx)
    report = session.report
    if not json_enabled(ctx):
        for line in error_lines(session.vault, report.invalid):
            click.echo(f"warning: {line}", err=True)
    emit(ctx, render(report), report_as_data(session.vault, report))


def render(report: SyncReport) -> str:
    """One line per changed and removed path, then the counts."""
    lines = [f"changed: {path}" for path in report.changed]
    lines.extend(f"removed: {path}" for path in report.removed)
    lines.append(
        f"{len(report.changed)} changed, {len(report.removed)} removed, "
        f"{report.unchanged_count} unchanged, {len(report.invalid)} invalid"
    )
    return "\n".join(lines)
