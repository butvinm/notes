"""Configuration: frozen dataclasses carrying the packaged defaults, loaded from `config.toml` with `tomllib`.

The CLI never writes `config.toml`; `notes init` copies the packaged template once and the user edits it by hand.
Unknown keys and sections are ignored, missing keys take the defaults below.
"""

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from notes.errors import UsageError
from notes.vault import config_path

DEFAULT_GENERATOR_COMMAND = (
    "codex",
    "exec",
    "--skip-git-repo-check",
    "--ephemeral",
    "--sandbox",
    "read-only",
    "--color",
    "never",
    "-",
)


@dataclass(frozen=True)
class GeneratorConfig:
    command: tuple[str, ...] = DEFAULT_GENERATOR_COMMAND
    timeout_seconds: float = 300


@dataclass(frozen=True)
class GitConfig:
    auto_push: bool = False
    remote: str = "origin"


@dataclass(frozen=True)
class NotificationsConfig:
    sound: bool = True
    sound_id: str = "message-new-instant"


@dataclass(frozen=True)
class RecallConfig:
    limit: int = 10
    min_score: float = 1.0
    path_boost: float = 2.0


@dataclass(frozen=True)
class Config:
    generator: GeneratorConfig = GeneratorConfig()
    git: GitConfig = GitConfig()
    notifications: NotificationsConfig = NotificationsConfig()
    recall: RecallConfig = RecallConfig()


DEFAULTS = Config()


def load(vault: Path) -> Config:
    """Read `<vault>/config.toml`; a missing file yields the defaults, unreadable TOML is a `UsageError`."""
    path = config_path(vault)
    if not path.is_file():
        return DEFAULTS
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise UsageError(f"{path}: invalid TOML: {error}") from error
    return from_mapping(data, source=str(path))


def from_mapping(data: Mapping[str, Any], *, source: str = "config.toml") -> Config:
    """Build a `Config` from parsed TOML data, checking the type of every known key."""
    generator = _Section(data, "generator", source)
    git = _Section(data, "git", source)
    notifications = _Section(data, "notifications", source)
    recall = _Section(data, "recall", source)
    return Config(
        generator=GeneratorConfig(
            command=generator.command_list("command", DEFAULTS.generator.command),
            timeout_seconds=generator.number("timeout_seconds", DEFAULTS.generator.timeout_seconds),
        ),
        git=GitConfig(
            auto_push=git.boolean("auto_push", DEFAULTS.git.auto_push),
            remote=git.string("remote", DEFAULTS.git.remote),
        ),
        notifications=NotificationsConfig(
            sound=notifications.boolean("sound", DEFAULTS.notifications.sound),
            sound_id=notifications.string("sound_id", DEFAULTS.notifications.sound_id),
        ),
        recall=RecallConfig(
            limit=recall.positive_integer("limit", DEFAULTS.recall.limit),
            min_score=recall.number("min_score", DEFAULTS.recall.min_score),
            path_boost=recall.number("path_boost", DEFAULTS.recall.path_boost),
        ),
    )


class _Section:
    """Typed access to one TOML table; a table that is missing or not a table behaves as empty."""

    def __init__(self, data: Mapping[str, Any], name: str, source: str) -> None:
        table = data.get(name)
        self.table: Mapping[str, Any] = table if isinstance(table, Mapping) else {}
        self.name = name
        self.source = source

    def _error(self, key: str, expected: str) -> UsageError:
        return UsageError(f"{self.source}: [{self.name}] {key} must be {expected}")

    def boolean(self, key: str, default: bool) -> bool:
        value = self.table.get(key, default)
        if not isinstance(value, bool):
            raise self._error(key, "true or false")
        return value

    def string(self, key: str, default: str) -> str:
        value = self.table.get(key, default)
        if not isinstance(value, str):
            raise self._error(key, "a string")
        return value

    def integer(self, key: str, default: int) -> int:
        value = self.table.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise self._error(key, "an integer")
        return value

    def positive_integer(self, key: str, default: int) -> int:
        """A count that has to be at least 1: zero or a negative number would be read as a slice, not as a cap."""
        value = self.integer(key, default)
        if value < 1:
            raise self._error(key, "a positive integer")
        return value

    def number(self, key: str, default: float) -> float:
        value = self.table.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise self._error(key, "a number")
        return float(value)

    def command_list(self, key: str, default: tuple[str, ...]) -> tuple[str, ...]:
        value = self.table.get(key, default)
        if not isinstance(value, list | tuple) or not value or not all(isinstance(item, str) for item in value):
            raise self._error(key, "a non-empty list of strings")
        return tuple(value)
