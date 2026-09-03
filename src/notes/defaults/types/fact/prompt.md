# Fact

A fact records something that is true about a system, a process, or a person's preferences, and where that knowledge came from, so it can be trusted or rechecked later.

## What to capture

- **Fact:** the statement itself, precise enough to act on: names, versions, limits, defaults, addresses, exact behavior. One fact per note; split unrelated facts.
- **Source:** where the fact was learned: a file and line, a document, a command and its output, a person, a message. Exact enough to verify again.
- **Context:** when and under which conditions it holds: the environment, the version, the configuration, the date it was observed, and what would make it stale.

## What is critical

The fact and its source. A fact without a source becomes a rumor after a few weeks; the assistant should ask where it came from before drafting when the packet does not say.

## How to reason

- Prefer the exact wording from the source (a config key, an error message, a command) over a paraphrase, and quote it.
- Put every name the fact might be searched by in `keywords`: the abbreviation, the full name, the internal nickname.
- Tag with the system or project the fact belongs to; add the tracker issue when the fact was learned while working on one.
- When the fact contradicts something the developer believed, say so plainly in the body.
