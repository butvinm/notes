"""Packaged defaults: the files `notes init` installs into a fresh vault, and the version stamp that records them.

The package ships `config.toml`, the shared `prompt.md`, the vault `.gitignore` (as `gitignore`),
and a `prompt.md` plus `template.md` per kind under `defaults/`; `install` copies them into a vault once.
`DEFAULTS_VERSION` inside the package is bumped whenever a packaged prompt or template changes,
`notes init` records it in `<vault>/.defaults-version`,
and `notes check` says when the package carries a newer version.
The CLI never overwrites vault-owned prompts or templates after that; the user compares and merges them by hand.
"""

import re
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

from notes.vault import (
    PROMPT_NAME,
    TEMPLATE_NAME,
    config_path,
    kind_prompt_path,
    kind_template_path,
    notes_dir,
    shared_prompt_path,
)

VERSION_STAMP = ".defaults-version"
VERSION_FILE = "DEFAULTS_VERSION"
GITIGNORE_SOURCE = "gitignore"

_AUTO_PUSH_RE = re.compile(r"^auto_push = (?:true|false)$", re.MULTILINE)


def packaged_root() -> Traversable:
    """The `defaults/` directory inside the installed package."""
    return resources.files("notes").joinpath("defaults")


def packaged_text(*parts: str) -> str:
    """The content of a packaged file, named relative to `defaults/`."""
    return packaged_root().joinpath(*parts).read_text(encoding="utf-8")


def packaged_kinds() -> list[str]:
    """The kinds the package ships: subdirectories of `defaults/types/` with a `template.md`, sorted by name."""
    types = packaged_root().joinpath("types")
    return sorted(child.name for child in types.iterdir() if child.joinpath(TEMPLATE_NAME).is_file())


def packaged_version() -> int:
    """The version of the defaults this CLI ships."""
    return int(packaged_text(VERSION_FILE).strip())


def config_text(*, auto_push: bool) -> str:
    """The packaged `config.toml` with `auto_push` set as asked; every other line is verbatim."""
    value = "true" if auto_push else "false"
    text, count = _AUTO_PUSH_RE.subn(f"auto_push = {value}", packaged_text("config.toml"))
    if count != 1:
        raise RuntimeError("the packaged config.toml must carry exactly one `auto_push` line")
    return text


def install(vault: Path, *, auto_push: bool) -> list[Path]:
    """Write the packaged defaults into `vault` and stamp their version; returns every file written.

    Creates the vault directory and `notes/` when they are missing, so the result satisfies `vault.is_vault`.
    Meant for a fresh vault: existing files are overwritten, which is why `notes init` refuses an existing vault.
    """
    notes_dir(vault).mkdir(parents=True, exist_ok=True)
    files = {
        config_path(vault): config_text(auto_push=auto_push),
        shared_prompt_path(vault): packaged_text("prompt.md"),
        vault / ".gitignore": packaged_text(GITIGNORE_SOURCE),
    }
    for kind in packaged_kinds():
        files[kind_prompt_path(vault, kind)] = packaged_text("types", kind, PROMPT_NAME)
        files[kind_template_path(vault, kind)] = packaged_text("types", kind, TEMPLATE_NAME)
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    write_version_stamp(vault)
    return [*files, stamp_path(vault)]


def stamp_path(vault: Path) -> Path:
    return vault / VERSION_STAMP


def installed_version(vault: Path) -> int | None:
    """The version recorded in the vault, or None when the stamp is missing or unreadable."""
    path = stamp_path(vault)
    if not path.is_file():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def write_version_stamp(vault: Path) -> None:
    """Record the packaged version in the vault; `notes init` does this once."""
    stamp_path(vault).write_text(f"{packaged_version()}\n", encoding="utf-8")


def newer_defaults_available(vault: Path) -> bool:
    """Whether the package carries newer defaults than the vault recorded;
    a missing or unreadable stamp counts as older.
    """
    installed = installed_version(vault)
    return installed is None or installed < packaged_version()
