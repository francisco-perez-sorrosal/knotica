---
id: dec-draft-3020a1a6
title: Suggestion record schema v1 — the committed P3 queue, lifecycle, and candidate-as-opaque-dict boundary
status: proposed
category: architectural
date: 2026-07-19
summary: The P1 gap + P2 candidate join is persisted as a new schema-versioned SuggestionRecord to a committed append-only <topic>/.knotica/suggestions/suggestions.jsonl (not an uncommitted staging file — a cross-process reader needs committed state, dec-025 Option B), with a pending/approved/rejected/ingested lifecycle mutated in place one-commit-per-transition; the candidate is embedded as an opaque JSON dict (not a typed SourceCandidate) to keep core/records.py off any edge into discovery/ and preserve the MCP cold-start boundary.
tags: [gapfill, phase-p3, suggestion-queue, schema, records, dec-006, dec-025, vault-transaction, stateless-server, import-boundary, p4-contract]
made_by: agent
agent_type: systems-architect
branch: worktree-hackathon-loop-ideas
pipeline_tier: standard
affected_files:
  - src/knotica/core/records.py
  - src/knotica/core/gapfill.py
  - src/knotica/mcp_server/tools_suggestions.py
affected_reqs: [REQ-03, REQ-04, REQ-05, REQ-06]
re_affirms: dec-025
dissent: A committed suggestions.jsonl grows unbounded and commits machine-proposed pending rows before any human sees them; an uncommitted staging file would keep pending noise out of git history and honor the golden.py precedent the brief cites — at the cost of the dashboard MCP server (a separate process) being unable to read a queue that was never committed.
---

## Context

P3 joins P1's `genuine_gap` `GapRecord`s to P2's ranked `SourceCandidate`s and presents them for human
approval via MCP tools + the dashboard. The join must persist as a queryable queue. Knotica is a stateless
server: the vault (git) is the only shared state (dec-004), and the writer (loop-side or CLI discovery) and
the reader (the stateless MCP server / dashboard) are **separate processes**. Two persisted-record precedents
exist: the committed `gaps.jsonl` (dec-025) and the *uncommitted* `golden.staging.jsonl`. The TASK_BRIEF cites
the golden staging-then-freeze convention as the model. `core/records.py`, the single self-versioned record
home (dec-006), is imported at MCP cold start; `discovery/` is deliberately kept *off* that path (dec-013,
fitness test `mcp_server ⊬ discovery`).

## Decision

Introduce **`SuggestionRecord` (schema_version 1)** in `core/records.py`, persisted one-per-line to a
**committed** append-only `<topic>/.knotica/suggestions/suggestions.jsonl`.

