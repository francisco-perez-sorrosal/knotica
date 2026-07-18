---
id: dec-draft-unified-query-api
title: Unified wiki query API — one MCP tool, invisible engines
status: proposed
category: architectural
date: 2026-07-17
summary: Interactive and headless wiki answers share one MCP tool named `query`. Baseline MessagesApiRunner and future compiled DSPy programs are internal QueryEngine backends only — never a second public tool (`wiki_query` is retired as an MCP name). Dashboard Ask, Arena scoring, and eval reuse the same facade.
tags: [query, mcp, dashboard, arena, phase-3a, client-as-brain, dspy]
made_by: agent
agent_type: systems-architect
branch: worktree-hackathon-loop-ideas
pipeline_tier: standard
affected_files: [src/knotica/core/query_engine.py, src/knotica/mcp_server/tools_query.py, docs/PRE_PLAN.md, dashboard/src/AskPane.tsx]
affected_reqs: []
dissent: Keeping PRE_PLAN's separate `wiki_query` tool name would make Phase 3a compile visible in the tool surface and force dashboards/agents to pick between two answer APIs.
re_affirms: [dec-003]
---

## Context

PRE_PLAN Phase 3a named an "optimized `wiki_query` tool" after DSPy compile. The dashboard Ask pane and Arena need a headless answer path today (eval already has `MessagesApiRunner`). Exposing two MCP answer tools (`query` prompt protocol vs `wiki_query`) confuses users and agents.

## Decision

1. **One public answer tool: `query`.** Args `question`, `topic`, optional `vault`. Returns `answer`, `citations`, `pages_used`. No `engine` / `dspy` / `compiled` fields in the default envelope.
2. **Internal `QueryEngine`.** Default backend is `MessagesApiRunner`. Phase 3a compiled artifacts swap in behind the same function — no MCP rename.
3. **MCP prompt `query` stays** for agentic browse (`search` / `read_page`). Prompt text prefers the `query` tool for one-shot answers.
4. **Headless LLM is allowed** for this one tool (and eval/arena), early arrival of PRE_PLAN's "server gains optional LLM access." Inventory tools remain deterministic.
5. **Retire `wiki_query` as a public MCP name.** PRE_PLAN Phase 3a wording updates to "compiled engine behind MCP tool `query`."

## Consequences

- Dashboard Ask and Arena never mention engines.
- Tool count stays thin (no parallel answer tool).
- Claude Code `/knotica:query` can call the tool or browse; both surfaces stay the `query` operation.
