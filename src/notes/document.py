"""Note documents: frontmatter and H1 parsing, validation, round-trip frontmatter rewriting, template rendering.

A note is a Markdown file whose frontmatter sits between a `---` line at the very start and the next `---` line;
the body is everything after that, and the title is the single `# ` heading outside fenced code blocks.
`parse` never raises: a missing or malformed frontmatter becomes a validation error,
so that sync can record an invalid file together with its errors instead of failing.
Line numbers in errors are 1-based file lines,
taken from `ruamel` node positions for frontmatter and from the scan for headings.
"""

import hashlib
import io
import os
import posixpath
import re
import shutil
import tempfile
from collections.abc import Callable, Collection, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError

from notes import schedule as schedules
from notes.errors import ValidationFailed
from notes.slug import creation_date_from_path
from notes.vault import NOTES_DIR_NAME

FENCE = "---"
STATUSES = ("active", "archived")
RELATIONS = ("supersedes", "child", "related")
LIST_FIELDS = ("paths", "tags", "keywords", "references")
SCHEDULE_REQUIRED_KINDS = ("reminder",)

# Lines of the file taken by the opening fence: YAML line 0 is file line 2.
_YAML_LINE_OFFSET = 2
_CODE_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_CLOSING_HASHES_RE = re.compile(r"\s+#+\s*$")


@dataclass(frozen=True)
class ValidationError:
    """One problem in a note; `line` is None when the problem is not tied to a line, such as a bad filename."""

    line: int | None
    message: str


@dataclass(frozen=True)
class Relation:
    """An outgoing relation; `target_path` is vault-relative even though the file stores it relative to the note."""

    relation: str
    target_path: str


@dataclass(frozen=True)
class Heading:
    line: int
    text: str


@dataclass(frozen=True)
class Note:
    """The typed record of a valid note, as the index stores it."""

    path: str
    kind: str
    status: str
    title: str
    body: str
    paths: tuple[str, ...]
    tags: tuple[str, ...]
    keywords: tuple[str, ...]
    references: tuple[str, ...]
    schedule: str | None
    related: tuple[Relation, ...]
    content_hash: str
    created_date: date


@dataclass(frozen=True)
class Document:
    """A parsed note file: the raw frontmatter mapping with its source positions, the body, and the H1 headings.

    `validate` reads the raw pieces and reports every problem; `to_note` turns a validated document into a `Note`.
    `frontmatter` is None when the block is missing, malformed, or not a mapping, and `problems` says why.
    """

    path: str
    frontmatter: CommentedMap | None
    body: str
    body_start: int
    headings: tuple[Heading, ...]
    content_hash: str
    problems: tuple[ValidationError, ...]

    @property
    def title(self) -> str | None:
        return self.headings[0].text if self.headings else None

    def get(self, key: str) -> Any:
        return None if self.frontmatter is None else self.frontmatter.get(key)

    def to_note(self) -> Note:
        """Build the typed note; call it after `validate` reported no errors.

        A document without frontmatter, without a title, or without a dated filename raises `ValidationFailed`,
        because those cannot be represented; any other invalid field is simply dropped.
        """
        frontmatter = self.frontmatter
        title = self.title
        if frontmatter is None or title is None:
            raise ValidationFailed(f"{self.path}: not a valid note, run `notes check`")
        try:
            created = creation_date_from_path(self.path)
        except ValueError as error:
            raise ValidationFailed(f"{self.path}: {error}") from None
        return Note(
            path=self.path,
            kind=_string_or_empty(frontmatter.get("kind")),
            status=_string_or_empty(frontmatter.get("status")),
            title=title,
            body=self.body,
            paths=_string_items(frontmatter.get("paths")),
            tags=_string_items(frontmatter.get("tags")),
            keywords=_string_items(frontmatter.get("keywords")),
            references=_string_items(frontmatter.get("references")),
            schedule=_canonical_schedule(frontmatter.get("schedule")),
            related=tuple(relations_in(self.path, frontmatter.get("related"))),
            content_hash=self.content_hash,
            created_date=created,
        )


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse(path: str, text: str) -> Document:
    """Split `text` into frontmatter and body and find the H1 headings; `path` is the note's vault-relative path."""
    lines = _split_lines(text)
    problems: list[ValidationError] = []
    frontmatter: CommentedMap | None = None
    body = text
    body_start = 1
    if lines and _is_fence(lines[0]):
        close = _closing_fence(lines)
        if close is None:
            problems.append(ValidationError(1, "frontmatter is not closed by a `---` line"))
            body = ""
            body_start = len(lines) + 1
        else:
            frontmatter, problems = _load_frontmatter("".join(lines[1:close]))
            body = "".join(lines[close + 1 :])
            body_start = close + 2
    else:
        problems.append(ValidationError(1, "missing frontmatter: the file must start with a `---` line"))
    headings = tuple(_h1_headings(body, body_start))
    return Document(path, frontmatter, body, body_start, headings, content_hash(text), tuple(problems))


