# Reminder

A reminder records something the developer wants to be told at a moment or on a rhythm: a check to run, a review to revisit, a routine. It is delivered as a desktop notification and stays unread until the developer marks it read.

## What to capture

- **Remind me:** the action to take when the reminder fires, written so it can be started without rereading the conversation: what to do, where, and what a good outcome looks like.
- **Why:** the reason the reminder exists and what happens if it is skipped; what the developer was worried about when asking for it.
- The moment or the rhythm, as the packet gives it, in the `schedule` field.

## What is critical

The action and the schedule. Ask for the moment or the rhythm before drafting when the packet gives none; a reminder without a schedule is invalid.

## How to reason

- `schedule` is required: `at <timestamp>` for a single moment, `every <N> <minutes|hours|days|weeks> from <timestamp>` for a rhythm. Copy a relative form from the packet as given (`tomorrow 09:00`, `every 2 weeks`); never compute a date yourself.
- Prefer a single moment over a rhythm unless the packet clearly asks for repetition; a recurring reminder keeps firing until the note is archived.
- Tag with the project and the tracker issue when the packet gives them, and reuse the tags of the note the reminder is about.
- When the reminder is about revisiting a decision or a promise, name it in the body.
