"""Slugs and dated filenames: every note is named `YYYY-MM-DD-<slug>.md`,
so its creation date is derivable from its path.
"""

import re
import unicodedata
from datetime import date
from pathlib import Path, PurePosixPath

from notes.vault import NOTES_DIR_NAME, notes_dir

DATED_FILENAME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-(.+)\.md$")
SHORT_NAME_WORDS = 7

# Anything that is not a Unicode letter or digit: `\w` covers letters, digits, and the underscore,
# and the underscore is spelled out so it becomes a separator like every other punctuation mark.
_SEPARATOR_RUN_RE = re.compile(r"[\W_]+")


def slugify(text: str) -> str:
    """Turn free text into a filename slug: lowercase, letters and digits of any script, everything else becomes `-`.

    `Project Atlas task updates over Kafka` becomes `project-atlas-task-updates-over-kafka`,
    and `Обновления задач через Kafka` becomes `обновления-задач-через-kafka`.
    Text is NFC-normalized first so a decomposed accent or Cyrillic breve is not split off as a separator.
    Text that leaves no letter or digit behind raises `ValueError`.
    """
    normalized = unicodedata.normalize("NFC", text).lower()
    slug = _SEPARATOR_RUN_RE.sub("-", normalized).strip("-")
    if not slug:
        raise ValueError(f"`{text}` has no letters or digits to build a filename from")
    return slug


def suggest_short_name(title: str) -> str:
    """The default short name for a title: its first seven words, whitespace collapsed.

    The result is offered for editing in a terminal and slugified by `dated_filename` afterwards,
    so punctuation is kept here and the user sees the words they typed.
    """
    return " ".join(title.split()[:SHORT_NAME_WORDS])


def dated_filename(vault: Path, created: date, short_name: str) -> str:
    """The vault-relative ID of a new note: `notes/YYYY-MM-DD-<slug>.md`, made unique with `-2`, `-3`, ... on collision.

    Every entry under `notes/` with the candidate name counts as a collision, files and directories alike,
    so the returned path does not exist yet. It is the name a note is about to get, not a reservation: only
    `create_dated_note`, which claims the name as it writes, can promise that nothing else takes it meanwhile.
    """
    stem = f"{created.isoformat()}-{slugify(short_name)}"
    directory = notes_dir(vault)
    suffix = 1
    while (directory / _candidate(stem, suffix)).exists():
        suffix += 1
    return f"{NOTES_DIR_NAME}/{_candidate(stem, suffix)}"


def create_dated_note(vault: Path, created: date, short_name: str, content: bytes) -> str:
    """Create `notes/YYYY-MM-DD-<slug>.md` holding `content` and return its vault-relative ID.

    The name is claimed by the create itself (`O_EXCL`), and the next suffix is tried when the file is already
    there, so two processes creating a note from the same short name on the same day get two notes: the second one
    takes `-2` instead of overwriting the first. An existing directory counts as a collision as it does for
    `dated_filename`.
    """
    directory = notes_dir(vault)
    stem = f"{created.isoformat()}-{slugify(short_name)}"
    suffix = 1
    while True:
        candidate = _candidate(stem, suffix)
        try:
            with (directory / candidate).open("xb") as handle:
                handle.write(content)
        except FileExistsError:
            suffix += 1
            continue
        return f"{NOTES_DIR_NAME}/{candidate}"


def _candidate(stem: str, suffix: int) -> str:
    """The `suffix`-th candidate filename for `stem`: the plain name first, then `-2`, `-3`, ..."""
    return f"{stem}.md" if suffix == 1 else f"{stem}-{suffix}.md"


def creation_date_from_path(path: str) -> date:
    """The creation date encoded in a `YYYY-MM-DD-<slug>.md` filename.

    Only the final path component matters, so notes in subdirectories of `notes/` are fine.
    Any other shape, or a date that does not exist on the calendar, raises `ValueError` with a message naming the file.
    """
    name = PurePosixPath(path).name
    match = DATED_FILENAME_RE.match(name)
    if match is None:
        raise ValueError(f"filename `{name}` must look like YYYY-MM-DD-<slug>.md")
    year, month, day, _slug = match.groups()
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        raise ValueError(f"filename `{name}` has an impossible date {year}-{month}-{day}") from None
