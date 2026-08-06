---
id: dec-064
title: Close adaptive threshold tuning — thresholds are not the binding constraint on anything measured
status: re-affirmation
category: architectural
date: 2026-08-02
summary: Adaptive per-topic threshold tuning existed to answer what looked like a calibration problem; dec-062 established the problem was candidate-window geometry, and Phase 3 then measured the result — hard-orphan rate flat across quote shapes and ordinary rewrites orphaning at 7.7% — so no measured quantity is threshold-bound and the item closes with no implementation effort spent.
tags: [notes, anchoring, calibration, thresholds, measurement, phase-4-gate, scope-closure]
made_by: user
agent_type: orchestrator
branch: worktree-notes-overlay-phase4
pipeline_tier: standard
re_affirms: dec-062
dissent: The shipped thresholds have been validated on exactly one vault with one topic, and "no measurement is threshold-bound" is a statement about the measurements taken rather than about the threshold; a corpus whose rewrites cluster differently could make per-topic tuning the cheapest available lever, and this closure discards a designed item to avoid re-deriving it later.
affected_files:
  - src/knotica/core/notes/resolve.py
  - src/knotica/core/notes_config.py
  - .ai-state/measurements/STEP1_ORPHAN_RATE.md
---

# Close adaptive threshold tuning — thresholds are not the binding constraint on anything measured

## Context

Adaptive threshold tuning — per-topic calibration of `guess_threshold` and
`complete_orphan_threshold`, rather than the single shipped pair — entered the notes-overlay scope
when the fuzzy rung appeared unreachable. A superseded draft (`dec-draft-c4b81d90`) had measured
that no non-verbatim edit exceeded ~0.64 against a 0.75 gate and concluded the ladder was
mis-calibrated. Per-topic tuning is the natural remedy for a calibration problem.

**`dec-062` established that the premise was wrong.** The draft had generalised from a single quote
shape (a sub-clause of a longer sentence); measured across shapes, a one-character typo scores
0.9946 when the quote is approximately a whole sentence. The limit was **candidate-window
geometry** — sentence-bounded windows capped at 2x the quote structurally cap the similarity ratio
— not the threshold. `dec-062` fixed the geometry in `candidates.py`/`scoring.py` and left
`guess_threshold` at 0.75 explicitly.

That left adaptive tuning as an item with no live motivation, carried forward on inertia. The
Phase 4 brief posed the charter question for it — *what measurement would make this unnecessary?* —
and answered it: the one already in `STEP1_ORPHAN_RATE.md`.

This item was never its own ADR. It was a standing position inside `SYSTEMS_PLAN` § Sequencing,
which predates every Phase 3 measurement.

## Decision

**Close adaptive threshold tuning as "not needed."** It is removed from the notes-overlay scope,
not carried forward as deferred. No implementation effort is spent. `dec-062`'s ruling — fix the
geometry, leave the threshold — is re-affirmed on the post-fix measurements it predicted.

### Ground 1 — the geometry fix did what it claimed, so the calibration premise is gone

Phase 3 measured hard-orphan rate **flat across quote shapes** on KB pages: whole-sentence 38.7%,
sub-clause 37.6%, two-sentence 38.9%. The shape-dependence that produced the original calibration
diagnosis is not merely reduced but absent. `dec-062`'s fix holds on realistic pages rather than
only on its own fixtures, and the shape axis is retired as a concern.

Per-topic tuning answers "this topic's edits score systematically differently from that one's."
The measured spread across shapes — the axis that actually varied — is now under two percentage
points.

### Ground 2 — nothing measured is threshold-bound

Every quantity Phase 3 and Phase 4 measured turned out to be bounded by something other than the
thresholds:

- **Residual orphaning** is bounded by rewrite *class*, not threshold: ordinary knowledge rewrites
  orphan at 7.7%, below the 8-20% band `dec-058` accepted, while one wholesale supersession
  produces 85% of all observed orphaning and would survive any threshold.
- **Review burden** is bounded by anchor density, not threshold: at 7.7% a topic needs ~13 anchors
  on a single rewritten page to generate one review item.
- **Read-time cost** is bounded by git subprocess spawn, not threshold or even resolution
  (`dec-065`, Step 1: 92-97% of wall-clock is process spawn).

A tunable that moves none of the measured constraints is not a deferred feature; it is a
non-feature.

### Ground 3 — a per-topic threshold has a real cost, not merely no benefit

Thresholds are the two numbers that decide, silently and vault-wide, whether a note is re-anchored
or sent to a human. Making them per-topic multiplies the configurations under which a silent
misplacement can occur, and `dec-062`'s own falsifier 1 — the fuzzy rung placing a note on the
*wrong* passage — is the failure mode with no detector. Adding a per-topic axis widens exactly the
surface `dec-058` chose a high threshold to keep narrow.

