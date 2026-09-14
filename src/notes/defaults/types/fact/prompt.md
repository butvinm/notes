# Fact

A fact records something that is the case: a truth about a system, a process, or a person's preferences, or something that happened. It records where that knowledge came from, so it can be trusted or rechecked later.

## What to capture

- **Fact:** the statement itself, precise enough to act on: names, versions, limits, defaults, addresses, exact behavior. For something that happened, the occurrence in the past tense with the systems and people involved, and for an incident the symptom, the cause when known, and what was done. One fact per note; split unrelated facts.
- **Source:** where the fact was learned: a file and line, a document, a command and its output, a person, a message. Exact enough to verify again.
- **Context:** when and under which conditions it holds: the environment, the version, the configuration, the date it was observed or the date it happened, and what would make it stale. For something that happened, the consequences belong here too: follow-up work, new constraints, decisions taken, the tracker issues that track them.

## What is critical

The fact and its source. A fact without a source becomes a rumor after a few weeks; the assistant should ask where it came from before drafting when the packet does not say. For something that happened, the date is the source of its place on the timeline: ask for it when the packet gives none, and never infer a date the packet does not give.

## How to reason

- Prefer the exact wording from the source (a config key, an error message, a command) over a paraphrase, and quote it.
- Keep the statement factual; the interpretation belongs in a decision or an idea note.
- Put every name the fact might be searched by in `keywords`: the abbreviation, the full name, the internal nickname, and for an incident the affected systems, services, and error names, so the note is found when the same symptom appears again.
- Tag with the system or project the fact belongs to; add the tracker issue or the incident ID when the packet gives one.
- When the fact contradicts something the developer believed, say so plainly in the body.
- When what happened caused a decision or a reminder, say so in the body; the developer links the notes with `notes relate`.
