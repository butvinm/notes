# Reminder

A reminder records something the developer wants to be told at a moment or on a rhythm: a check to run, a review to revisit, a routine, or a commitment that comes due. It is delivered as a desktop notification and stays unread until the developer marks it read.

## What to capture

- **Remind me:** the action to take when the reminder fires, written so it can be started without rereading the conversation: what to do, where, and what a good outcome looks like. When the action is owed to or by someone, name that person and what "done" looks like for them: the deliverable (a review, a reply, a release, a document) and its recipient.
- **Why:** the reason the reminder exists and what happens if it is skipped; what the developer was worried about when asking for it. For a commitment, where it was made (a meeting, a thread, an issue) and what depends on it.
- The moment or the rhythm, as the packet gives it, in the `schedule` field.

## What is critical

The action and the schedule. Ask for the moment or the rhythm before drafting when the packet gives none; a reminder without a schedule is invalid.

## How to reason

- `schedule` is required: `at <timestamp>` for a single moment, `every <N> <minutes|hours|days|weeks> from <timestamp>` for a rhythm. Copy a relative form from the packet as given (`tomorrow 09:00`, `every 2 weeks`); never compute a date yourself.
- Prefer a single moment over a rhythm unless the packet clearly asks for repetition; a recurring reminder keeps firing until the note is archived. A commitment with a due date is always a single moment, never a rhythm.
- Tag with the project and the tracker issue when the packet gives them, with the other person's name when the reminder is a commitment, and reuse the tags of the note the reminder is about.
- When the reminder is about revisiting a decision or a conversation that is already a note, name it in the body.