## Considered Options

### A — Close it as "not needed" (chosen)

Removes the item on measurement. Costs the option value of a designed lever, and leaves per-topic
variation unaddressed if it ever appears.

### B — Implement per-topic thresholds anyway, since the config seam exists

**Rejected.** `notes_config.py` already resolves thresholds per call, so the seam is cheap — but
cheapness is not motivation. Shipping a tunable nobody has a measured reason to turn creates a
support surface, a documentation obligation, and a new way for two topics to disagree about what
"orphaned" means, in exchange for no measured improvement.

### C — Keep it deferred rather than closed

**Rejected**, following `dec-063`'s reasoning for the block-ID spikes: an indefinitely deferred item
re-litigates on the same absent evidence indefinitely. That is how the unmeasured 8-20% estimate
survived three phases. The evidence exists now; the scope decision should consume it.

## Consequences

**Positive.** Phase 4 sheds an item at zero implementation cost. The thresholds stay a single,
globally-reasoned pair, which keeps `dec-058`'s silent-misplacement surface as narrow as it was
designed to be, and keeps every measurement in this project comparable across topics.

**Negative.** The shipped thresholds are validated against one vault, one topic, and a
predominantly LLM-authored rewrite population. If a future corpus does show systematic per-topic
divergence, this closure means re-deriving the case rather than resuming a scoped item. The
0.35-0.75 band's occupancy is also **not** as sparse as originally scoped — Phase 3 measured 30.5%
of resolutions inside it, populated by the heavy rewrite classes — so the band remains a live
review surface even though the thresholds bounding it are not the constraint.

**Newly surfaced, not addressed here.** `STEP1_ORPHAN_RATE.md` § "A correction to the brief" records
that the LLM adjudicator for the `[0.35, 0.75)` band was re-scoped rather than settled by the
measurement. That is a separate item from threshold tuning and is untouched by this closure.

## Disconfirmation

**Falsifier.** Either would make this decision wrong:

1. Hard-orphan or re-anchor rates are measured across **several topics** and diverge materially —
   more than the ~2-point spread Phase 3 saw across quote shapes — in a way traceable to the
   thresholds rather than to rewrite class. Per-topic calibration would then be addressing a real,
   measured difference.
2. A topic is found whose rewrite population sits predominantly inside the `[0.35, 0.75)` band, so
   that most of its anchors are neither auto-placed nor confidently orphaned. At that point moving
   *that topic's* gate is the cheapest available lever, and the global pair is the constraint.

**Steelmanned runner-up (Option B).** The strongest case for shipping it anyway: `dec-058` set the
thresholds high on the explicit reasoning *start high and let the user lower them* — it built an
escape hatch on purpose. Two phases later the project has declined to lower them (`dec-062`),
declined to make them adjustable (this ADR), and validated them on a single topic of a single
vault while asserting they generalise. The evidence that "thresholds are not the binding
constraint" is entirely evidence about *this* corpus, whose rewrites are LLM-authored by a loop
under the project's own control — a population that is unusually well-behaved by construction and
is precisely the one `dec-063` already flagged as possibly unrepresentative. The seam is one
config line; the cost of being wrong is a user whose notes orphan constantly and whose only
recourse is a code change. Refusing a cheap, reversible knob because the current corpus does not
need it optimises for a corpus of one.

**Reversal trigger.** Revisit when either holds: (a) hard-orphan rate is measured on two or more
topics and the between-topic spread exceeds the within-topic spread by a clear margin; or (b) a
user reports review burden concentrated in one topic while others behave, which is the field
signature of a topic-specific calibration problem and the first evidence that would not come from
this project's own instruments.

## Prior Decision

Re-affirms `dec-062` (anchor recovery is bounded by candidate-window geometry, not by
`guess_threshold`) **without superseding it**. Nothing in `dec-062` changes: the sub-span alignment,
the multi-sentence widening, and `guess_threshold = 0.75` all stand. What changes is the status of
the scope item that `dec-062`'s diagnosis left without a purpose — `dec-062` established that the
threshold was not the problem, and Phase 3's post-fix measurements confirm that no measured
quantity is threshold-bound, which retires adaptive tuning rather than merely deprioritising it.

**Evidence a future supersession would require:** not argument, but a hard-orphan or re-anchor-rate
measurement spanning **two or more topics**, showing between-topic divergence attributable to the
thresholds rather than to rewrite class. The instruments to produce it are committed
(`scripts/measure_real_rewrites.py`, `scripts/measure_rewrite_severity.py`); the missing input is a
vault with more than one active topic, not new code.
