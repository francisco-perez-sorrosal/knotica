---
description: "[Tend] Put a wiki claim on trial — find mentions, audit evidence, and generate a proposed-removal diff for human review."
argument-hint: "<claim> [topic]"
allowed-tools:
  - Bash(knotica tend guillotine:*)
  - mcp__plugin_knotica_knotica__*
---
Run Memory Guillotine on the claim. It never rewrites wiki pages — the diff it produces is
evidence for you to review, not an applied edit; re-grounding after a retraction flows through
the gap-fill pipeline, not this command.

Resolve the topic first: use `$2` if given. Otherwise take it from the conversation — the
topic you last queried or discussed. Only if no topic is inferable, call
`wiki_status(view="scope")` and ask once, offering the vault's topics.

```
knotica tend guillotine "$1" --topic <resolved-topic> --dry-run
```
