---
id: dec-035
title: Source-gate outcome surfacing — additive gate_outcome field, quarantine ref, bounded per-question diff, auto-mark_ingested
status: accepted
category: architectural
date: 2026-07-19
summary: A source-candidate gate verdict is surfaced by ONE additive nullable `gate_outcome` block on SuggestionRecord v1 (dec-030 Addendum sanction) carrying {verdict, scalar, baseline_scalar, refused_ref, regressed_questions[top-N]}; on merge the loop auto-flips approved→ingested (reusing the existing mark_ingested state-machine; the manual review action stays); on refuse the candidate branch is retained under a quarantine namespace (loop/x/…) rather than deleted, with the full per-question diff as a pointer, not an inline payload (dec-002 pointer discipline).
tags: [mcp, gapfill, phase-p4, source-gate, suggestion-record, schema-evolution, dashboard, wiki-status, dec-002, dec-030, refusal-semantics]
made_by: agent
agent_type: interface-designer
branch: worktree-hackathon-loop-ideas
pipeline_tier: standard
affected_files:
  - src/knotica/core/records.py
  - src/knotica/core/status.py
  - src/knotica/core/gapfill.py
  - src/knotica/mcp_server/tools_suggestions.py
  - dashboard/src/
affected_reqs: []
dissent: A distinct terminal `refused` lifecycle status would read more honestly than keeping a gate-refused source at `approved` with a gate_outcome annotation; it is rejected only to keep the five-state machine and the dec-030 lifecycle unchanged, at the cost of `approved_awaiting_ingest` conflating never-tried with tried-and-refused sources until the derived sub-count is read.
---

## Context

The P4 gate (`core/loop.py`) merges a source candidate that closes a gap without regression and must
**refuse** a dilutive one — "quarantined with the per-question diff attached, not silently deleted."
Two facts constrain the surfacing: today `_keep` FF-merges and `_discard` **DELETES** the refused
branch (`loop.py:882`); and the reader of the outcome (dashboard Suggestions pane, interactive client)
reads `SuggestionRecord`s via `suggestions_read` and the `wiki_status.suggestions` block. dec-030
Addendum sanctions **additive** schema evolution on `SuggestionRecord` v1 and sanctions P4 **automating**
the `approved → ingested` transition in addition to the retained manual `mark_ingested` review action.
The model consumer's context is a shared resource (dec-002): a full per-question eval diff must not ride
inline in every paged read.

## Decision

**One additive nullable field, `gate_outcome`, on `SuggestionRecord`** (schema_version stays 1 — additive,
dec-006 probe unchanged; dec-030 Addendum sanction). Null until a gate verdict exists. Shape:

```
gate_outcome: {
  verdict: "merged" | "refused",
  scalar: float, baseline_scalar: float,
  decided_generation: int,
  refused_ref: str | null,          # quarantine branch on refuse; null on merge
  regressed_questions: [            # bounded top-N (default 10, the agent-pagination cap)
    {qa_id, question, baseline_score, candidate_score, delta}
  ],
  diff_summary: str                 # "3 of 20 golden questions regressed"
} | null
```

**Merge path** (`_keep`, source kind): FF-merge as today, then the loop auto-performs the
`approved → ingested` transition through the **existing** `apply_decision(decision="mark_ingested")`
state-machine home (`core/gapfill.py:106,113`) — one commit, sets `status="ingested"` + `ingested_at`,
plus `gate_outcome.verdict="merged"`. The **manual** `mark_ingested` action in `suggestions_review`
stays (dec-030 Addendum): automation is *additive*, both call one state-machine.

**Refuse path** (source kind, gate fail, no arena — arena is prompt-only): the candidate branch is
**retained**, renamed to a quarantine namespace `loop/x/<topic>/source-<sid[:8]>` (analogous to
`loop/r/`), not deleted. The record's `status` **stays `approved`** (the source may still be good; the
distillation can be improved and re-submitted — the resumable open handshake supports retry);
`gate_outcome.verdict="refused"` with `refused_ref` pointing at the quarantine branch and
`regressed_questions` carrying the top-N per-question deltas from the eval manifest (dec-023). The
**full** per-question diff lives on the quarantine ref / a `.knotica/` audit artifact — a **pointer, not
a payload** (dec-002): `suggestions_read` returns the bounded top-N, never the full manifest.

