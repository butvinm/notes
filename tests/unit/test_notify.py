"""Tests for the desktop notification and sound helpers: they report failure and never raise."""

from notes import notify
from notes.notify import NotifyResult
from notes.proc import CommandResult, CommandTimeout
from tests.conftest import RecordedCommands

TITLE = "Review the Project Atlas Kafka decision"
NOTE_ID = "notes/2026-09-02-review-project-atlas-kafka-decision.md"


def test_send_runs_notify_send_with_the_app_name(recorded_commands: RecordedCommands) -> None:
    result = notify.send(TITLE, NOTE_ID)

    assert result == NotifyResult(True)
    assert result
    assert recorded_commands.commands() == [("notify-send", "--app-name", "notes", "--", TITLE, NOTE_ID)]
    assert recorded_commands.calls[0].timeout == notify.TIMEOUT


def test_send_keeps_a_title_starting_with_a_dash_out_of_option_parsing(recorded_commands: RecordedCommands) -> None:
    assert notify.send("-x is not an option", NOTE_ID)

    assert recorded_commands.commands() == [
        ("notify-send", "--app-name", "notes", "--", "-x is not an option", NOTE_ID)
    ]


def test_send_failure_returns_false_with_the_reason(recorded_commands: RecordedCommands) -> None:
    recorded_commands.script(
        "notify-send",
        returncode=1,
        stderr="Failed to show notification: Could not connect: No such file or directory\n",
    )

    result = notify.send(TITLE, NOTE_ID)

    assert not result
    assert result == NotifyResult(
        False, "notify-send failed: Failed to show notification: Could not connect: No such file or directory"
    )


def test_send_failure_without_stderr_reports_the_exit_status(recorded_commands: RecordedCommands) -> None:
    recorded_commands.script("notify-send", returncode=2)

    assert notify.send(TITLE, NOTE_ID) == NotifyResult(False, "notify-send failed: exit status 2")


def test_send_missing_binary_returns_false_without_raising(recorded_commands: RecordedCommands) -> None:
    recorded_commands.missing("notify-send")

    result = notify.send(TITLE, NOTE_ID)

    assert not result
    assert result.error == "notify-send is not installed or not on PATH"


def test_send_timeout_returns_false_without_raising(recorded_commands: RecordedCommands) -> None:
    def hang(command: tuple[str, ...]) -> CommandResult:
        raise CommandTimeout(command, notify.TIMEOUT)

    recorded_commands.respond("notify-send", using=hang)

    assert notify.send(TITLE, NOTE_ID) == NotifyResult(False, "notify-send timed out after 10 seconds")


def test_play_sound_runs_canberra_gtk_play(recorded_commands: RecordedCommands) -> None:
    result = notify.play_sound("message-new-instant")

    assert result == NotifyResult(True)
    assert recorded_commands.commands() == [("canberra-gtk-play", "-i", "message-new-instant")]
    assert recorded_commands.calls[0].timeout == notify.TIMEOUT


def test_play_sound_failure_returns_false_with_the_last_stderr_line(recorded_commands: RecordedCommands) -> None:
    recorded_commands.script(
        "canberra-gtk-play",
        returncode=1,
        stderr="Option parsing failed: Cannot open display:\n\nFailed to play sound\n",
    )

    result = notify.play_sound("message-new-instant")

    assert not result
    assert result.error == "canberra-gtk-play failed: Failed to play sound"


def test_play_sound_missing_binary_returns_false(recorded_commands: RecordedCommands) -> None:
    recorded_commands.missing("canberra-gtk-play")

    assert notify.play_sound("bell") == NotifyResult(False, "canberra-gtk-play is not installed or not on PATH")
