---
description: Ingest a source into the knotica wiki (fetch, place by topic, write pages, log).
argument-hint: "<source-url> [topic]"
allowed-tools:
  - mcp__plugin_knotica_knotica__*
  - Bash(knotica prompt:*)
---
!`knotica prompt ingest --source "$1" --topic "$2"`
