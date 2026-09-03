"""Tests for configuration loading: packaged defaults, overrides, ignored unknown keys, and type checks."""

import tomllib
from importlib import resources
from pathlib import Path

import pytest

from notes import config
from notes.config import Config, GeneratorConfig, GitConfig, NotificationsConfig, RecallConfig
from notes.errors import UsageError


def write_config(directory: Path, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_dataclass_defaults_match_technical_details() -> None:
    assert Config() == Config(
        generator=GeneratorConfig(
            command=(
                "codex",
                "exec",
                "--skip-git-repo-check",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--color",
                "never",
                "-",
            ),
            timeout_seconds=300,
        ),
        git=GitConfig(auto_push=False, remote="origin"),
        notifications=NotificationsConfig(sound=True, sound_id="message-new-instant"),
        recall=RecallConfig(limit=10, min_score=1.0, path_boost=2.0),
    )


def test_packaged_defaults_match_dataclass_defaults() -> None:
    text = resources.files("notes").joinpath("defaults/config.toml").read_text(encoding="utf-8")

    assert config.from_mapping(tomllib.loads(text)) == Config()


def test_load_from_bare_vault(bare_vault: Path) -> None:
    assert config.load(bare_vault) == Config()


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    assert config.load(tmp_path) == Config()


def test_empty_file_gives_defaults(tmp_path: Path) -> None:
    write_config(tmp_path, "")

    assert config.load(tmp_path) == Config()


def test_every_key_can_be_overridden(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        """
[generator]
command = ["my-generator", "--fast"]
timeout_seconds = 30

[git]
auto_push = true
remote = "backup"

[notifications]
sound = false
sound_id = "bell"

[recall]
limit = 3
min_score = 0.5
path_boost = 4
""",
    )

    loaded = config.load(tmp_path)

    assert loaded == Config(
        generator=GeneratorConfig(command=("my-generator", "--fast"), timeout_seconds=30),
        git=GitConfig(auto_push=True, remote="backup"),
        notifications=NotificationsConfig(sound=False, sound_id="bell"),
        recall=RecallConfig(limit=3, min_score=0.5, path_boost=4.0),
    )
    assert isinstance(loaded.recall.path_boost, float)
    assert isinstance(loaded.generator.timeout_seconds, float)


def test_partial_section_keeps_other_defaults(tmp_path: Path) -> None:
    write_config(tmp_path, "[git]\nauto_push = true\n")

    loaded = config.load(tmp_path)

    assert loaded.git == GitConfig(auto_push=True, remote="origin")
    assert loaded.generator == GeneratorConfig()
    assert loaded.recall == RecallConfig()


def test_unknown_keys_and_sections_are_ignored(tmp_path: Path) -> None:
    write_config(tmp_path, '[recall]\nlimit = 5\ncolour = "blue"\n\n[future]\nenabled = true\n')

    assert config.load(tmp_path).recall == RecallConfig(limit=5)


def test_section_that_is_not_a_table_is_ignored(tmp_path: Path) -> None:
    write_config(tmp_path, "recall = 5\n")

    assert config.load(tmp_path) == Config()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('[recall]\nlimit = "ten"\n', "[recall] limit must be an integer"),
        ("[recall]\nlimit = 2.5\n", "[recall] limit must be an integer"),
        ("[recall]\nlimit = true\n", "[recall] limit must be an integer"),
        # A cap of zero or less would be read as a slice from the end and print nearly every note instead of none.
        ("[recall]\nlimit = 0\n", "[recall] limit must be a positive integer"),
        ("[recall]\nlimit = -1\n", "[recall] limit must be a positive integer"),
        ("[recall]\nmin_score = true\n", "[recall] min_score must be a number"),
        ('[recall]\npath_boost = "high"\n', "[recall] path_boost must be a number"),
        ('[git]\nauto_push = "yes"\n', "[git] auto_push must be true or false"),
        ("[git]\nremote = 1\n", "[git] remote must be a string"),
        ('[generator]\ncommand = "codex exec"\n', "[generator] command must be a non-empty list of strings"),
        ("[generator]\ncommand = []\n", "[generator] command must be a non-empty list of strings"),
        ('[generator]\ncommand = ["codex", 1]\n', "[generator] command must be a non-empty list of strings"),
        ('[generator]\ntimeout_seconds = "5m"\n', "[generator] timeout_seconds must be a number"),
        ("[notifications]\nsound = 1\n", "[notifications] sound must be true or false"),
    ],
)
def test_wrong_types_are_rejected(tmp_path: Path, text: str, expected: str) -> None:
    path = write_config(tmp_path, text)

    with pytest.raises(UsageError) as info:
        config.load(tmp_path)

    assert info.value.message == f"{path}: {expected}"


def test_invalid_toml_is_rejected(tmp_path: Path) -> None:
    path = write_config(tmp_path, "[recall\nlimit = 1\n")

    with pytest.raises(UsageError, match="invalid TOML") as info:
        config.load(tmp_path)

    assert info.value.message.startswith(f"{path}: ")