def load(vault: Path, path: str) -> Document:
    """Parse the note at the vault-relative `path`, read as bytes so that line endings reach the hash untranslated."""
    return parse(path, (vault / path).read_bytes().decode("utf-8"))


def validate(document: Document, known_kinds: Collection[str], vault: Path) -> list[ValidationError]:
    """Every problem that makes the document an invalid note, ordered by line; an empty list means valid.

    Relation targets are checked against the files in `vault`, which is why validity can change when other files do.
    A schedule must follow the canonical grammar of `notes.schedule`, and a `reminder` must have one.
    """
    errors = list(document.problems)
    errors.extend(_filename_errors(document.path))
    errors.extend(_heading_errors(document))
    frontmatter = document.frontmatter
    if frontmatter is not None:
        errors.extend(_kind_errors(frontmatter, known_kinds))
        errors.extend(_status_errors(frontmatter))
        for field in LIST_FIELDS:
            errors.extend(_string_list_errors(frontmatter, field))
        errors.extend(_schedule_errors(frontmatter))
        errors.extend(_related_errors(frontmatter, document.path, vault))
    return sorted(errors, key=lambda error: error.line or 0)


def rewrite_frontmatter(path: Path, mutate: Callable[[CommentedMap], bool | None]) -> bool:
    """Apply `mutate` to the frontmatter mapping of the file at `path` and write it back.

    The body stays byte-for-byte identical and the untouched frontmatter keeps its formatting:
    flow lists stay flow lists, quotes and comments survive, and block sequences keep the file's indentation style.
    When `mutate` returns False the file is left untouched (not even rewritten), and the result says whether the
    file was written. A file without a loadable frontmatter mapping raises `ValidationFailed`.
    The rewrite goes through `write_atomically`, so an interrupted `relate`, `move`, or schedule normalization
    leaves the note as it was instead of truncating it.
    """
    text = path.read_bytes().decode("utf-8")
    lines = _split_lines(text)
    if not lines or not _is_fence(lines[0]):
        raise ValidationFailed(f"{path}: missing frontmatter, nothing to rewrite")
    close = _closing_fence(lines)
    if close is None:
        raise ValidationFailed(f"{path}: frontmatter is not closed by a `---` line")
    yaml_text = "".join(lines[1:close])
    mapping, problems = _load_frontmatter(yaml_text)
    if mapping is None:
        raise ValidationFailed(f"{path}: {problems[0].message}")
    if mutate(mapping) is False:
        return False
    sequence, offset = _guess_sequence_indent(yaml_text)
    rewritten = lines[0] + _dump(mapping, sequence, offset) + lines[close] + "".join(lines[close + 1 :])
    write_atomically(path, rewritten.encode("utf-8"))
    return True


