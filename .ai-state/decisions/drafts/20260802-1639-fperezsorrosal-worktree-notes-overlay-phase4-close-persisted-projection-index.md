---
id: dec-draft-9e1d9377
title: Close the persisted projection index — the read cost is subprocess overhead, not resolution
status: re-affirmation
category: architectural
date: 2026-08-02
summary: A drift-queue open is user-visible from 8-10 notes, but 92-97% of that wall-clock is git subprocess spawn and only 3-8% is resolution, so dec-058's "resolution is free" premise measures correct and the cost belongs to three named redundancies in the git layer rather than to the derived-projection design an index would replace.
tags: [notes, anchoring, performance, measurement, projection-index, phase-4-gate, git-subprocess]
made_by: user
agent_type: orchestrator
branch: worktree-notes-overlay-phase4
pipeline_tier: standard
re_affirms: dec-058
dissent: Falsifier 2 said "costs enough to be user-visible", and it does — 14.8s at 100 notes is not a marginal breach, and this ADR declines the remedy the falsifier named on the strength of a repair plan whose 6x improvement is modelled rather than measured; if the three fixes underdeliver, the index will have been declined twice on reasoning rather than once on evidence.
affected_files:
  - scripts/measure_read_latency.py
  - docs/designs/notes-overlay/STEP1_READ_LATENCY.md
  - src/knotica/core/vcs.py
  - src/knotica/core/notes/store.py
  - src/knotica/core/notes/reconcile.py
  - src/knotica/mcp_server/tools_dispatch_notes_read.py
---

# Close the persisted projection index — the read cost is subprocess overhead, not resolution

## Context

`dec-058` chose a bi-partite anchor model — an immutable anchor of record plus a **derived** live
projection, recomputed on every read rather than persisted. It made that choice falsifiable, and
**falsifier 2** was the one Phase 3 left open:

