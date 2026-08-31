---
id: dec-053
title: Capture per-example eval errors at the runner/metric seams, not by re-running
status: accepted
category: architectural
date: 2026-07-24
summary: Classify the dspy-swallowed per-example exception in the forward wrapper (runner) and the scorer metric (ok/judge), re-raising to preserve failure-scoring — no second model call.
tags: [evals, dspy, observability, rate-limit, no-double-bill]
made_by: agent
agent_type: systems-architect
branch: worktree-eval-error-visibility
pipeline_tier: standard
affected_files:
  - src/knotica/evals/harness/
  - src/knotica/evals/scorer.py
  - src/knotica/evals/error_capture.py
affected_reqs: [REQ-01, REQ-02, REQ-03, REQ-07]
dissent: A single scorer-level wrapper would capture answer and judge errors with gold.id directly and touch one fewer file, at the cost of missing runner-raise errors (the observed 429 case).
---

## Context

An observation eval runs 25 golden questions through `dspy.Evaluate`. When the
runner is rate-limited, the Anthropic 429 is raised inside the program's
`forward`; `dspy.Evaluate` catches it, records a failure-scored empty-prediction
triple, and logs the cause only to `loop.err.log`. The structured state that
`wiki_status`/the dashboard read never sees *why* an example failed — only that
the run aborted. We need the per-example error class (429 vs parse vs other) in
structured state, captured **without a second model call** (the path is
rate-limited and billed) and **without changing** the scalar or the
trustworthy-scalar refusal (`_reject_on_failures`).

## Decision

Capture at the two seams knotica already owns, classifying from the exception
object and re-raising so dspy's failure-scoring is unchanged:

1. **Runner errors** — wrap the inner `program(question=question)` call in the
   existing `_ProgressProgram.forward` (`harness.py`) in `try/except`; on
   exception, resolve the golden id via a `question→id` map and emit
   `on_outcome(id, "error", *classify_error(exc))`, then re-raise.
2. **Success + judge errors** — in the scorer's `score(gold, prediction)` (which
   already carries `gold.id`), emit `on_outcome(gold.id, "ok", "", "")` after a
   successful judge, or `on_outcome(gold.id, "error", *classify_error(exc))` on a
   `JudgeParseError` before re-raising.

`classify_error` lives in a new leaf module `evals/error_capture.py` importable
by both `harness` and `scorer` (the classifier cannot live in `harness` — that
would create a `harness → scorer → harness` import cycle). Re-raising everywhere
means `dspy.Evaluate` still produces the failure triple and `_reject_on_failures`
still aborts the run identically.

## Considered Options

### Option 1 — Forward wrapper (runner) + scorer metric (ok/judge), both re-raise [chosen]

- Pros: captures the observed runner 429; `ok` is emitted only after both answer
  and judge succeed (honest labels); zero extra model calls; uses owned seams;
  scoring and abort behavior byte-for-byte unchanged.
- Cons: two touch-points; the runner seam needs a `question→id` map (the metric
  seam has `gold.id` directly).

### Option 2 — Inspect the failed empty prediction post-run

- Pros: single point, post-run.
- Cons: fatal flaw — dspy has already swallowed the exception text; the failed
  prediction is an empty sentinel with no cause. Cannot classify 429 vs parse.

### Option 3 — Re-run failed examples to capture the error

- Pros: full fidelity.
- Cons: double-bills a rate-limited path — directly violates a health guard.

### Option 4 — A dspy error hook / custom failure callback

- Pros: single seam if it existed.
- Cons: dspy 3.2 exposes no stable public per-example error hook; relying on
  internals is more fragile than the forward/metric wrappers knotica owns.

## Consequences

- Positive: the 429 cause reaches structured state and the dashboard; no double
  billing; scoring/refusal untouched; classifier is dspy-agnostic and unit-testable
  with a fake failing runner.
- Negative: a `question→id` map is needed for the runner seam (rare duplicate
  question → fall back to the question string as key); two modules change instead
  of one; coupling to dspy's "program raises → metric skipped → failure triple"
  execution model (pinned by tests).

## Disconfirmation

- **Falsifier:** if a fake runner that raises a 429 does not yield an
  `examples` entry with `error_class="rate_limit_429"`, or if adding the seams
  changes the composed scalar / the abort on failure, the decision is wrong.
- **Steelmanned runner-up:** wrap only the scorer metric. It has `gold.id`
  directly (no map), touches one fewer file, and captures answer-plus-judge
  errors in one place. It is the better design *if* runner-stage exceptions were
  rare — but the observed, motivating failure (25/25 unscored under rate-limit)
  is a runner-stage raise that never reaches the metric, so a scorer-only seam
  would show an empty list exactly when the feature matters most.
- **Reversal trigger:** if judge-stage 429s become the common failure mode, or if
  a future dspy exposes a first-class per-example error hook, revisit — the
  `on_outcome` contract is already threaded and would absorb either change.
