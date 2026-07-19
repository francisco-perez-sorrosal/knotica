---
id: dec-028
title: Suggestion-queue interface — two-tool decomposition with action-parameterized review
status: accepted
category: architectural
date: 2026-07-19
summary: The P3 gap-fill approval surface exposes two deterministic MCP tools — a read-only paginated suggestions_read (dec-002 cursor envelope) and a two-phase mutating suggestions_review(action ∈ {approve,reject,defer,mark_ingested}, mode ∈ {dry-run,apply}); the SuggestionRecord denormalizes the SourceCandidate and the motivating gap's display fields so a card renders with zero cross-file join; the approved record IS the queued ingest instruction, surfaced to the interactive client via an additive wiki_status.suggestions block and an ingest-protocol text hook.
tags: [mcp, tool-design, agent-interface, suggestion-queue, human-approval, gap-fill, phase-3, dashboard, client-as-brain, pagination]
made_by: agent
agent_type: interface-designer
branch: worktree-hackathon-loop-ideas
pipeline_tier: standard
affected_files:
  - src/knotica/mcp_server/
  - src/knotica/core/records.py
  - src/knotica/core/status.py
  - dashboard/src/
affected_reqs: []
dissent: Three separate approve/reject/defer tools would be marginally more discoverable (each verb self-names) and independently testable; consolidating them behind an action enum trades that for a smaller model-facing surface and one state-transition code path — the same trade dec-003 already made for list_links/branch_promote.
re_affirms: [dec-002, dec-003, dec-020]
---

## Context

P3 lands the human-approval surface where the loop's diagnosed `genuine_gap` `GapRecord`s
(dec-025) meet ranked, reputability-scored `SourceCandidate`s (dec-027) and a human decides
approve / reject / defer. The surface must serve two consumers over **one** data contract (dec-020:
MCP tools only, no new transport): the Preact dashboard (as a pure MCP client) and an interactive
Claude session that later executes the ingest (client-as-brain, dec-014 — approval must NOT ingest).
Constraints inherited: deterministic tools only (no server LLM), stateless server (topic explicit,
per-call config resolve), one commit per mutating vault op, rejection captured with a reason
(proposer-quality feedback, never a silent discard), never render fabricated data.

The open interface decisions: how many tools and how fat; the suggestion record schema and its
lifecycle states; the pagination contract for a queue that can grow; and how the approved suggestion
is discovered by the interactive client.

## Decision

**Two deterministic MCP tools**, extending the as-built house patterns:

1. **`suggestions_read(topic, status="pending", cursor="", limit=20, vault="")`** — read-only,
   returns the dec-002 opaque-cursor envelope `{suggestions, status_counts, next_cursor, has_more,
   total_count, skipped_malformed}`. Default `limit=20`, max `50`. Sort `detected_generation` desc,
   `rank` asc. Records are full `SuggestionRecord`s (candidate denormalized) so a card renders with
   no second call. Mirrors `wiki_status`/`metrics_read`: `envelope.read_ok`, `NOT_CONFIGURED`
   contract, no lock, no commit.

