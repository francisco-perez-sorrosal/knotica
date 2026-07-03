---
id: dec-draft-6ea4e4f3
title: MCP SDK — official mcp 1.28.1 over jlowin fastmcp 3.4.2
status: proposed
category: architectural
date: 2026-07-03
summary: Use the official mcp SDK (mcp.server.fastmcp.FastMCP) rather than jlowin fastmcp v3; dep-weight shrinks the cold uvx start and canonicity de-risks Phase-4 HTTP+OAuth.
tags: [mcp, sdk, dependencies, cold-start, phase-1]
made_by: agent
agent_type: systems-architect
branch: pipeline-wiki-mvp-core
pipeline_tier: standard
affected_files: [src/knotica/mcp/, pyproject.toml]
affected_reqs: [REQ-PLUGIN-01, REQ-PROMPT-01]
dissent: A protocol-heavy Phase 4 (auth providers, CIMD) plus a large MCP-integration test surface would have favored jlowin fastmcp's batteries and in-memory Client(server) transport.
---

## Context

`docs/PRE_PLAN.md` says "FastMCP" without pinning which package — and there are two: the official
`mcp` SDK (which ships `mcp.server.fastmcp.FastMCP`, 1.28.1) and jlowin's standalone `fastmcp` v3
(3.4.2). RESEARCH_FINDINGS Q1e verified both satisfy every knotica requirement (static-name/lazy-body
prompts, resources, tools, streamable HTTP + OAuth for Phase 4). The architect must pick one. The choice
is load-bearing because the MVP's single measured operational risk is the **cold `uvx --from` env
resolution** (24.4 s with fastmcp-class deps vs a lighter env), which can trip Claude Code's MCP startup
window on first launch/update.

## Decision

Use the **official `mcp` SDK 1.28.1** via `mcp.server.fastmcp.FastMCP`. Dependency policy: floor
`mcp>=1.28`, not an exact pin. The swap surface is confined to `src/knotica/mcp/`.

## Considered Options

### Option A — official `mcp` 1.28.1 (chosen)
- **Pros:** lighter dependency env → directly shrinks the cold uvx resolution that is the MVP's top
  operational risk; reference implementation tracking the spec directly (de-risks Phase-4 HTTP+OAuth);
  in-memory testing available via `mcp.shared.memory` streams.
- **Cons:** no batteries-included auth providers (Phase 4 wires auth by hand); no `fastmcp list/call`
  smoke CLI; protocol-level tests are slightly more verbose.

### Option B — jlowin `fastmcp` 3.4.2
- **Pros:** best-in-class in-memory `Client(server)` pytest transport; richer typed conversion; batteries
  for auth (GitHubProvider, StaticTokenVerifier) helpful at Phase 4; agent-friendly smoke CLI.
- **Cons:** heavier env (the 67-package, 130 MB, 24.4 s cold resolution in the RESEARCH timing was a
  fastmcp env) — worsens the exact risk we most need to shrink; docs site tracks `main` with
  maintainer-acknowledged drift.

## Consequences

- **Positive:** smallest feasible cold-start footprint; canonical spec tracking; one fewer third-party
  layer between knotica and the protocol.
- **Negative:** the test harness cannot lean on fastmcp's in-memory client — mitigated because
  client-as-brain makes tools deterministic functions unit-tested at the `core` layer (no protocol
  needed), leaving only a thin MCP band that the official SDK's in-memory streams cover. Phase-4 auth is
  hand-wired rather than provider-dropped.

## Disconfirmation

- **Falsifier:** if the official-SDK env, once resolved, is not materially lighter/faster to cold-resolve
  than the fastmcp env in the Phase-1 cold-start drill (i.e. dep-weight was not the real lever), the
  primary rationale collapses.
- **Steelmanned runner-up (fastmcp):** knotica's differentiator is a *self-improving* system whose Phase-4
  remote surface needs OAuth and whose correctness leans on a large MCP-protocol contract; fastmcp's
  in-memory `Client(server)` makes that contract cheap to test end-to-end, and its auth providers turn
  Phase-4 from a build into a config. If protocol-level test friction or Phase-4 auth work proves painful,
  fastmcp was the higher-leverage pick and the extra cold-start seconds are a one-time, pre-warmable cost.
- **Reversal trigger:** revisit if (a) the cold-start drill shows dep-weight did not move cold-resolution
  meaningfully, OR (b) Phase-4 HTTP+OAuth on the official SDK requires substantially more work than
  fastmcp's providers, OR (c) the thin MCP test band grows and protocol-test friction becomes a real drag.
  The D2 module boundary keeps the swap confined to `mcp/`.