def write_atomically(path: Path, data: bytes) -> None:
    """Replace the file at `path` with `data` in one step, so an interrupted rewrite cannot truncate a note.

    The replacement is written to a temporary file next to it, flushed to disk, given the mode of the file it
    replaces, and only then renamed over it: `os.replace` is atomic, so a reader sees either the old file or the
    new one and never a half-written note. A write that fails before the rename leaves the original untouched and
    takes the temporary file with it.
    """
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        shutil.copymode(path, temporary)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def normalize_schedule(path: Path, now: datetime) -> str | None:
    """Rewrite a `schedule` written in a relative or lenient form into canonical text; the new text, or None.

    `in 3 days`, `tomorrow 09:30`, a bare `2026-10-01` (which YAML loads as a date), and leniently written canonical
    forms all become canonical, resolved against `now` in its timezone. A schedule that is canonical already leaves
    the file untouched, and a value the grammar does not know, a missing key, or a file whose frontmatter cannot be
    loaded are left alone for validation to report.
    """
    canonical: str | None = None

    def mutate(mapping: CommentedMap) -> bool:
        nonlocal canonical
        value = mapping.get("schedule")
        text = _schedule_text(value)
        if text is None:
            return False
        try:
            normalized = schedules.normalize_input(text, now)
        except ValueError:
            return False
        if normalized == value:
            return False
        mapping["schedule"] = normalized
        canonical = normalized
        return True

    try:
        rewrite_frontmatter(path, mutate)
    except ValidationFailed:
        return None
    return canonical


def render_template(template_text: str, title: str, schedule: str | None = None) -> str:
    """Fill a kind template: plain replacement of `{title}` and `{schedule}`, nothing else is interpreted.

    Without a schedule the placeholder becomes empty, so a `schedule: {schedule}` line turns into a null `schedule:`,
    which validation then reports as missing for the kinds that require one.
    """
    return template_text.replace("{schedule}", schedule or "").replace("{title}", title)


def relative_target(from_path: str, target_path: str) -> str:
    """The `note:` reference to write in `from_path` for `target_path`: relative to the note's directory, never `./`."""
    return posixpath.relpath(target_path, posixpath.dirname(from_path))


def resolve_target(from_path: str, reference: str) -> str:
    """The vault-relative path a `note:` reference in `from_path` points at; `ValueError` when it leaves `notes/`."""
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(from_path), reference))
    if not resolved.startswith(NOTES_DIR_NAME + "/"):
        raise ValueError(f"relation target `{reference}` is outside {NOTES_DIR_NAME}/")
    return resolved


# Parsing helpers


def _split_lines(text: str) -> list[str]:
    """Lines with their `\\n` kept, split only on `\\n` so exotic separators inside prose never count as line breaks."""
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _is_fence(line: str) -> bool:
    return line.rstrip() == FENCE


def _closing_fence(lines: list[str]) -> int | None:
    return next((index for index in range(1, len(lines)) if _is_fence(lines[index])), None)


def _yaml() -> YAML:
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 1_000_000
    return yaml


def _load_frontmatter(yaml_text: str) -> tuple[CommentedMap | None, list[ValidationError]]:
    try:
        data = _yaml().load(yaml_text)
    except YAMLError as error:
        return None, [ValidationError(_yaml_error_line(error), f"invalid YAML in frontmatter: {_yaml_problem(error)}")]
    if data is None:
        return CommentedMap(), []
    if not isinstance(data, CommentedMap):
        return None, [ValidationError(_YAML_LINE_OFFSET, "frontmatter must be a mapping of keys to values")]
    return data, []


def _yaml_error_line(error: YAMLError) -> int:
    mark = getattr(error, "problem_mark", None) or getattr(error, "context_mark", None)
    line = getattr(mark, "line", None)
    return _YAML_LINE_OFFSET + (line if isinstance(line, int) else 0)


def _yaml_problem(error: YAMLError) -> str:
    problem = getattr(error, "problem", None)
    if isinstance(problem, str) and problem:
        return problem
    return str(error).splitlines()[0] if str(error) else type(error).__name__


