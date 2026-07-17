---
id: dec-003
title: Tool decomposition — thin deterministic tools with a write_page fat-write exception
status: accepted
category: architectural
date: 2026-07-03
summary: Expose ~10 thin deterministic tools (no progressive disclosure), consolidate list_links/backlinks into one direction-parameterized tool, and make write_page a single transactional fat-write (scrub+write+commit+log+optional root-index upsert); reserved bookkeeping files (index.md/log.md/SCHEMA.md) are never direct write targets.
tags: [mcp, tool-design, agent-interface, decomposition, client-as-brain]
made_by: agent
agent_type: interface-designer
branch: pipeline-wiki-mvp-core
pipeline_tier: standard
affected_files:
  - src/knotica/mcp/
dissent: Splitting write_page's side effects into separate tools would be more composable and testable; bundling them trades agent flexibility for atomicity the client rarely needs to control.
---

## Context

Client-as-brain fixes the tool layer as **deterministic** (no server LLM in Phase 1); the model does all cognition. The design question is decomposition: how many tools, how fat, and whether the surface needs progressive disclosure. LLM tool-selection accuracy degrades past ~20–25 tools; the vault operations are ingest/query/lint/curate, orchestrated by the model branching on intermediate results.

## Decision

**Ten thin tools**, no progressive disclosure (well under the degradation threshold — a meta-tool would be premature). Tools are thin because the agent branches (`search`→`read_page`→`write_page` is not a fixed chain). Three specific calls:

1. **Add `list_topics`** — the read primitive the locked topic-inference policy requires (enumerate topics to place content). Without it the policy is unimplementable.
2. **Consolidate `list_links`/`backlinks` into one `list_links(topic, page, direction=out|in|both)`** — one schema in the model's context instead of two; `direction="in"` is backlinks. Bloch minimal-surface + consistency.
3. **`write_page` is a transactional fat-write** — secret-scrub + atomic write + one git commit + log append + **optional root-`index.md` upsert (via the `index_entry` arg)** execute as one indivisible unit. These side effects *always* co-occur; splitting them would let the client leave the vault half-committed (page written but not logged/indexed, or committed but not scrubbed). This is the justified fat-tool: steps always occur together and combined semantics are clear.
4. **Reserved bookkeeping files are never direct write targets** (adjudicated 2026-07-03, Step-2 three-way contract conflict: the earlier "second `write_page` call to `index.md`" instruction collided with the reserved-name guard REQ-TOOL-03 *and* the required-non-empty `topic` rule, since root `index.md` is topic-less). Resolution: `index.md`, `log.md`, and `SCHEMA.md` are maintained *only* as atomic side effects of the operations that affect them — `write_page` upserts the catalog line via `index_entry`; `create_topic` maintains the topic's index line. The reserved-name guard therefore stays **absolute** (no narrowing) and no topic-less write path is introduced. Chosen over (a) sanctioning a `topic=""`+`page="index.md"` special case [two invariants weakened] and (c) an 11th dedicated root-index tool [lets the client write a page and forget the index — the exact consistency failure the transaction prevents].

## Considered Options

### A. Ten thin tools, list_links consolidated, write_page fat-write (CHOSEN)
- Pros: small surface, no progressive disclosure needed; atomic vault mutations; single link mental-model; the stateless/deterministic contract stays clean.
- Cons: `write_page`'s bundled side effects are less individually testable; `list_links` with a direction enum is marginally less discoverable than two named tools.

### B. Separate write / commit / log / scrub tools
- Pros: maximally composable; each side effect independently testable and callable.
- Cons: the model must orchestrate a 4-call sequence correctly every write; any dropped step corrupts the audit/scrub invariants — precisely the "hard to misuse" failure. Rejected.

### C. Keep list_links and backlinks as two tools (PRE_PLAN literal)
- Pros: names match well-known wiki/Obsidian vocabulary exactly.
- Cons: two schemas for one graph query in two directions; redundant surface. The consolidated tool's description names "backlinks" explicitly, preserving discoverability.

## Consequences

- Positive: a 10-tool surface a model selects over reliably; vault invariants (one-commit-per-op, scrub, log, catalog consistency) enforced *inside* the tool, not delegated to model discipline; the reserved-name guard stays absolute; page+index can never drift (one call); room to grow before progressive disclosure is warranted.
- Negative: `write_page` gains an `index_entry` arg and the transaction (Step 25 `VaultTransaction`) must upsert an `index.md` line keyed by topic+page (parse/replace, not blind append) — more logic in the single mutation path; if the tool count grows past ~20 in later phases (wiki_query, compile triggers, SIA moves), progressive disclosure must be revisited then.

## Disconfirmation

- **Falsifier:** if models frequently fail to discover backlinks behind `direction="in"`, or if the fat `write_page` forces awkward client workarounds (e.g., wanting to commit without logging), the consolidation/bundling was wrong.
- **Steelmanned runner-up:** Option B's fully-decomposed side-effect tools give the SIA/DSPy loops (Phase 3) finer control over commit granularity, which a bundled `write_page` hides — a real cost if the loops need sub-page transactional control.
- **Reversal trigger:** when the tool count approaches ~20 (Phase 3+ adds loop-trigger and query-program tools), re-evaluate for progressive disclosure and reconsider whether `write_page` should expose a `commit=false` staging mode for multi-page atomic ingests.
