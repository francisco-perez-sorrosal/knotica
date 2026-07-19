---
id: dec-024
title: Four-way fault classifier at the loop regression hook — ordered decision procedure + heal-redirect contract
status: accepted
category: architectural
date: 2026-07-18
summary: A deterministic ordered cascade classifies each regressed golden question into dilution / retrieval-fault / generation-fault / genuine-gap from the dec-023 v2 manifest, then routes prompt-cause to the existing arena heal and knowledge-cause to a persisted gap record; the arena is skipped only when every regressed id is knowledge-cause, and a classifier exception always falls through to heal.
tags: [loop, phase-3a, gap-fill, classifier, regression, arena, heal, retrieval-trace, dilution, import-boundary, fingerprint-neutral]
made_by: agent
agent_type: systems-architect
branch: worktree-hackathon-loop-ideas
pipeline_tier: standard
affected_files:
  - src/knotica/core/loop.py
  - src/knotica/core/gap_classifier.py
affected_reqs: [REQ-01, REQ-02, REQ-03, REQ-04, REQ-05, REQ-07]
re_affirms: dec-023
dissent: An always-fire-arena (additive-only) contract is simpler and never risks losing self-healing on a mixed regression; skipping the arena on an all-knowledge-cause regression is an optimization whose only proof that arena is futile is the dilution war-story, not a general guarantee.
---

## Context

The autoresearch gap-fill spine's P1 stage sits at `core/loop.py:339-345`, between the `regressed`
computation and `_heal_prompts_after_regression`. Today the loop blindly races prompt variants (the
arena) on *any* regression, regardless of cause. The war-story (0.96 → 0.9493, a diluting page displaced
a relevant one) proves this is sometimes futile: racing prompt bodies can never recover a page that
retrieval no longer fetches.

The dec-023 substrate (manifest schema v2, implemented at HEAD `3a6e0af`) now persists everything a
cause-diagnosis needs on the eval clone: per-question stable ids (`per_example[].id`), the retrieval
trace (`per_example[].pages`), and a cross-generation `held_out_delta` with per-id score deltas and
`pages_added`/`pages_removed` set-diffs. The golden set carries the reference page (`QARecord.pages_used`),
and a live page-existence check is available against the clone store. This decision defines *how* to turn
that substrate into a four-way verdict and *how* to route it — without touching the harness.

## Decision

**Ordered decision procedure** (first match wins), applied per regressed golden id (a regression at the
id level = `quality_delta < 0` or `qa_accuracy_delta < 0` in `held_out_delta.per_id`):

1. `reference_pages` non-empty **AND** none exist on the clone → **genuine_gap** (knowledge-cause).
2. `reference_pages` exist **AND** a reference page is in the current retrieval trace → **generation_fault**
   (prompt-cause).
3. `reference_pages` exist **AND** a reference page is in `pages_removed` **AND** `pages_added` is
   non-empty → **dilution** (knowledge-cause).
4. `reference_pages` exist, reference absent from trace, not a fresh displacement → **retrieval_fault**
   (neutral).
5. `reference_pages` empty / unlocalizable → **unclassified** (neutral).

The co-occurrence ambiguity (reference missing from trace AND a new page added) is resolved by
**precedence**: generation-fault (reference present) is tested before dilution; dilution requires *both*
a displacement *and* a new competitor; everything else with a missing-but-existing reference is
retrieval-fault.

**Heal-redirect contract.** After classification the loop:
- writes one gap record per `genuine_gap`/`dilution` verdict (see the companion gap-record-schema ADR),
  in its own `VaultTransaction`;
- `route = REDIRECT` (skip the arena) **iff** every regressed id is `genuine_gap`/`dilution` and at least
  one knowledge verdict exists; otherwise `route = HEAL` — call `_heal_prompts_after_regression`
  unchanged. Null/absent `held_out_delta`, an empty regression set, an unclassified id, or **any**
  classifier exception all resolve to `HEAL` (conservative default; self-healing is never lost).

**Failure isolation.** The classify + gap-write call is wrapped in `try/except` at the loop hook; on any
exception the loop surfaces a typed message and falls through to the existing heal path. The classifier
can never block or destabilize the observe/heal cycle.