Fields: `schema_version`, `suggestion_id` (= `sha1(topic|gap_id|source_key)[:16]`, dedup + identity;
`source_key` = normalized DOI else normalized URL, reusing `DiscoveryService`'s normalization), `topic`,
`gap_id` (join to the motivating `GapRecord`), `qa_id` (denormalized golden id), `query_text` (the
deterministic query issued — provenance), `candidate` (an **opaque JSON object**: the verbatim
`SourceCandidate.to_record()` payload), `status` (`pending`|`approved`|`rejected`|`ingested`, a bare tagged
string), `proposed_at`, `decided_at` (null until a decision),
`decided_reason` (required non-empty on reject, optional on defer, else null), `ingest_ref` (null; P4 records the `loop/c/*`
branch on ingest).

**Committed, not staged.** The suggestion queue is committed exactly as `gaps.jsonl` is — because the reader
is a different process than the writer, only committed git state is shared. This re-affirms dec-025's Option A
and re-applies its Option B rejection: an uncommitted staging file is invisible to the dashboard's stateless
`suggestions_read`. The *semantic* "staging" survives as the `pending` status; the storage is committed.

**Lifecycle mutated in place, one commit per transition.** `pending → approved` and `pending → rejected` are
P3's transitions (via the `dry-run|apply` `suggestion_decide` tool), each an own `VaultTransaction` (dec-008).
`approved → ingested` (+ `ingest_ref`) is P4's transition; P3 reserves the value and the field. A decision on
a non-`pending` record is refused with a typed error.

**Candidate as an opaque dict.** `SuggestionRecord.candidate` is `dict[str, object]`, not a typed
`SourceCandidate`. Typing it as `discovery.SourceCandidate` would drag `discovery/` onto the MCP cold-start
path (dec-013 violation, fitness-test break). Storing the frozen `to_record()` dict keeps `core/records.py` a
leaf with no edge into `discovery/`. The nested dict carries P2's own `schema_version`, so the outer record and
the inner candidate version independently.

**Observe-safety.** `suggestions.jsonl` lives under `.knotica/suggestions/` — a `.knotica/`-but-not-`prompts/`
path, which `_content_changed_since` classifies bookkeeping; a suggestion write never re-triggers
`observe_default`.

## Considered Options

### Option A — committed append-only suggestions.jsonl, candidate as opaque dict (chosen)
- Pro: cross-process readable (the dashboard's whole reason to exist); uniform physics with `gaps.jsonl`;
  dec-006 probe; observe-safe; import boundary preserved; two independent version probes.
- Con: git-history growth (bounded by `(gap_id, source_key)` dedup; resolved-record pruning deferred, additive).

### Option B — uncommitted staging file (the golden.staging.jsonl precedent the brief cites)
- Pro: no history bloat; pending rows never enter git until acted on; honors the cited precedent.
- Con: a stateless MCP server (separate process) cannot read an uncommitted file the loop/CLI wrote — fatal to
  the dashboard read path, the exact reason dec-025 Option B was rejected for the gap queue. Rejected.

### Option C — candidate typed as SourceCandidate in core/records.py
- Pro: static typing of the nested fields.
- Con: pulls `discovery/` onto the MCP cold-start path (dec-013), breaks the `mcp_server ⊬ discovery` fitness
  test. Rejected.

### Option D — a second "instruction" file for approved suggestions
- Pro: separates the approval queue from the proposal queue.
- Con: an `approved` status on the one record already *is* the queued instruction; a second file duplicates
  state and adds a sync surface. Rejected (Simplicity First).

## Consequences

**Positive:**
- The dashboard + interactive client read one committed artifact per topic, filterable by status; `wiki_status`
  reports lifecycle counts. The record is self-describing (dec-006) and the P4 hand-forward is explicit.
- `core/records.py` stays discovery-free; the cold-start boundary is intact and fitness-tested.
- One commit per transition preserves the one-commit-per-mutation invariant and the audit trail.

**Negative / costs:**
- Committed `pending` rows enter git before human review (marked `pending`; the gate is the decision, not the
  write). The file grows with distinct proposed candidates; pruning of terminal rows is a deferred additive pass.
- The candidate is not statically typed at the outer record (boundary validation happens in P2's `from_record`
  when a discovery-aware consumer rehydrates).

## Disconfirmation

- **Falsifier:** If the dashboard read path were ever collapsed into the same process as the discovery writer
  (e.g. a monolithic local CLI that both discovers and reviews), the cross-process argument would dissolve and
  an uncommitted staging file (Option B) would avoid history bloat with no downside.
- **Steelmanned runner-up:** Option B is strongest under a same-process reader; it matches the cited golden.py
  precedent and keeps machine-proposed noise out of permanent history until a human blesses it. It fails only
  because knotica's stateless-server invariant puts the writer and the reader in different processes sharing
  state solely through committed git.
- **Reversal trigger:** If `suggestions.jsonl` grows past a few hundred lines in practice, add a prune step
  (drop terminal `rejected`/`ingested` rows beyond a retention window) — additive, no schema change. If the
  reader and writer ever become one process, revisit the staging split.

## Prior Decision

Re-affirms **dec-025** (the committed gap-queue rationale) and **dec-006** (self-versioned record discipline).
dec-025 established that a stateless-server queue read by a separate process must be committed, not staged;
`SuggestionRecord` adopts that verbatim for the sibling suggestion queue. It does not reopen or modify any
dec-025/dec-006 shape; it adds a new record kind under the same rules. A future supersession would require
evidence that the suggestion reader and writer share a process (making uncommitted state sufficient) or that
gap-fill suggestions should not self-version.

## Amendment (2026-07-19, pipeline in flight)

As-built reconciliation with `INTERFACE_DESIGN.md` §D2 (the interface layer owns surface schema per
the shadowing protocol): the lifecycle is **five states** (`pending/approved/rejected/deferred/
ingested` — `deferred` added for the review UX), the decision reason field is **`decided_reason`**
(shared by reject-required / defer-optional, replacing `rejection_reason`), and `proposer_version`
is not persisted on the record (the record's `schema_version` is the capability probe; formulation
versioning rides on the queue module, not each row). The committed-queue and opaque-candidate
decisions of this ADR are unchanged.
