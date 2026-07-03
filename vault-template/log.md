# Operation Log

Append-only log of vault operations, newest last: one entry per mutating operation, written in
the same commit as the operation itself. The entry format is frozen by [[SCHEMA]]:

```
## [YYYY-MM-DD] <op> | <topic> | <title>
- <touched page path>   (optional bullets, one per touched page)
```

Example:

```
## [2026-07-03] write_page | agentic-systems | Ingest ReAct paper
- agentic-systems/react.md
- index.md
```

Entries follow below.

## [2026-07-03] store_source | agentic-systems | Agent Workflow Memory, arXiv 2409.07429 (demo sample)
- sources/agentic-systems/wang2024awm.md

## [2026-07-03] write_page | agentic-systems | Agent Workflow Memory (Wang et al., 2024) (demo sample)
- agentic-systems/agent-workflow-memory.md

## [2026-07-03] write_page | agentic-systems | Workflow induction (demo sample)
- agentic-systems/workflow-induction.md

## [2026-07-03] write_page | agentic-systems | Agent memory (demo sample)
- agentic-systems/agent-memory.md

## [2026-07-03] write_page | agentic-systems | Index entries for the AWM demo ingest (demo sample)
- index.md
