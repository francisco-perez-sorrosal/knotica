---
description: Answer a question from the knotica wiki, grounded in curated topic pages.
argument-hint: "<question> [topic]"
allowed-tools:
  - mcp__plugin_knotica_knotica__*
  - Bash(knotica prompt:*)
---
!`knotica prompt query --question "$1" --topic "$2"`
