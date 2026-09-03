"""Relations between notes, written into the frontmatter of the note they start from.

`add_relation` is what `notes relate` does to the file: after checking the relation name, that both notes exist,
that the note is not related to itself, and that the same relation is not there already, it appends a
`{relation, note}` entry to the `related` list through the round-trip rewriter, creating the key when the note has
none. The `note` reference is written relative to the source file, as the note format asks, and everything else in
the file, flow lists, comments, and the body, is left as the user formatted it. Every refusal is raised before the
file is touched.

`retarget` is the file side of `notes move`: once a note has been renamed, the references that the rename made
stale, the moved note's own and those of the notes pointing at it, are rewritten through the same rewriter.
"""

from collections.abc import Iterable
from pathlib import Path

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from notes import document
from notes.document import RELATIONS
from notes.errors import UsageError, ValidationFailed


def add_relation(vault: Path, src: str, relation: str, dst: str) -> str:
    """Append the relation from `src` to `dst` to the frontmatter of `src`; returns the reference it wrote.

    `src` and `dst` are canonical vault-relative IDs, as `vault.resolve_id` returns them. An unknown relation, a
    missing note on either side, a self-relation, and a relation the note already has (however its reference is
    spelled) are a `UsageError`; a `related` key that is not a list is a `ValidationFailed` pointing at `notes check`.
    """
    if relation not in RELATIONS:
        raise UsageError(f"unknown relation `{relation}` (expected supersedes, child, or related)")
    for path in (src, dst):
        if not (vault / path).is_file():
            raise UsageError(f"no note at {path}")
    if src == dst:
        raise UsageError("a note cannot relate to itself")
    reference = document.relative_target(src, dst)

    def mutate(mapping: CommentedMap) -> bool:
        related = mapping.get("related")
        if related is None:
            related = CommentedSeq()
            mapping["related"] = related
        elif not isinstance(related, list):
            raise ValidationFailed(f"{src}: related must be a list of `relation` and `note` pairs; run `notes check`")
        if any(item.relation == relation and item.target_path == dst for item in document.relations_in(src, related)):
            raise UsageError(f"duplicate relation: {src} already has `{relation} {reference}`")
        related.append(CommentedMap([("relation", relation), ("note", reference)]))
        return True

    document.rewrite_frontmatter(vault / src, mutate)
    return reference


def retarget(vault: Path, old: str, new: str, referrers: Iterable[str] = ()) -> list[str]:
    """After the note at `old` was renamed to `new`, rewrite the `note:` references the rename made stale; returns
    the vault-relative paths of the files it wrote, the moved note first.

    The moved note's references were written relative to its old directory, so every well-formed one is expressed
    again relative to `new` (a reference to the note itself follows the move). Each of the `referrers`, the notes the
    index knows to point at `old`, gets its references to `old` re-pointed at `new`, relative to its own directory;
    nothing else in those files changes. A file that cannot be rewritten (missing, not UTF-8, no loadable
    frontmatter) is skipped: it is not a valid note, and `notes check` reports it.
    """
    rewritten = []
    if _retarget(vault, location=new, origin=old, old=old, new=new):
        rewritten.append(new)
    for referrer in sorted(set(referrers) - {old, new}):
        if _retarget(vault, location=referrer, origin=referrer, old=old, new=new):
            rewritten.append(referrer)
    return rewritten


def _retarget(vault: Path, *, location: str, origin: str, old: str, new: str) -> bool:
    """Rewrite the references in the file at `location`, which were written for a note at `origin`."""
    moved = location != origin

    def mutate(mapping: CommentedMap) -> bool:
        related = mapping.get("related")
        if not isinstance(related, list):
            return False
        changed = False
        for item in related:
            if not isinstance(item, dict):
                continue
            note = item.get("note")
            if not isinstance(note, str) or not note.strip():
                continue
            try:
                target = document.resolve_target(origin, note)
            except ValueError:
                continue
            if target == old:
                target = new
            elif not moved:
                continue
            reference = document.relative_target(location, target)
            if reference != note:
                item["note"] = reference
                changed = True
        return changed

    try:
        return document.rewrite_frontmatter(vault / location, mutate)
    except (ValidationFailed, OSError, UnicodeDecodeError):
        return False
