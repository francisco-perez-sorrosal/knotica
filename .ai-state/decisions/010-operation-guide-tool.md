---
id: dec-010
title: Serve operation protocols as a tool (read_protocol) + set server instructions
status: accepted
category: architectural
date: 2026-07-04
summary: Add a read_protocol tool + FastMCP instructions so plain natural-language requests complete multi-step operations on clients that do not surface MCP prompts.
tags: [mcp, prompts, tools, ergonomics, client-as-brain]
made_by: agent
agent_type: orchestrator
branch: pipeline-wiki-mvp-core
pipeline_tier: lightweight
affected_files:
  - src/knotica/mcp_server/tools_guide.py
  - src/knotica/mcp_server/server.py
  - tests/test_mcp_guide.py
dissent: A read_protocol tool duplicates the prompt surface's purpose and nudges toward one more round-trip; a client that surfaces MCP prompts well needs neither.
---

## Context

knotica is client-as-brain: the MCP tools are deterministic and each operation (ingest/query/lint/curate)
is a multi-step protocol the model orchestrates, guided by an operation **prompt**. This works in Claude
Code (the `/knotica:*` command aliases inject the prompt), but in Claude Desktop — which does not surface
MCP prompts as slash commands — a plain "ingest this paper" made the model call `store_source` and stop:
the source was stored but never distilled into pages, wikilinked, or indexed. The friction is a fundamental
MCP asymmetry: **tools are invoked automatically from natural language; prompts must be manually inserted
by the user.**

## Decision

Two additions, both preserving client-as-brain (no server-side LLM, tools stay deterministic):

1. **`read_protocol(operation, topic)` tool** — returns the *same* vault-resolved operation body the
   `prompts/get` handler and `knotica prompt` CLI serve, via the shared `core.prompts.get_prompt` resolver
   (single source of truth). Because it is a tool, the model invokes it from a natural-language request,
   loads the full protocol, and follows it end to end. `operation` is a `Literal` enum (SDK enforces valid
   values); unconfigured returns setup-guidance (mirrors the prompt surface), a malformed vault returns a
   `NOT_CONFIGURED` error envelope.
2. **Server `instructions`** — FastMCP top-level instructions telling the model the operations are
   multi-step and to call `read_protocol` before acting (never stop after `store_source`). Injected into
   context on every client, zero user effort.

## Considered Options

- **A. Prompt-only (status quo).** Reliable when the client surfaces prompts; fails silently in Desktop.
- **B. Server instructions only.** Zero-cost nudge, helps every client, but advisory — not a guarantee.
- **C. read_protocol tool only.** Strong (tools auto-invoke) but the model must know to call it.
- **D. B + C (chosen).** Instructions point the model at the tool; the tool delivers the protocol. Together
  they approximate the Claude Code slash-command UX in any MCP client.
- **E. A fat server-side ingest that does the cognitive work.** Rejected: violates client-as-brain
  (server would need LLM access; forbidden until the Phase-3a headless loops).

## Consequences

- Positive: "ingest this paper" completes the full sequence in Claude Desktop; the protocol stays
  single-sourced in the vault `prompts/`; the tool count grows by one (10 -> 11), an additive change.
- Negative: one more tool on the surface (the design targeted ~10, no progressive disclosure); a `Literal`
  in the tool signature duplicates `core.prompts.OPERATIONS` (a type annotation cannot reference the tuple).
- Neutral: server instructions are advisory; the tool is the load-bearing half.

## Disconfirmation

- **Falsifier:** if, with instructions + tool in place, models still routinely stop after `store_source` in
  Desktop, the nudge did not work and a stronger mechanism (or a fat guided tool) is warranted.
- **Steelmanned runner-up (B only):** if server instructions alone reliably drive completion, the extra tool
  is surface bloat; instructions are free and the tool is not.
- **Reversal trigger:** MCP clients converging on first-class prompt invocation (prompts auto-offered from
  natural language) would make read_protocol redundant — revisit then.
