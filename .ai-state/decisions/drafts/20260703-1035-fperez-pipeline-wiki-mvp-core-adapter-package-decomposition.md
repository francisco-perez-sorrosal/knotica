---
id: dec-draft-8d8c18a1
title: Adapter/operations package decomposition and `mcp_server` module naming
status: proposed
category: implementation
date: 2026-07-03
summary: MCP adapter lives at src/knotica/mcp_server/ (avoids shadowing the `mcp` SDK) with per-concern registration modules; core.operations and cli are packages with one module per op/command.
tags: [module-structure, packaging, mcp, naming]
made_by: agent
agent_type: implementation-planner
branch: pipeline-wiki-mvp-core
pipeline_tier: standard
affected_files:
  - src/knotica/mcp_server/
  - src/knotica/core/operations/
  - src/knotica/cli/
  - .ai-state/DESIGN.md
  - docs/architecture.md
---

## Context

The architecture (dec-draft-9039d858, D2) draws the module boundary — hexagonal core, thin `mcp`/`cli`
adapters — but leaves intra-adapter layout to the planner. Two concrete issues surfaced at step
decomposition: (1) `SYSTEMS_PLAN.md`/`DESIGN.md` name the adapter `src/knotica/mcp/`, which shadows the
official `mcp` SDK package name inside the project tree — harmless to Python 3 absolute imports but a
recurring source of tooling/editor/human confusion and ambiguous tracebacks; (2) the adapter and
`core.operations` each aggregate 4–9 sub-concerns with distinct requirement traces, and a single
`server.py`/`operations.py` file would force every parallel step pair through one file, serializing work
and bloating diffs.

## Decision

- Rename the MCP adapter package to `src/knotica/mcp_server/`.
- Decompose it per concern: `server.py` (FastMCP construction + registration calls only), `envelope.py`
  (shared result/error formatting per INTERFACE_DESIGN §1.4), `tools_read.py`, `tools_write.py`,
  `resources.py`, `prompts.py`. Registration steps remain **sequential** (they all touch `server.py`).
- `core/operations/` is a package, one module per operation (`write_page`, `store_source`,
  `create_topic`, `curate_example`, `migrate`), each opening the shared `VaultTransaction`.
- `cli/` is a package, one module per subcommand plus `common.py` (exit codes, output conventions).

## Considered Options

### A — Keep `src/knotica/mcp/` single-module adapter (architect's sketch)
- Pros: matches DESIGN.md as written; fewer files.
- Cons: name-shadows the `mcp` dependency for humans/tooling; one fat module concentrates 10 tools +
  4 prompts + 4 resources + envelope in a single file; parallel test pairing loses file disjointness.

### B — `mcp_server/` package, per-concern modules (chosen)
- Pros: no name collision; table-of-contents layout; small sequential diffs against `server.py` only;
  envelope is one shared module (single contract source).
- Cons: slight indirection; diverges from the literal module name in DESIGN.md (docs updated in place).

## Consequences

- Positive: file-disjoint step pairs; the import-boundary fitness test targets clean package names;
  future `--http` transport lands as one more module.
- Negative: any doc citing `knotica.mcp` must say `knotica.mcp_server`; corrected in `.ai-state/DESIGN.md`
  and `docs/architecture.md` at planning time.

## Disconfirmation

Not `category: architectural`; noted informally — if the official SDK ever requires the server package
to be importable under a fixed name, or if per-concern modules prove to be ceremony (files < ~40 lines),
collapse back to fewer modules.
