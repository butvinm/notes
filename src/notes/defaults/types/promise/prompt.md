# Promise

A promise records a commitment with a due date: something the developer owes someone, or something someone owes the developer. The note is delivered as a notification when it comes due.

## What to capture

- **Promise:** what exactly was promised, to whom or by whom, and what "done" looks like. Include the deliverable (a review, a reply, a release, a document) and its recipient.
- **Owner:** who is responsible: the developer or the other person. Use the name the packet gives.
- **Due:** when it is due, restated in words; the `schedule` field carries the exact moment.
- Where the promise was made (a meeting, a thread, an issue) and what depends on it.

## What is critical

The promise, the owner, and the due date. A promise without a date cannot be delivered; the assistant should ask for one before drafting when the packet gives none.

## How to reason

- `schedule` is required and must be the one-shot form `at <timestamp>` with the due moment. Copy a relative form from the packet as given (`in 3 days`, `2026-10-01 10:00`); never compute a date yourself.
- Tag with the project, the tracker issue, and the other person's name when the packet gives them.
- When the promise came out of a decision or a conversation that is already a note, mention it in the body.
