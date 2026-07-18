# ADR draft — Compiled query artifact wins over Arena baseline prompt

Status: draft  
Date: 2026-07-17

## Context

Phase 3a adds DSPy-compiled query artifacts under `<topic>/.knotica/compiled/`.
Arena (M5) continues to evolve vault `query.md` as the reactive heal path.

## Decision

When a healthy compiled artifact is present, `QueryEngine.select_runner` serves
`CompiledRunner` (optimized instructions + demos). Otherwise it serves
`MessagesApiRunner` driven by `query.md`.

Arena promotion of `query.md` does **not** delete the compiled artifact. The next
`knotica compile` refreshes the artifact on a new `compile/<topic>/<sha>` branch.

Public MCP surface remains a single `query` tool with no engine fields in the
default envelope.

## Consequences

- Compile is the proactive flywheel; Arena remains the reactive gate-fail path.
- Human merge of the compile branch is required before live vaults serve compiled.
- Topics without a compiled artifact keep the Arena / baseline prompt path.
