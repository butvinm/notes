"""Vault location, layout, and note ID resolution: one vault at `~/.notes`, resolved by `root()` alone."""

from pathlib import Path

from notes.errors import NoVaultError, UsageError

VAULT_DIR_NAME = ".notes"
NOTES_DIR_NAME = "notes"
PROMPT_NAME = "prompt.md"
TEMPLATE_NAME = "template.md"


def root() -> Path:
    """The single vault location; tests redirect it by setting `HOME`."""
    return Path.home() / VAULT_DIR_NAME


def notes_dir(vault: Path) -> Path:
    return vault / NOTES_DIR_NAME


def types_dir(vault: Path) -> Path:
    return vault / "types"


def drafts_dir(vault: Path) -> Path:
    return vault / "drafts"


def db_path(vault: Path) -> Path:
    return vault / "index.sqlite"


def config_path(vault: Path) -> Path:
    return vault / "config.toml"


def shared_prompt_path(vault: Path) -> Path:
    return vault / PROMPT_NAME


def kind_dir(vault: Path, kind: str) -> Path:
    return types_dir(vault) / kind


def kind_prompt_path(vault: Path, kind: str) -> Path:
    """`types/<kind>/prompt.md`: what the generator should capture for the kind; optional for a user-added kind."""
    return kind_dir(vault, kind) / PROMPT_NAME


def kind_template_path(vault: Path, kind: str) -> Path:
    """`types/<kind>/template.md`: the scaffold `notes new` fills in; its presence is what makes a kind known."""
    return kind_dir(vault, kind) / TEMPLATE_NAME


def is_vault(path: Path) -> bool:
    """A directory is a vault when it contains `config.toml` and a `notes/` directory."""
    return config_path(path).is_file() and notes_dir(path).is_dir()


def require_vault() -> Path:
    """The vault at `~/.notes`, or `NoVaultError` telling the user to run `notes init`."""
    vault = root()
    if not is_vault(vault):
        raise NoVaultError(f"no vault at ~/{VAULT_DIR_NAME}, run `notes init`")
    return vault


def known_kinds(vault: Path) -> list[str]:
    """The valid kinds: subdirectories of `types/` that contain `template.md`, sorted by name."""
    types = types_dir(vault)
    if not types.is_dir():
        return []
    return sorted(child.name for child in types.iterdir() if (child / TEMPLATE_NAME).is_file())


def canonical_id(vault: Path, text: str) -> str:
    """The canonical vault-relative path behind any accepted spelling of a note ID, whether or not the file exists.

    Accepted forms: a vault-relative path (`notes/2026-09-02-x.md`),
    an absolute path inside the vault (`~` is expanded),
    and a name relative to `notes/` such as a bare filename (`2026-09-02-x.md`).
    Anything that does not land under `notes/` is a `UsageError`. `notes move` uses this for the destination;
    `resolve_id` adds the check that the file exists.
    """
    if not text.strip():
        raise UsageError("empty note id")
    given = Path(text).expanduser()
    if given.is_absolute():
        candidate = given
    elif given.parts and given.parts[0] == NOTES_DIR_NAME:
        candidate = vault / given
    else:
        candidate = notes_dir(vault) / given
    resolved = candidate.resolve()
    try:
        relative = resolved.relative_to(notes_dir(vault).resolve())
    except ValueError:
        relative = None
    if relative is None or relative == Path("."):
        raise UsageError(f"{text}: not a path under {NOTES_DIR_NAME}/ in the vault")
    return (Path(NOTES_DIR_NAME) / relative).as_posix()


def resolve_id(vault: Path, text: str) -> str:
    """Turn any accepted spelling of a note ID (see `canonical_id`) into the canonical vault-relative path.

    The file must exist under `notes/`; anything else is a `UsageError`.
    """
    canonical = canonical_id(vault, text)
    if not (vault / canonical).is_file():
        raise UsageError(f"no note at {canonical}")
    return canonical
