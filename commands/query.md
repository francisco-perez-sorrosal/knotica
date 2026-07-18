---
description: Answer a question from the knotica wiki, grounded in curated topic pages.
argument-hint: "<question> [topic]"
allowed-tools:
  - mcp__plugin_knotica_knotica__*
  - Bash(knotica prompt:*)
---
Answer the user's wiki question.

1. Prefer the MCP tool `query` with `question`=$1 and `topic`=$2 (when provided) — that is
   the single wiki-answer API. Present `answer`, `citations`, and `pages_used`.
2. Only if the user wants to explore pages, load the protocol instead:
!`knotica prompt query --question "$1" --topic "$2"`
3. After answering, offer to save a curated example via `curate_example`.
