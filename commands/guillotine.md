---
description: Put a wiki claim on trial — find mentions, audit evidence, and generate a reversible retraction patch.
argument-hint: "<claim>"
allowed-tools:
  - Bash(knotica guillotine:*)
---
Run Memory Guillotine on the claim:

```
knotica guillotine "$1" --topic agentic-systems --dry-run
```

Replace `agentic-systems` with the relevant topic if needed.
