---
kind: fact
status: active
paths: [~/Dev/notes]
tags: [sqlite, search]
keywords: [FTS5, full-text search, stemmer]
references: []
---

# SQLite FTS5 stems Latin words with the porter tokenizer

**Fact:** The porter tokenizer reduces English words to their stem, so `updates` matches `update`.

**Source:** SQLite documentation.

**Context:** Russian words are lemmatized before they reach the index.
