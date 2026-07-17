---
id: dec-002
title: Search pagination — opaque cursor envelope over offset implementation
status: accepted
category: architectural
date: 2026-07-03
summary: search exposes an opaque next_cursor/has_more/total_count envelope (offset-encoded at MVP) rather than raw offset/limit, so the contract survives the Phase-5 vector-backend swap unchanged.
tags: [mcp, pagination, search, agent-interface, stateless]
made_by: agent
agent_type: interface-designer
branch: pipeline-wiki-mvp-core
pipeline_tier: standard
affected_files:
  - src/knotica/mcp/
  - src/knotica/search/
dissent: An opaque cursor is over-engineering for a filesystem search returning a handful of results at MVP scale; plain offset/limit would be simpler and adequate.
---

## Context

`search` is the only list-returning tool over an unbounded collection (page contents across topics). The model consumer's context window is a shared resource, so the result must be paginated and bounded. Two contract shapes are available: expose `offset`/`limit` directly, or expose an opaque `next_cursor` the client echoes back. The MVP backend is ripgrep/BM25 over a local vault; Phase 5 swaps in a vector/hybrid backend. The server is **stateless** — it may hold no cursor memory between calls.

## Decision

`search` returns `{results, next_cursor, has_more, total_count}`. `next_cursor` is an **opaque base64 token that self-encodes `{query, sort, offset}`** — it carries its own state, so no server-side cursor store is needed (stateless-server compatible). Default page size 10, max 50. Results are **pointers** (topic, page path, snippet, score), never full page bodies. At MVP the token decodes to an offset over a deterministic sort; the client never sees or depends on that.

## Considered Options

### A. Opaque cursor envelope, offset-encoded (CHOSEN)
- Pros: contract survives the ripgrep→BM25→vector backend evolution with zero client-visible change; agent-ergonomic (bounded, self-describing); stateless (token is self-contained); matches the Stripe "design the list envelope first" canon.
- Cons: a decode/encode step and a token the model must round-trip; mild over-engineering at current scale.

### B. Raw `offset` / `limit`
- Pros: simplest possible; trivially understood by the model; zero encoding.
- Cons: `offset` is meaningless/leaky against a future vector backend (relevance ranking has no stable numeric offset); changing it later is a **breaking contract change** to a model-facing surface — exactly what versioning discipline says to avoid.

### C. No pagination (return all matches)
- Pros: no cursor at all.
- Cons: unbounded response displaces reasoning tokens; violates the agent pagination rule (default 10–20). Rejected outright.

## Consequences

- Positive: one stable search contract from Phase 1 through Phase 5; small, predictable responses; no stateful cursor store to reconcile across concurrent sessions.
- Negative: a thin encode/decode layer now; an `INVALID_CURSOR` error path to implement (stale/malformed token → "restart without a cursor").

## Disconfirmation

- **Falsifier:** if the vector backend (Phase 5) turns out to paginate naturally by a numeric offset too, or if the search result set is provably always ≤ one page in practice, the cursor indirection bought nothing.
- **Steelmanned runner-up:** plain `offset`/`limit` (Option B) is what most small tools ship; at a few-dozen-page vault the model will rarely page at all, so the simpler contract would carry zero cost for years and could be migrated behind a version bump if ever needed.
- **Reversal trigger:** if Phase-5 search design lands on a numeric-offset-friendly backend AND no other list tool has adopted the cursor envelope, collapse `search` back to `offset`/`limit` and drop the token machinery.
