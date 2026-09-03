"""The user's editor: `$VISUAL`, then `$EDITOR`, run through `run_command` with the terminal inherited.

`notes new` and `notes edit` open a note here when they run in a terminal; outside one the step is skipped by the
commands, because a terminal editor started without a terminal hangs or fails. The variable may hold a command with
arguments (`code --wait`), split the way a shell would; the file path is appended as the last argument.
Every failure is an `EditorError`: no editor configured, a value that cannot be split, the program not found,
or a non-zero exit.
"""

import os
import shlex
from pathlib import Path

from notes import proc
from notes.errors import EditorError

VARIABLES = ("VISUAL", "EDITOR")
"""The environment variables consulted in order; the first one with a non-blank value wins."""


def editor_command() -> list[str] | None:
    """The configured editor as an argument list, or None when neither variable holds a command."""
    for variable in VARIABLES:
        value = os.environ.get(variable, "")
        if not value.strip():
            continue
        try:
            command = shlex.split(value)
        except ValueError as error:
            raise EditorError(f"${variable} is not a valid command ({error}): {value}") from None
        if command:
            return command
    return None


def require_editor() -> list[str]:
    """The configured editor, or an `EditorError` telling the user which variables to set."""
    command = editor_command()
    if command is None:
        raise EditorError("no editor configured: set $VISUAL or $EDITOR")
    return command


def open_in_editor(path: Path) -> None:
    """Open `path` in the editor and wait until it closes; the editor inherits stdin, stdout, and stderr.

    A missing program or a non-zero exit status is an `EditorError` naming the file, which stays where it is.
    """
    command = require_editor()
    try:
        result = proc.run_command([*command, path], capture=False)
    except proc.CommandNotFound:
        raise EditorError(
            f"editor `{command[0]}` not found; set $VISUAL or $EDITOR to an installed editor ({path} is left as it is)"
        ) from None
    if not result.ok:
        raise EditorError(f"editor `{command[0]}` exited with status {result.returncode}; {path} is left as it is")