def _h1_headings(body: str, body_start: int) -> Iterator[Heading]:
    fence: tuple[str, int] | None = None
    for offset, raw in enumerate(_split_lines(body)):
        line = raw.rstrip("\r\n")
        match = _CODE_FENCE_RE.match(line)
        if match:
            marker = match.group(1)
            if fence is None:
                fence = (marker[0], len(marker))
                continue
            if marker[0] == fence[0] and len(marker) >= fence[1] and not line[match.end() :].strip():
                fence = None
                continue
        if fence is None and line.startswith("# "):
            yield Heading(body_start + offset, _CLOSING_HASHES_RE.sub("", line[2:]).strip())


def _string_or_empty(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _string_items(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item.strip())


def _canonical_schedule(value: Any) -> str | None:
    """The schedule in canonical text, as the index stores it; None when absent or not parseable."""
    if not isinstance(value, str):
        return None
    try:
        return schedules.format(schedules.parse(value))
    except ValueError:
        return None


def _schedule_text(value: Any) -> str | None:
    """A `schedule` value as text `normalize_input` can read: a non-blank string, or what YAML made of a bare
    `2026-10-01` (a date) or a bare `2026-10-01T10:00:00+03:00` (an aware datetime, the `at` form minus its keyword).
    """
    if isinstance(value, str):
        return value if value.strip() else None
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return None
        return f"at {schedules.format_timestamp(value)}"
    if isinstance(value, date):
        return value.isoformat()
    return None


def relations_in(path: str, value: Any) -> Iterator[Relation]:
    """The well-formed relations in a raw `related` value of the note at `path`, with vault-relative targets.

    Items validation would reject (not a mapping, a missing or blank `note`, a reference leaving `notes/`) are
    skipped rather than reported; `validate` is where they become errors.
    """
    if not isinstance(value, list):
        return
    for item in value:
        if not isinstance(item, dict):
            continue
        relation, note = item.get("relation"), item.get("note")
        if not isinstance(relation, str) or not isinstance(note, str) or not note.strip():
            continue
        try:
            yield Relation(relation, resolve_target(path, note))
        except ValueError:
            continue


# Validation helpers


def _key_line(mapping: CommentedMap, key: str) -> int | None:
    try:
        return mapping.lc.key(key)[0] + _YAML_LINE_OFFSET
    except (KeyError, AttributeError, TypeError):
        return None


def _item_line(sequence: Any, index: int) -> int | None:
    try:
        return sequence.lc.item(index)[0] + _YAML_LINE_OFFSET
    except (KeyError, AttributeError, TypeError):
        return None


def _filename_errors(path: str) -> list[ValidationError]:
    try:
        creation_date_from_path(path)
    except ValueError as error:
        return [ValidationError(None, f"{error}; rename it with `notes move`")]
    return []


def _heading_errors(document: Document) -> list[ValidationError]:
    headings = document.headings
    if not headings:
        return [
            ValidationError(document.body_start, "missing H1 title: the body needs exactly one line starting with `# `")
        ]
    errors = [ValidationError(extra.line, "more than one H1: a note has exactly one title") for extra in headings[1:]]
    if not headings[0].text:
        errors.append(ValidationError(headings[0].line, "H1 title is empty"))
    return errors


def _kind_errors(frontmatter: CommentedMap, known_kinds: Collection[str]) -> list[ValidationError]:
    kind = frontmatter.get("kind")
    if kind is None:
        return [ValidationError(1, "kind is required")]
    if not isinstance(kind, str):
        return [ValidationError(_key_line(frontmatter, "kind"), "kind must be a string")]
    if kind not in known_kinds:
        known = ", ".join(sorted(known_kinds)) or "none installed under types/"
        return [ValidationError(_key_line(frontmatter, "kind"), f"unknown kind `{kind}` (known kinds: {known})")]
    return []


def _status_errors(frontmatter: CommentedMap) -> list[ValidationError]:
    status = frontmatter.get("status")
    if status is None:
        return [ValidationError(1, "status is required")]
    if status not in STATUSES:
        return [ValidationError(_key_line(frontmatter, "status"), "status must be active or archived")]
    return []


def _string_list_errors(frontmatter: CommentedMap, field: str) -> list[ValidationError]:
    value = frontmatter.get(field)
    if value is None:
        return []
    if not isinstance(value, list):
        return [ValidationError(_key_line(frontmatter, field), f"{field} must be a list of strings")]
    errors = []
    for index, item in enumerate(value):
        if isinstance(item, str) and item.strip():
            continue
        hint = "" if isinstance(item, str) else " (quote values that look like numbers or dates)"
        errors.append(ValidationError(_item_line(value, index), f"{field}[{index}] must be a non-empty string{hint}"))
    return errors


def schedule_problem(text: str) -> str | None:
    """Why `text` is not an acceptable schedule: the canonical grammar rejects it."""
    try:
        schedules.parse(text)
    except ValueError as error:
        return f"schedule: {error}"
    return None


def _schedule_errors(frontmatter: CommentedMap) -> list[ValidationError]:
    value = frontmatter.get("schedule")
    kind = frontmatter.get("kind")
    line = _key_line(frontmatter, "schedule")
    if value is None:
        if kind in SCHEDULE_REQUIRED_KINDS:
            return [ValidationError(line if "schedule" in frontmatter else 1, f"schedule is required for a {kind}")]
        return []
    if not isinstance(value, str) or not value.strip():
        return [ValidationError(line, "schedule must be a non-empty string")]
    problem = schedule_problem(value)
    return [ValidationError(line, problem)] if problem is not None else []


def _related_errors(frontmatter: CommentedMap, path: str, vault: Path) -> list[ValidationError]:
    value = frontmatter.get("related")
    if value is None:
        return []
    if not isinstance(value, list):
        return [
            ValidationError(_key_line(frontmatter, "related"), "related must be a list of `relation` and `note` pairs")
        ]
    errors: list[ValidationError] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(value):
        line = _item_line(value, index)
        message = _relation_problem(item, path, vault, seen)
        if message is not None:
            errors.append(ValidationError(line, f"related[{index}]: {message}"))
    return errors


def _relation_problem(item: Any, path: str, vault: Path, seen: set[tuple[str, str]]) -> str | None:
    if not isinstance(item, dict):
        return "must be a mapping with `relation` and `note`"
    relation, note = item.get("relation"), item.get("note")
    if relation is None:
        return "relation is required"
    if relation not in RELATIONS:
        return f"unknown relation `{relation}` (expected supersedes, child, or related)"
    if not isinstance(note, str) or not note.strip():
        return "note must be the path of another note, relative to this file"
    try:
        target = resolve_target(path, note)
    except ValueError as error:
        return str(error)
    if target == path:
        return "a note cannot relate to itself"
    if not (vault / target).is_file():
        return f"target `{note}` does not exist (resolved to {target})"
    if (relation, target) in seen:
        return f"duplicate relation `{relation} {note}`"
    seen.add((relation, target))
    return None


# Rewriting helpers


def _guess_sequence_indent(yaml_text: str) -> tuple[int, int]:
    """`(sequence, offset)` for `YAML.indent`, read from the first block sequence item in the text;
    the (4, 2) default matches the packaged templates when the text has no block sequence to learn from.
    """
    key_indent: int | None = None
    for line in yaml_text.splitlines():
        content = line.lstrip(" ")
        if not content.strip() or content.startswith("#"):
            continue
        indent = len(line) - len(content)
        if content.startswith("- ") or content.rstrip() == "-":
            if key_indent is None or indent < key_indent:
                break
            offset = indent - key_indent
            return offset + 2, offset
        key_indent = indent
    return 4, 2


def _dump(mapping: CommentedMap, sequence: int, offset: int) -> str:
    if not mapping:
        return ""
    yaml = _yaml()
    yaml.indent(mapping=2, sequence=sequence, offset=offset)
    buffer = io.StringIO()
    yaml.dump(mapping, buffer)
    return buffer.getvalue()
