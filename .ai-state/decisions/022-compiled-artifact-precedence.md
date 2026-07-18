---
id: dec-022
title: Compiled query artifact takes precedence over the Arena baseline prompt
status: accepted
category: implementation
date: 2026-07-17
summary: QueryEngine.select_runner serves CompiledRunner whenever a healthy compiled artifact exists, else the query.md-driven MessagesApiRunner; Arena promotions never delete the artifact, and the public MCP surface stays a single `query` tool.
tags: [query, compile, arena, phase-3a, runner-selection]
made_by: agent
agent_type: implementation-planner
branch: worktree-hackathon-loop-ideas
pipeline_tier: standard
affected_files: [src/knotica/core/query_engine.py, src/knotica/core/compiled.py]
affected_reqs: []
---

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
