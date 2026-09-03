"""Error hierarchy: every `NotesError` reaches the user as exit code 1 with a human or JSON message."""

from typing import ClassVar


class NotesError(Exception):
    """Base class for user-facing errors; `type` is the machine name printed in the JSON error envelope."""

    type: ClassVar[str] = "error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NoVaultError(NotesError):
    """No vault exists at `~/.notes`."""

    type = "no_vault"


class ValidationFailed(NotesError):
    """A note or draft did not pass validation."""

    type = "validation_failed"


class GeneratorError(NotesError):
    """The draft generator failed, timed out, or returned an unusable envelope."""

    type = "generator_error"


class GitError(NotesError):
    """A Git operation that must succeed failed, for example a divergent pull or an explicit push."""

    type = "git_error"


class SystemdError(NotesError):
    """The systemd user manager could not be driven: `systemctl` is missing, unreachable, or refused a unit."""

    type = "systemd_error"


class EditorError(NotesError):
    """The editor could not be used: neither `$VISUAL` nor `$EDITOR` is set, the program is missing, or it failed."""

    type = "editor_error"


class UsageError(NotesError):
    """The request cannot be carried out as asked: an unknown note, kind, relation, or an unreadable config."""

    type = "usage_error"
