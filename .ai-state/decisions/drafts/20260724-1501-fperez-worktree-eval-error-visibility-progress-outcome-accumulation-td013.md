---
id: dec-draft-fe068253
title: Accumulate per-example outcomes via a single-writer lock; fix td-013 in the write primitive
status: proposed
category: architectural
date: 2026-07-24
summary: One in-memory outcomes list under one lock in the loop evaluate closure (coherence) plus a unique-per-write temp file and non-raising write_progress (primitive safety) together subsume td-013.
tags: [evals, concurrency, progress, td-013, loop]
made_by: agent
agent_type: systems-architect
branch: worktree-eval-error-visibility
pipeline_tier: standard
affected_files:
  - src/knotica/core/loop_progress.py
  - src/knotica/core/loop.py
affected_reqs: [REQ-04, REQ-05]
dissent: Per-thread unique tmp names alone would stop the crash without a lock, but would not guarantee the accumulated list is coherent (lost updates across threads).
---

## Context

The eval must grow an `examples: [{id, status, error_class, detail}]` list as the
run proceeds, correct under `dspy.Evaluate`'s 4 scoring threads. Today
`write_progress` writes one shared `path.with_suffix(".tmp")` then `os.replace`;
under concurrency the 4 threads' renames interleave (`[Errno 2] .tmp -> .json`)
and the escaping `OSError` makes dspy cancel the whole run (**td-013**, open).
An accumulating multi-thread list cannot be correct until this is fixed.

## Decision

Two complementary layers:

1. **Primitive safety (the td-013 fix, self-contained in `loop_progress.py`):**
   replace the shared `.tmp` with a unique per-write temp file
   (`tempfile.mkstemp(dir=path.parent)`) + `os.replace`, and make the whole
   `write_progress` **non-raising** — swallow `OSError`, log at debug. A progress
   hiccup can never again cancel an eval. This alone closes td-013's crash.
2. **List coherence (in `loop.py`):** the `evaluate` closure — one process
   driving dspy's threads — owns an in-memory `outcomes` list and one
   `threading.Lock`. `_on_outcome`, `_on_example`, and `_on_substage` each take
   the lock, mutate/read the shared state, compose the **full** snapshot
   (phase/current/total/detail/substage + the current `examples`), and write.
   Holding one lock across read-append-write prevents lost updates — the
   "single-writer aggregation seam."

`read_progress` reconstructs `examples` (default `[]`); the list is bounded
(capped length + per-detail truncation). The existing whole-file `updated_at`
staleness (15 min) covers the multi-entry payload unchanged: an in-flight run
refreshes it on every write; a dead run's partial list goes stale/absent;
`clear_progress` removes it on completion.

## Considered Options

### Option 1 — Unique-tmp + non-raising primitive AND one lock in loop.py [chosen]

- Pros: crash fixed at the primitive (protects every caller, incl. the
  `tools_datasets` path); list coherent under threads; staleness semantics
  unchanged; server stays stateless (list is loop-process-local).
- Cons: two layers to reason about; a small in-memory list held during a run.

### Option 2 — Per-thread unique tmp only (no lock)

- Pros: minimal; stops the crash.
- Cons: does not make the accumulated list coherent — two threads read-modify-write
  the file and lose each other's appends. Insufficient for the feature.

### Option 3 — Guard `write_progress` with the existing per-topic flock

- Pros: reuses an existing lock.
- Cons: the vault git-mutation flock is the wrong instrument for a gitignored
  runtime heartbeat; couples progress to the mutation lock and risks contention
  with real vault ops. Rejected.

## Consequences

- Positive: td-013 resolved as a side effect; the run is never cancelled by a
  progress error again; the list is correct under 4 threads; no new persistent
  state, no git, no VaultStore.
- Negative: the loop process holds a small list in memory for the run's duration
  (bounded; loop-process-local, so the stateless-server invariant holds); two
  layers of thread-safety to keep in mind.

## Disconfirmation

- **Falsifier:** if concurrent `_on_outcome` calls from multiple threads drop an
  outcome, or if a forced write error still propagates and cancels a run, the
  decision is wrong.
- **Steelmanned runner-up:** unique-tmp alone (Option 2). It is the smallest
  possible change and genuinely stops the observed crash — attractive if the list
  were written by a single thread. It fails only because the accumulation is
  inherently multi-writer, so coherence (not just atomicity) is required.
- **Reversal trigger:** if the loop ever drives eval outcomes from multiple
  processes (not just threads), the in-memory-list-under-one-lock model breaks and
  the aggregation must move to an append-only on-disk log with a merge read.

## Prior Decision

Resolves td-013 (`.ai-state/TECH_DEBT_LEDGER.md`, dedup_key
`loop-progress-tmp-rename-race`): the shared-`.tmp` rename race and its
run-cancelling escaping exception. Flip `open → resolved` with the resolving
commit at merge.
