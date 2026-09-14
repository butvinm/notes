"""`notes prompt <kind>`: the shared prompt, the kind prompt, and the template, as the generator receives them."""

import click

from notes import prompts
from notes.cli import json_option
from notes.output import emit
from notes.vault import require_vault


@click.command("prompt", short_help="Print the generator prompt for a kind")
@click.argument("kind")
@json_option
@click.pass_context
def prompt_command(ctx: click.Context, kind: str) -> None:
    """Print what the generator is told about KIND: the shared prompt, the kind prompt, and the template.

    The same texts the draft flow sends, without a context packet; a skill reads them to learn what the kind expects.
    """
    parts = prompts.load_parts(require_vault(), kind)
    data = {
        "kind": parts.kind,
        "shared_prompt": parts.shared,
        "kind_prompt": parts.kind_prompt,
        "template": parts.template,
    }
    emit(ctx, prompts.compose(parts), data)
