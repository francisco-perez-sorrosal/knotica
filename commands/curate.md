---
description: Curate a knotica example into compile-ready training signal with a verdict.
argument-hint: "[topic] [verdict]"
allowed-tools:
  - mcp__plugin_knotica_knotica__*
  - Bash(knotica prompt:*)
---
!`knotica prompt curate --topic "$1" --verdict "$2"`