**Placement & import discipline.** The classifier lives in a new `src/knotica/core/gap_classifier.py`
with core-only top-level imports (`core.records`, `core.page`, `core.transaction`, `store`); the golden
loader (`evals.golden.load`, which returns `QARecord`s without triggering golden's lazy `dspy` import) is
imported lazily inside the function, and the loop imports the classifier lazily (mirroring the existing
`run_eval` lazy import at `loop.py:886`). The classifier is not part of the eval harness, so it touches
none of `harness_version`'s fingerprint inputs — **fingerprint-neutral by construction** (re-affirms
dec-023 §Register-Objection #1). `loop.py` (924 LOC, already over the 800 ceiling) receives only a
~12-15 line hook, not classifier logic.

## Considered Options

### Option A — ordered cascade + confidence-gated redirect (chosen)

- Pro: total and deterministic; the dilution/retrieval co-occurrence has one principled answer; the arena
  is skipped exactly when it is provably futile; every other path preserves current behavior byte-for-byte.
- Con: a mixed knowledge/generation regression still runs the arena; a score-only regression predicate
  ignores citation-only moves.

### Option B — additive-only (always fire arena; gap records are pure signal)

- Pro: simplest routing; never risks losing self-healing.
- Con: wastes an arena race on a pure-dilution regression (the war-story), and loses the demo's
  "dilution caught → arena skipped → gap named" narrative. The gap record still gets written, but the
  loop's *behavior* does not change, undercutting the whole point of cause-diagnosis.

### Option C — aggressive redirect (skip arena if any knowledge-cause id exists)

- Pro: maximizes the redirect's reach.
- Con: drops self-healing on a mixed regression where a genuine generation-fault genuinely needs the
  arena. Violates the conservative Health Guard.

### Option D — threshold/magnitude-first classification

- Pro: trivially cheap.
- Con: score magnitude does not distinguish cause; a large drop can be any of the four classes. Rejected.

## Consequences

**Positive:**
- The loop stops racing prompts on regressions prompt-variation cannot fix; the war-story would now be
  routed to a `dilution` gap record instead of a futile arena race.
- Deterministic, no LLM, no harness change, no fingerprint rotation, no baseline churn.
- Reads one already-persisted artifact plus the golden set on the clone; clone-not-live-vault holds.
- The classifier has its own module and test surface; `loop.py` does not grow materially.

**Negative / costs:**
- A second routing outcome (`REDIRECT`) adds a branch to `observe_default`.
- `retrieval_fault` routes to heal even though retrieval-tuning would be its ideal remediation (out of P1
  scope); it is deliberately not persisted, to keep P3's discovery queue clean.
- The score-only regression predicate books citation-only regressions as non-regressed at the id level (a
  v2-classifier extension point).

## Disconfirmation

- **Falsifier:** If, in practice, all-knowledge-cause regressions are vanishingly rare (real regressions
  are almost always *mixed*), the `REDIRECT` branch essentially never fires, and the redirect is dead
  weight over Option B — the classifier would still be useful for gap-record emission but the
  heal-redirect half would be unjustified complexity.
- **Steelmanned runner-up:** Option B (additive-only) is strongest if we treat "never lose self-healing"
  as inviolable and accept a wasted arena race as cheap insurance. It fails only because the arena race on
  a pure-dilution regression is not merely wasted — it can *promote a prompt variant that masks the
  dilution*, moving the scalar without fixing the displaced page, which is exactly the silent-degradation
  failure the gap-fill spine exists to prevent.
- **Reversal trigger:** If telemetry shows `REDIRECT` firing on <5% of regressions over a representative
  window, or a mixed regression where the skipped-arena branch would have healed a generation-fault,
  revert to Option B and keep the classifier as a pure gap-record emitter (bump `classifier_version`).

## Prior Decision

Re-affirms **dec-023** (eval-manifest diagnostic substrate). dec-023 froze the substrate shape "without
building any consumer"; this decision is that consumer. It confirms dec-023's "necessary and sufficient"
claim — the four fault classes are all computable from `{per_id score deltas, per_id trace diffs,
QARecord.pages_used, a live clone page-existence check}`, with no fourth persisted substrate item
required — so dec-023's Falsifier does not fire. No dec-023 field is changed or reopened.