**Surfacing deltas (all additive):**
- `suggestions_read` record wire dict gains `gate_outcome` (null until gated).
- `wiki_status.suggestions` gains `refused_awaiting_rework` = count of `approved` records with
  `gate_outcome.verdict == "refused"` (the existing `approved_awaiting_ingest`/`ingested` fields are
  unchanged; `refused_awaiting_rework` disambiguates never-tried from tried-and-refused).
- Dashboard Suggestions pane renders badges approved / refused (with a "view diff" affordance reading
  `refused_ref` + `regressed_questions`) / ingested. No new transport (dec-020).

## Considered Options

### A. Additive gate_outcome + quarantine ref + bounded diff + auto-mark_ingested (CHOSEN)
- Pros: minimal additive schema (one nullable field, five states unchanged); refusal is inspectable on
  the same read surface without a second call; the bulky diff stays a pointer (dec-002); merge automation
  reuses the one existing state-machine; retry is natural (status stays approved, resumable open).
- Cons: `approved_awaiting_ingest` conflates never-tried with refused until `refused_awaiting_rework` is
  read; a quarantine namespace (`loop/x/`) and its prune policy are new; the diff top-N is a lossy view
  (full manifest only on the ref).

### B. A distinct terminal `refused` lifecycle status
- Pros: reads most honestly; never conflates with approved.
- Cons: a sixth state changes the dec-030 five-state machine and every counter/filter that enumerates it
  — a wider, non-additive change; and a terminal `refused` fights the retry-after-rework flow (the source
  may still be good). Rejected on additive-minimality + retry grounds.

### C. Attach the diff to the GapRecord, not the SuggestionRecord
- Pros: the gap is the durable knowledge-need; a diff about "this source didn't close the gap" arguably
  belongs there.
- Cons: the reviewer reads *suggestions*, not gaps, at decision time; a cross-file join to render the
  refusal defeats dec-028's join-free denormalization. Rejected.

### D. Inline the full per-question manifest in gate_outcome
- Pros: one read, no pointer to follow.
- Cons: a full manifest (20+ questions × scores) displaces reasoning tokens on every paged read — the
  exact dec-002 pointer-not-payload violation. Rejected; kept as bounded top-N + ref.

## Consequences

**Positive:** the loop closes end-to-end and honestly — merges flip to `ingested` automatically, refusals
are quarantined and inspectable, and the whole outcome is additive on the v1 record, the status block, and
the dashboard (no existing field changes). The manual `mark_ingested` action survives for out-of-band
closes.

**Negative / costs:** a `loop/x/` quarantine namespace and a retention/prune policy are new (auto-prune
beyond newest-N, mirroring the `loop/r/` prune); `refused_awaiting_rework` is a derived count callers must
read to disambiguate; the top-N diff is lossy (full manifest on the ref only).

## Disconfirmation

- **Falsifier:** if reviewers can't act on a refusal without pulling the full manifest anyway (making the
  bounded top-N useless), or if a refused source is in practice never re-worked (making the retained
  branch + approved status pure clutter vs a clean terminal `refused`), the additive-annotation choice was
  wrong.
- **Steelmanned runner-up:** Option B's terminal `refused` status is the most legible model for a queue
  UI and matches the human-`rejected` precedent; its cost is only the five-state churn, which a
  pre-1.0 hackathon schema could absorb.
- **Reversal trigger:** if rework-after-refusal proves rare, collapse to Option B's terminal `refused`
  status and drop the retained branch; if the top-N diff proves insufficient, promote a dedicated
  refusal-diff read tool over the quarantine ref.

## Prior Decision

Builds additively on **dec-030** (SuggestionRecord v1; Addendum sanctioning additive evolution and P4
automation of `mark_ingested`) and re-applies **dec-002** (pointer-not-payload / bounded pages) and
**dec-028** (join-free denormalized record, action-parameterized review with the manual `mark_ingested`
action retained). Supersedes nothing.
