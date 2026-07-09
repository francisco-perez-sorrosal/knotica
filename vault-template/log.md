# Directory Update Log

Append-only log of vault operations, newest first: one entry per mutating operation,
written in the same commit as the operation itself. Native shape follows [[SCHEMA]] §3:

```
## YYYY-MM-DD
* **Update**: <op> · <topic> — <title> ([[touched/path]], [[index]])
```

Legacy Knotica headings (`## [YYYY-MM-DD] <op> | <topic> | <title>`) are still
parseable and are normalized by `knotica okf repair`.

## 2026-07-03

* **Update**: write_page · agentic-systems — Agent memory (demo sample) ([[agentic-systems/agent-memory]], [[index]])

* **Update**: write_page · agentic-systems — Workflow induction (demo sample) ([[agentic-systems/workflow-induction]], [[index]])

* **Update**: write_page · agentic-systems — Agent Workflow Memory (Wang et al., 2024) (demo sample) ([[agentic-systems/agent-workflow-memory]], [[index]])

* **Update**: store_source · agentic-systems — Agent Workflow Memory, arXiv 2409.07429 (demo sample) ([[sources/agentic-systems/wang2024awm]])
