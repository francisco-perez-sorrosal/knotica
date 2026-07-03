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