2. **`suggestions_review(topic, suggestion_id, action, mode="dry-run", reason="", vault="")`** —
   two-phase mutating tool. `action ∈ {approve, reject, defer, mark_ingested}`; `mode ∈ {dry-run,
   apply}` exactly per `tools_scoreboard` (`branch_promote`). `reject` requires a non-empty `reason`
   (enforced server-side). Apply performs one `VaultTransaction` (read-modify-write
   `suggestions.jsonl`, flip one record's `status`/`decided_at`/`decided_reason`, one commit) —
   never piggybacking on loop-state/metrics commits.

**Consolidate the three decision verbs behind an `action` enum** rather than shipping
`suggestion_approve` / `suggestion_reject` / `suggestion_defer` — they target the same record by the
same key and differ only in terminal status and whether a reason is required. This is the dec-003
`list_links(direction=…)` / `branch_promote(kind=…)` precedent.

**`SuggestionRecord` (`schema_version: 1`)** at `<topic>/.knotica/suggestions/suggestions.jsonl`
(observe-safe / bookkeeping path, same loop-observation exemption as `gaps.jsonl`). It **denormalizes**
the nested `SourceCandidate.to_record()` and **copies** the motivating gap's display fields
(`question`, `reference_pages`, `qa_id`, `fault_class`, `detected_generation`) — every copied field
is real data, honoring the never-fabricate guard while eliminating a cross-file join at render time.
`suggestion_id` = sha1(topic | gap_id | (doi or url))[:16] (dedup + join key, mirroring the gap-id
scheme). Lifecycle: `pending → approved → ingested`; `pending → rejected` (reason required);
`pending ↔ deferred`. `approved`/`ingested`/`rejected` are guarded transitions; illegal transitions
fail fast.

**The approved record IS the queued instruction** — it already carries `candidate.url` (what to
fetch), `question` + `reference_pages` (why/where), `gap_id` (trace). Discovery via two additive
surfaces: (a) an additive `wiki_status.suggestions` summary block
(`{pending, approved_awaiting_ingest, deferred, rejected, ingested, newest_proposed_at}`); (b) one
sentence in `read_protocol("ingest")` routing the client to `suggestions_read(status="approved")`.
Approval writes only the status flip on the default branch; the `loop/c/*` branch is created at
ingest time — approval ≠ ingest is preserved.

**Error contract**: envelope-consistent `{error:{code,message,fix,retryable}}`. One additive
`ErrorCode.SUGGESTION_NOT_FOUND` is the only new code; argument validation reuses `INVALID_CURSOR`
to match the exact `_promote_payload` precedent.

## Considered Options

### A. Two tools — read + action-parameterized review (CHOSEN)
- Pros: smallest model-facing surface (+2 tools); one state-transition code path; matches dec-003
  consolidation and the `wiki_status`-vs-`loop_promote` read/mutate split; dry-run|apply is the
  established two-phase muscle memory; cursor envelope reuses dec-002 wholesale.
- Cons: `action` enum is marginally less self-documenting than three named verbs; the review tool's
  branches are less individually discoverable.

### B. Three verb tools (suggestion_approve / _reject / _defer) + read
- Pros: each verb self-names; independently testable; approve needs no `action` arg.
- Cons: three near-identical schemas in the model's context for one state transition — the exact
  redundancy dec-003 rejected for `list_links`; +4 tools pushes harder on the ~20–25 threshold.

### C. One fat tool (read + mutate in a mode-switched call)
- Pros: fewest tools.
- Cons: fuses read (no lock/commit) with mutate (flock, one-commit-per-op) — violates the do-not-mix
  primitive rule; a mode-overloaded tool is hard to misuse *wrongly* (dry-run a read?). Rejected.

### D. Normalized record (store candidate by reference, join at read)
- Pros: no duplication of `SourceCandidate` bytes; single source of truth for a candidate.
- Cons: a card render or an agent read needs a second lookup against the discovery output; the
  discovery output is per-query ephemeral (dec-027: candidates carry no gap linkage) so there is no
  stable store to join against. Denormalization is the honest, join-free shape. Rejected.

## Consequences

- **Positive**: dashboard and interactive-client share one contract; the approved record is
  self-sufficient as an ingest instruction (no separate instruction file); pagination and error
  paths reuse dec-002/envelope machinery unchanged; the surface grows by only two tools; the
  `wiki_status` and ingest-protocol handoffs are additive (no existing field or contract changes).
- **Negative**: `SuggestionRecord` duplicates candidate bytes (bounded — a batch is capped);
  `suggestions_review` carries a small state-machine the implementer must guard; +2 tools moves the
  surface closer to the dec-003 progressive-disclosure re-evaluation line (the *next* tool-adding
  phase should trigger that review); one additive `ErrorCode`.

## Disconfirmation

- **Falsifier**: if models frequently pass the wrong `action` value or struggle to discover
  approve/reject behind one tool (vs three named verbs), or if the denormalized record drifts
  visibly from a re-run discovery result in a way that confuses reviewers, the consolidation/
  denormalization was wrong.
- **Steelmanned runner-up (Option B)**: three named verb tools are what most tool surfaces ship;
  at a handful of tools the ~20–25 threshold is distant, and self-naming verbs are the most
  model-legible shape — the consolidation's token saving is small until the surface is large.
- **Reversal trigger**: if a later phase splits approval into materially different flows (e.g.
  batch-approve, conditional-approve-with-edits) such that the actions no longer share one code
  path, split `suggestions_review` back into per-action tools; and revisit denormalization if a
  persistent candidate store with stable ids emerges upstream.

## Prior Decision

Re-affirms `dec-002` (opaque-cursor pagination envelope — `suggestions_read` adopts it directly),
`dec-003` (thin deterministic tools, consolidate near-identical operations behind an enum,
progressive disclosure deferred until ~20–25 tools), and `dec-020` (dashboard is a pure MCP client
over deterministic tools; no new transport — the Sources pane and the `ui://` stretch both ride the
existing two-transport artifact). Does not supersede any prior decision.
