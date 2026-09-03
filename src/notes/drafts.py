"""The draft store: what `notes draft` keeps under `<vault>/drafts/` between generation and confirmation.

A draft is a directory `drafts/<draft-id>/` holding `draft.json` (the id, kind, short name, timestamps, and the
feedback of every revision), `context.json` (the packet the generator received), and `draft.md` (the note as the
generator last wrote it). Drafts never take part in search or recall and never reach Git: `drafts/` lies outside
`notes/`, so sync never scans it, and the packaged `.gitignore` excludes it. `notes draft save` turns a draft into
a note and deletes it; `notes draft discard` deletes it as it is. User confirmation is the trust boundary.
"""

import json
import shutil
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from notes import schedule as schedules
from notes.errors import UsageError
from notes.vault import drafts_dir

META_FILE = "draft.json"
CONTEXT_FILE = "context.json"
MARKDOWN_FILE = "draft.md"

_ID_TIME_FORMAT = "%Y%m%d-%H%M%S"


@dataclass(frozen=True)
class Draft:
    """The metadata of one draft, as `draft.json` stores it; `revisions` is the feedback of every revision, in order."""

    id: str
    kind: str
    short_name: str
    created_at: str
    updated_at: str
    revisions: tuple[str, ...]


def draft_dir(vault: Path, draft_id: str) -> Path:
    """`drafts/<draft-id>/`; a spelling that is not a single path segment is a `UsageError`."""
    return drafts_dir(vault) / _check_id(draft_id)


def markdown_path(vault: Path, draft_id: str) -> Path:
    return draft_dir(vault, draft_id) / MARKDOWN_FILE


def context_path(vault: Path, draft_id: str) -> Path:
    return draft_dir(vault, draft_id) / CONTEXT_FILE


def relative_markdown_path(vault: Path, draft_id: str) -> str:
    """The vault-relative path of the draft's Markdown, the shape error lines and JSON use next to the absolute one."""
    return markdown_path(vault, draft_id).relative_to(vault).as_posix()


def new_id(vault: Path, kind: str, now: datetime) -> str:
    """`<YYYYMMDD-HHMMSS>-<kind>` for a draft made at `now`, with `-2`, `-3`, ... when that directory exists."""
    stem = f"{now.strftime(_ID_TIME_FORMAT)}-{kind}"
    candidate = stem
    suffix = 2
    while draft_dir(vault, candidate).exists():
        candidate = f"{stem}-{suffix}"
        suffix += 1
    return candidate


def create(vault: Path, kind: str, short_name: str, markdown: str, context: Mapping[str, Any], now: datetime) -> Draft:
    """Store a fresh draft: its directory with the three files; nothing is left behind when a write fails."""
    draft_id = new_id(vault, kind, now)
    stamp = schedules.format_timestamp(now)
    draft = Draft(draft_id, kind, short_name, stamp, stamp, ())
    directory = draft_dir(vault, draft_id)
    directory.mkdir(parents=True)
    try:
        _write_json(context_path(vault, draft_id), dict(context))
        markdown_path(vault, draft_id).write_text(markdown, encoding="utf-8")
        _write_meta(vault, draft)
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return draft


def load(vault: Path, draft_id: str) -> Draft:
    """The draft stored under `draft_id`; an unknown id or an unreadable `draft.json` is a `UsageError`."""
    path = draft_dir(vault, draft_id) / META_FILE
    if not path.is_file():
        raise _not_found(draft_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise UsageError(f"draft `{draft_id}` is unreadable: {path}: {error}") from None
    return _from_data(draft_id, data, path)


def list_all(vault: Path) -> list[Draft]:
    """Every stored draft in id order, which is creation order; entries without a `draft.json` are not drafts."""
    directory = drafts_dir(vault)
    if not directory.is_dir():
        return []
    return [load(vault, child.name) for child in sorted(directory.iterdir()) if (child / META_FILE).is_file()]


def read_markdown(vault: Path, draft_id: str) -> str:
    return markdown_path(vault, draft_id).read_text(encoding="utf-8")


def read_context(vault: Path, draft_id: str) -> dict[str, Any]:
    """The packet the generator received, for a revision to receive it again."""
    path = context_path(vault, draft_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise UsageError(f"draft `{draft_id}` is unreadable: {path}: {error}") from None
    if not isinstance(data, dict):
        raise UsageError(f"draft `{draft_id}` is unreadable: {path}: the context packet is not a JSON object")
    return data


def save_markdown(
    vault: Path,
    draft: Draft,
    markdown: str,
    *,
    now: datetime,
    short_name: str | None = None,
    feedback: str | None = None,
) -> Draft:
    """Replace the draft's Markdown and record the revision: the feedback is appended, `updated_at` stamped with
    `now`, and the short name replaced when a new one is given. Returns the updated draft."""
    revisions = draft.revisions if feedback is None else (*draft.revisions, feedback)
    updated = replace(
        draft,
        short_name=short_name or draft.short_name,
        updated_at=schedules.format_timestamp(now),
        revisions=revisions,
    )
    markdown_path(vault, draft.id).write_text(markdown, encoding="utf-8")
    _write_meta(vault, updated)
    return updated


def delete(vault: Path, draft_id: str) -> None:
    """Remove the draft's directory with everything in it; an unknown id is a `UsageError`."""
    directory = draft_dir(vault, draft_id)
    if not (directory / META_FILE).is_file():
        raise _not_found(draft_id)
    shutil.rmtree(directory)


def _check_id(draft_id: str) -> str:
    if not draft_id or draft_id in (".", "..") or "/" in draft_id or "\\" in draft_id:
        raise _not_found(draft_id)
    return draft_id


def _not_found(draft_id: str) -> UsageError:
    return UsageError(f"no draft `{draft_id}` (see `notes draft list`)")


def _write_meta(vault: Path, draft: Draft) -> None:
    _write_json(draft_dir(vault, draft.id) / META_FILE, asdict(draft) | {"revisions": list(draft.revisions)})


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _from_data(draft_id: str, data: Any, path: Path) -> Draft:
    """The draft behind a loaded `draft.json`; the directory name is the id whatever the file says."""
    if not isinstance(data, dict):
        raise UsageError(f"draft `{draft_id}` is unreadable: {path}: not a JSON object")
    kind, short_name = data.get("kind"), data.get("short_name")
    if not isinstance(kind, str) or not kind or not isinstance(short_name, str) or not short_name:
        raise UsageError(f"draft `{draft_id}` is unreadable: {path}: `kind` and `short_name` must be non-empty strings")
    revisions = data.get("revisions") or []
    if not isinstance(revisions, list) or not all(isinstance(item, str) for item in revisions):
        raise UsageError(f"draft `{draft_id}` is unreadable: {path}: `revisions` must be a list of strings")
    return Draft(
        id=draft_id,
        kind=kind,
        short_name=short_name,
        created_at=_string(data.get("created_at")),
        updated_at=_string(data.get("updated_at")),
        revisions=tuple(revisions),
    )


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""
