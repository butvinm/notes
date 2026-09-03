# Event

An event records something that happened: an incident, a release, a migration, a meeting outcome, a change in the team. It exists so the timeline can be reconstructed later and the consequences are not forgotten.

## What to capture

- **What happened:** the event itself, in the past tense, with the systems and people involved. For an incident: the symptom, the cause when known, and what was done.
- **When:** the date, and the time when it matters. Use the packet's wording; do not infer a date the packet does not give.
- **Consequences:** what changed as a result: follow-up work, new constraints, decisions taken, promises made. Point at the tracker issues that track the follow-ups.

## What is critical

What happened and when. Without a date the event cannot be placed on the timeline; the assistant should ask before drafting when the packet gives none.

## How to reason

- Keep the description factual; the interpretation belongs in a decision or an idea note.
- Put the affected systems, services, and error names in `keywords`, so the note is found when the same symptom appears again.
- Tag with the project and the incident or issue ID when the packet gives them.
- When the event caused a decision or a promise, say so in the body; the developer links the notes with `notes relate`.
