---
id: dec-draft-c4b81d90
title: The fuzzy rung is unreachable at guess_threshold 0.75 — measured, and left unchanged pending a user ruling
status: proposed
category: architectural
date: 2026-07-31
summary: Measurement on a real vault shows no non-verbatim edit exceeds ~0.64, so guess_threshold 0.75 makes the fuzzy rung unreachable and every rewrite lands in review; the threshold is deliberately left at its shipped value because lowering it is a UX call the user owns.
tags: [notes, anchoring, calibration, thresholds, measurement, phase-3-gate]
made_by: agent
agent_type: orchestrator
branch: worktree-notes-overlay-phase2
pipeline_tier: full
dissent: Shipping a rung that provably never fires is dead code by another name; the honest alternatives were to lower the threshold or to remove the rung, and doing neither leaves the ladder describing behaviour it does not exhibit.
affected_files:
  - src/knotica/core/notes_config.py
  - src/knotica/core/notes/scoring.py
re_affirms: dec-058
---

## Context

`dec-058` set `guess_threshold = 0.75` and `complete_orphan_threshold = 0.35`, citing MSR:
start high, accept more orphans and more review, avoid silent misplacement. `SYSTEMS_PLAN`
recorded that Hypothesis's weights were **never validated against this corpus**. Phase 2
validated them.

## Decision

**Record the measurement; change nothing.** The thresholds ship at 0.75 / 0.35.

Measured after the candidate-window defect was fixed, over the design's own edit-class matrix:

| edit class | score | outcome |
|---|---|---|
| verbatim | 1.000 | `exact`/`shifted` |
| typo (1 character) | 0.636 | `orphaned` + guess |
| 1 word swapped | 0.627 | `orphaned` + guess |
| 2 words swapped | 0.607 | `orphaned` + guess |
| clause reordered | 0.585 | `orphaned` + guess |
| light paraphrase | 0.601 | `orphaned` + guess |
| heavy paraphrase | 0.522 | `orphaned` + guess |
| full rewrite, same topic | 0.401 | `orphaned` + guess |
| unrelated text | 0.000 | `orphaned`, no guess |

**The ceiling for any non-verbatim match is ~0.64. A single-character typo does not clear
0.75.** The ladder therefore has three reachable outcomes, not six, and rung 6 (`fuzzy`) is
unreachable.

**Why**: `(50·quote + 20·prefix + 20·suffix + 2·position)/92` approaches 1.0 only if all four
terms do simultaneously. The candidate window is sentence-bounded and so is *wider* than the
quote, which drags `quote` similarity down even for a one-character edit, while prefix/suffix
are compared across a window that moved. The normaliser assumes a simultaneity the geometry
does not permit.

## Considered Options

### Option A — record and leave unchanged (chosen)

- **Pro** — the threshold is a documented UX dial and the plan explicitly said to start high
  and let the user lower it. Changing it silently would substitute a measurement for a
  judgement the user owns.
- **Pro** — behaviour is safe: nothing is lost, every orphan but a total mismatch carries a
  guess, and the historical text is always retrievable.
- **Con** — ships a rung that provably never fires.

### Option B — lower `guess_threshold` to ~0.55–0.60

- **Pro** — matches the design's evident *intent*; typo, word-swap, reorder and light
  paraphrase become `fuzzy`, heavy paraphrase and below stay reviewed orphans.
- **Pro** — the separation is clean: same-passage edits cluster 0.40–0.64 and unrelated text
  scores 0.00, so there is a wide empty band and no risk of admitting garbage.
- **Con** — auto-placement is exactly what MSR warns about, and this is a user-facing
  trade-off, not an implementation detail.

### Option C — re-normalise the scorer so the terms can co-reach 1.0

- **Pro** — fixes the cause rather than the symptom.
- **Con** — abandons published weights for hand-tuned ones on a nine-point sample; the
  temptation to fit the measurement is the exact failure that produced an unvalidated
  constant in the first place.

## Consequences

**Positive** — the fuzzy/orphan rate is now measured rather than assumed, which is the input
Phase 3's block-ID spikes were meant to gate on.

**Negative** — until a threshold ruling, every rewrite lands in the review queue. Users with
churn-heavy topics will see a queue proportional to rewrite volume rather than to genuine loss.

## Disconfirmation

- **Falsifier** — if real usage shows users *accepting* nearly every guess in the review
  queue, the review step is friction without signal and the threshold is simply too high.
  Conversely, if users frequently *reject* guesses at 0.60+, 0.75 is vindicated.
- **Steelmanned runner-up** — Option B is probably right. The only reason it is not chosen
  here is authority, not evidence: the evidence points at it.
- **Reversal trigger** — the first accepted-guess dataset from real review sessions. That is
  also what `SYSTEMS_PLAN` defers to Phase 4 as "adaptive threshold tuning from accepted
  guesses"; this measurement says that work is needed sooner than Phase 4.

## Prior Decision

Re-affirms `dec-058`'s thresholds as *shipped values* while contradicting its implicit
expectation that `fuzzy` would be a commonly reached rung — its own worked example predicted
a paraphrase scoring ≈0.78 and resolving `fuzzy`; the measured value for a comparable edit is
0.52. Bears directly on `dec-058`'s no-block-IDs falsifiers: the automatic re-anchor rate is
currently limited by this threshold, not by the absence of block IDs, so Spikes 3a/3b should
not be run until the threshold question is settled and re-measured.
