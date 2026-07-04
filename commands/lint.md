---
description: Lint knotica wiki pages against the schema (links, structure, confidence, supersession).
argument-hint: "[topic]"
allowed-tools:
  - mcp__plugin_knotica_knotica__*
  - Bash(knotica prompt:*)
---
!`knotica prompt lint --topic "$1"`
