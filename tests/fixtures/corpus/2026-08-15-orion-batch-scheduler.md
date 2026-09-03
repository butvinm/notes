---
kind: idea
status: active
paths: [~/Dev/orion]
tags: [orion, scheduler]
keywords: [cron, batch jobs]
references: []
---

# Orion batch scheduler

**Idea:** Run Orion batch jobs through one scheduler instead of a pile of cron entries.

**Why it might matter:** Cron entries drift between machines and nobody sees failures.

**Open questions:** Whether systemd timers are enough.
