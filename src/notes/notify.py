"""Desktop notifications and the notification sound, through `notify-send` and `canberra-gtk-play`.

Both functions report failure instead of raising: `tick` runs unattended every minute, and a missing binary or an
unreachable notification service must leave the delivery recorded and unread rather than abort the run. The
result is truthy on success and carries the reason otherwise, for the warning `tick` prints on stderr.
"""

from dataclasses import dataclass

from notes import proc

APP_NAME = "notes"
TIMEOUT = 10.0
"""Seconds a notification or sound command may take; neither should block a run that repeats every minute."""


@dataclass(frozen=True)
class NotifyResult:
    """Whether the program ran successfully; `error` says why not."""

    ok: bool
    error: str = ""

    def __bool__(self) -> bool:
        return self.ok


def send(title: str, body: str) -> NotifyResult:
    """Show a desktop notification with the note's title and ID; never raises."""
    return _run(["notify-send", "--app-name", APP_NAME, "--", title, body])


def play_sound(sound_id: str) -> NotifyResult:
    """Play one sound from the desktop sound theme; never raises."""
    return _run(["canberra-gtk-play", "-i", sound_id])


def _run(command: list[str]) -> NotifyResult:
    program = command[0]
    try:
        result = proc.run_command(command, timeout=TIMEOUT)
    except proc.CommandNotFound:
        return NotifyResult(False, f"{program} is not installed or not on PATH")
    except proc.CommandTimeout as error:
        return NotifyResult(False, f"{program} timed out after {error.timeout:g} seconds")
    if not result.ok:
        lines = [line.strip() for line in result.stderr.splitlines() if line.strip()]
        reason = lines[-1] if lines else f"exit status {result.returncode}"
        return NotifyResult(False, f"{program} failed: {reason}")
    return NotifyResult(True)
