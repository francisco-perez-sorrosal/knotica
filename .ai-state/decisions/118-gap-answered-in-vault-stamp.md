---
id: dec-118
title: A gap the vault already answers is a drain-time stamp on the gap record, not a derived view
status: accepted
category: behavioral
date: 2026-08-31
summary: The drain persists answered_in_vault_at on a gap whose entire candidate yield is already stored, and clears it the moment it stages a suggestion — so Home can surface the signal on all three surfaces as a plain record read, paying no discovery cost
tags: [gapfill, attention, home, record-schema, td-070]
made_by: agent
agent_type: implementer
branch: main
pipeline_tier: standard
affected_files:
  - src/knotica/core/records/
  - src/knotica/core/gapfill/drain.py
  - src/knotica/core/status.py
  - src/knotica/core/status_counts.py
  - src/knotica/cli/status.py
  - dashboard/src/lanes/home/attentionRows.ts
  - dashboard/src/lanes/home/attentionMeta.ts
  - dashboard/src/lanes/home/types.ts
---

## Context

A drain already computes `gaps_fully_in_vault`: the ids of gaps whose *entire* non-empty candidate
yield was dropped because the vault already stores those sources. Such a gap can never resolve —
only a merged source or a human dismissal closes one — yet costs a billed search on every drain, and
its real fault is retrieval or linking, not acquisition.

That finding existed only in the drain's one-shot result. An operator who did not run the drain
never saw it, and Home said "nothing needs you" over a gap queue that could not move (td-070).

The binding constraint is dec-092: the `view="attention"` payload pays no discovery, no lint walk,
no git subprocess, no note-anchor resolution. Re-deriving "is this gap answered by stored sources?"
at read time means running the URL join over every gap's candidates — i.e. discovery work — which
that view exists to refuse.

## Decision

Persist the observation where it is produced, and read it where it is cheap.

- `GapRecord` gains `answered_in_vault_at: str | None` — an additive-only optional field, schema
  stays v1, absent on pre-feature records.
- The drain **sets** it (to the drain's own UTC stamp) for each gap whose entire non-empty candidate
  yield was already-in-vault, and **clears** it (to `None`) for each gap the same drain staged at
  least one suggestion for — a stageable candidate being proof the vault does not already answer it.
  Both edits happen inside the drain's existing single transaction, which now declares `gaps.jsonl`
  alongside `suggestions.jsonl`. A drain with nothing to stamp, stage or heal remains a zero-commit
  no-op.
- No clearing pass on resolve/dismiss: every reader filters to `open`, so a terminal gap's stamp is
  moot.
- The attention view reports `gaps.answered_in_vault`, a count of open stamped gaps, derived by the
  same single `gaps.jsonl` read the row already pays for.
- All three surfaces render it in the `waiting` class: the dashboard's eighth attention row kind
  (`gaps_answered_in_vault`, anchored at Fill → Gap, where the evidence and the dismiss form are),
  and the matching CLI nudge line in the same urgency position. Both read the field defensively, so
  an older payload degrades by one signal rather than throwing.

## Considered Options

### Stamp the gap record at drain time (chosen)

- Pro: the reader pays one field read; the writer already had the answer in hand.
- Pro: survives across sessions and operators — the point of the debt item.
- Con: a second field to keep honest, and a clearing rule that must not drift from the setting rule.
  Mitigated by putting both edits in one function on one code path.

### Derive it in the attention view

- Pro: no schema change, no lifecycle rule, no staleness possible.
- Con: violates dec-092 outright — the derivation *is* a discovery-shaped join per gap, and the view
  is polled by Home on a timer.

### Report it only on the drain result (status quo)

- Pro: zero cost.
- Con: the debt item itself. The operator most likely to need the signal is the one who did not run
  the drain.

### A separate marker file under `.knotica/`

- Pro: keeps the record schema frozen.
- Con: a second source of truth for a gap's own state, with its own staleness and its own cleanup
  question; the record already carries `decided_reason` and `reported_reason` for exactly this kind
  of per-gap fact.

## Consequences

- Positive: a stalled-but-invisible class of gap is now visible on all three Home surfaces without a
  drain, and without weakening dec-092's budget.
- Positive: the stamp names *which* gaps, where `candidates_already_in_vault` could only give a
  topic-level total.
- Negative: the stamp is only as fresh as the last drain that queried that gap. A gap the `max_gaps`
  cap skipped keeps whatever the last drain that did query it concluded — accepted, because the
  alternative is computing it at read time.
- Negative: the drain's commit now touches two queue files. Still one commit per operation; the
  precedent is `apply_gate_outcome`, whose merged verdict closes its gap in the same transaction.
- Resolves td-070.
