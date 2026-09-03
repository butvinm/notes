---
kind: fact
status: active
paths: [~/Dev/exampleco]
tags: [kafka, infra]
keywords: [retention, topic]
references: []
---

# Kafka topic retention is seven days

**Fact:** The shared Kafka cluster keeps messages for seven days, so a consumer can replay a week of updates.

**Source:** Platform team wiki.

**Context:** Long enough to recover from a weekend outage.
