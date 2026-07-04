---
description: Show knotica wiki status — pages per topic, compile-ready count, lint, unpushed commits.
argument-hint: "[topic]"
allowed-tools:
  - Bash(knotica status:*)
---
Wiki status:
!`knotica status --topic "$1"`

Summarize the counts above and point to the next useful action (ingest more,
lint, or push unpushed commits).