> *"Read-time resolution measured on a realistic vault costs enough to be user-visible. Then the
> derived-projection premise ('resolution is free, so don't persist') no longer holds and a
> persisted, mutation-time-updated index becomes the right shape — which is Option B's locus by
> another route."*

The Phase 4 brief made this Step 1 and made it a gate: *"Do not design an index before this
measurement exists."* The measurement is free — `resolve_anchor` is a pure function and a vault can
be seeded in a temp directory — so it was run before anything was designed.

Phase 2 already made `read_note` O(1), so the remaining exposure is the **drift queue**, which
resolves every anchor in a topic on every open by design.

## Decision

**Close the persisted projection index as "not needed."** It is removed from Phase 4, not carried
forward as deferred. `dec-058`'s derived-projection choice is re-affirmed on measured evidence
rather than on estimate.

**The three redundancies the measurement exposed become the actual work** in its place. They are
recorded here as the decision's substance, not as an afterthought: closing the index is only
defensible *because* they exist and are cheap.

### Ground 1 — the falsifier's predicate is met, and its premise is nonetheless correct

Measured on a seeded, isolated vault (`scripts/measure_read_latency.py`; full method and tables in
`docs/designs/notes-overlay/STEP1_READ_LATENCY.md`):

| notes x anchors | drift-queue open | git share | resolution (cpu) |
|---|---|---|---|
| 10 x 1 | 1.47s | 92% | 0.11s |
| 50 x 1 | 6.69s | 96% | 0.27s |
| 100 x 1 | 14.78s | 96% | 0.56s |
| 200 x 3 | 64.98s | 96% | 2.32s |

A drift-queue open crosses one second at **8-10 notes** — below current usage, not a future
scaling concern. Falsifier 2's predicate is met without qualification.

But the premise it names — *resolution is free* — **measures correct**. Actual resolution is
**3-8% of wall-clock** in every one of the twelve scenarios; git subprocess spawn is **92-97%**.
The falsifier assumed cost would be proportional to resolution work, and inferred from
user-visible cost that the derived-projection design was wrong. The first half of that inference
does not hold, so the second half does not follow.

### Ground 2 — the cost is three redundancies, each cheaper to remove than an index

The git call count is exactly `2 x (2 x anchors) + 7 x queue_members`, a formula that predicted
every measured row (200 notes x 1 anchor, 30 queue members: predicted 1010, measured 1010). Every
factor in it is redundant work, not resolution:

1. **`read_file_at` spawns two processes per anchor.** It calls `_exists_at_ref`, then `show` —
   but `git show` already fails on a missing path, so the probe is redundant in the common case.
2. **The drift path resolves the whole topic twice.** `_drift_payload` already holds a resolved
   `NotesListing`, then calls `reconcile_notes`, which calls `list_notes` again. This is the
   dominant factor: at 100 anchors, moving drift-queue membership from 15% to 100% takes the call
   count only from 505 to 1100, because the doubled base listing swamps the per-member work.
3. **Blobs are re-fetched per anchor.** Anchors sharing a `(pinned_at, page)` pair — the normal
   case, since notes cluster on the same pages — each pay their own `git show` for byte-identical
   content. Nothing memoizes within a call.

A fourth observation, not a redundancy but recorded here because it shapes the fix: `reconcile_notes`
computes transitions for **every** queue member regardless of the page requested, so a paginated
drift open is O(topic), not O(page).

### Ground 3 — an index would work, and is the most expensive fix available

A persisted index removes the git reads, so it does address the measurement. It also reintroduces
precisely what `dec-058` declined it for: an invalidation obligation on every vault mutation, a
staleness failure mode with no natural detector, and a second source of truth for a value that is
currently always derivable. Paying that permanently to avoid deleting a redundant `_exists_at_ref`
call is the wrong trade at any note density this project will see.

## Considered Options

### A — Close the index; fix the three redundancies (chosen)

Removes a large structural item on measurement and replaces it with three local changes in
`vcs.py`, `reconcile.py`, and `tools_dispatch_notes_read.py`. Costs the option value of a design
that was already scoped (the dot-prefixed `.knotica/` seam is designed and invisible to
`iter_page_paths` and the loop watch).

### B — Build the persisted index, since falsifier 2 fired

**Rejected.** The falsifier is a conditional whose antecedent is an *explanation*, not just a
threshold: it predicts user-visible cost **because** resolution is expensive. Honouring a
pre-registered falsifier means honouring what it actually claimed, not just its headline number.
Firing it on a cost the design does not own would build the index to fix `git cat-file`.

### C — Build the index *and* fix the redundancies

**Rejected as ordering, not as substance.** If the fixes land first and the cost is still
user-visible, the index case becomes evidence-based and can be made then. Building both now spends
the structural cost before knowing whether it is needed, which is the sequencing error the
Phase 3/4 gate-first charter exists to prevent.

### D — Leave the index deferred rather than closed

**Rejected**, on the same reasoning `dec-063` used for the block-ID spikes: an indefinitely
deferred item is a standing invitation to re-litigate on absent evidence. The measurement exists
now; the decision should consume it.

## Consequences

**Positive.** Phase 4's largest structural item closes for the cost of one free measurement. The
instrument is committed and re-runnable, so the post-fix claim can be *measured* rather than
argued. The three redundancies are real, local wins that benefit `list_notes` on every surface —
not only the drift queue.

**Negative.** The three fixes are now built and re-measured, so this decision no longer rests on a
projection — but the margin at higher densities is thin. Git calls fell **5.05x** (505 to 100 at
100 anchors) against the modelled ~6x, and wall-clock fell **10-14x** (14.78s to 1.049s at 100
notes; 1.47s to 0.132s at 10). A drift open is snappy at every density this project has reached,
and crosses one second at 100 notes — *just* over, not comfortably under. Two further
non-structural reductions remain (memoizing `reconcile`'s own historical read, and the three
per-queue-member metadata calls); if both are spent and density keeps growing, falsifier 1 fires
and this decision is re-opened on its own terms.

The model was optimistic in one identifiable way, worth recording because the same reasoning will
be reused: it assumed `reconcile`'s `read_file_at(pinned_at, …)` would be memoized, but that read
lives outside `store.py`'s pass cache and still costs one subprocess per queue member.

The measurement is also single-run per scenario on one machine whose git spawn (17-22ms/call) is
slow relative to typical CI (3-6ms). The decomposition and the call-count model are
machine-independent; the absolute seconds are not.

**Newly surfaced, not addressed here.** The post-fix CPU floor — 0.24s of resolution at 100
anchors, 1.07s at 600 — is immune to every fix above.

## Disconfirmation

**Falsifier.** Either would make this decision wrong:

1. The three redundancies are fixed and a re-run of `scripts/measure_read_latency.py` still shows a
   drift-queue open above ~1s at note densities the project actually reaches. The cost would then
   not have been the redundancies, and the index case would be evidence-based.
2. Note density reaches the high hundreds of anchors per topic while the drift queue stays an
   interactive surface. The measured CPU floor (1.07s at 600 anchors) is untouched by the git-layer
   fixes, so at that density resolution *does* become the binding cost — the regime falsifier 2
   described, arrived at honestly.

**Steelmanned runner-up (Option B).** The strongest case for building the index anyway: `dec-058`
pre-registered this falsifier so the decision would not be made on argument, and the falsifier
fired — not marginally, but at 14.8s on a hundred notes and 65s on two hundred. This ADR answers a
fired falsifier by reinterpreting it, which is exactly the move pre-registration exists to prevent,
and it does so on a repair plan that has not been built or measured. The measurement also shows the
drift queue is O(topic) with no pagination relief, so the cost grows with vault success rather than
with usage of the feature. An index makes read cost independent of anchor count permanently; three
subprocess fixes make a linear cost cheaper by a constant, and linear-with-a-better-constant is
still linear. If note density grows the way a working knowledge system implies it should, this
decision defers the same structural work to a point where the vault is larger and the migration
costlier.

**Reversal trigger.** Revisit when either holds: (a) the three redundancies are fixed, the
instrument re-run, and a drift-queue open still exceeds ~1s at realistic density; or (b) a topic's
measured anchor count exceeds ~500 while the drift queue remains an interactive read surface. In
case (b) the right shape is memoized projections keyed by `(anchor, page-blob-sha)`, not the
mutation-time-updated index `dec-058` contemplated — the measurement points at the comparison work,
not at the storage layer.

## Prior Decision

Re-affirms `dec-058` (bi-partite immutable anchor of record plus derived live projection)
**without superseding it**. Nothing in `dec-058`'s model changes: the anchor record, the resolution
ladder, the thresholds, and the append-only correction rule all stand. What changes is the status
of its falsification path — falsifier 1 was closed by `dec-063` as unable to change the outcome,
and falsifier 2 is now measured: its predicate fires, its premise holds, and its inference does
not carry.

**Evidence a future supersession would require:** not argument, but a re-run of
`scripts/measure_read_latency.py` *after* the three redundancies are fixed, showing a drift-queue
open still above ~1s at a note density the project has actually reached. The instrument is
committed; the missing input is the fix, not new measurement code.
