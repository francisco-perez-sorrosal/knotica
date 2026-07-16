---
id: dec-draft-6fd2cfdf
title: Hand-rolled metric core, run by dspy.Evaluate (user override)
status: proposed
category: architectural
date: 2026-07-15
summary: The eval metric core stays a single hand-rolled pure callable score(gold, pred, trace=None) -> float|bool (the DSPy metric contract). Per a 2026-07-15 user override at the architecture checkpoint, dspy.Evaluate is adopted NOW as the per-example runner over that metric (not deferred to Phase 3a) — runner only, no optimizers/compilation and no dspy.LM/litellm; LLMClient, judge, and BaselineRunner are unchanged.
tags: [evals, phase-2, scorer, dspy, evaluate, sia, triple-consumer, build-vs-adopt, user-override]
made_by: user
branch: worktree-pipeline-eval-harness
pipeline_tier: standard
affected_files: [src/knotica/evals/scorer.py, src/knotica/evals/program.py, src/knotica/evals/harness.py, src/knotica/evals/__init__.py, pyproject.toml]
affected_reqs: [REQ-SCALAR-01, REQ-SCALAR-03, REQ-RUN-04]
dissent: Keeping the hand-rolled devset for-loop and deferring dspy to Phase 3a would keep Phase 2 free of dspy and its large transitive tree (litellm et al.), at the cost of not exercising the DSPy leg now and re-wiring the runner at 3a — the tradeoff the user judged not worth making.
---

## Context

Phase 2's evaluator must serve three consumers over its lifetime (the "triple-consumer contract"): the headless `knotica eval` CLI now, a DSPy metric in Phase 3a, and a SIA `evaluate(gen_dir)` in Phase 3b. The research fragment's Q1 build-vs-adopt table found the contract collapses to a single requirement: a pure Python callable `score(gold, pred, trace=None) -> float`, which *is* the DSPy `metric(gold, pred, trace)` contract (verified: float when `trace is None`, bool when `trace` is set). Full eval frameworks (Inspect AI, DeepEval) invert control (they own the run) and fight the contract; promptfoo is Node. The open sub-question was **who runs the per-example loop**: a hand-rolled devset for-loop, or `dspy.Evaluate` (a runner *over* the metric, not a metric core).

## Decision

**Hand-roll the metric core; adopt `dspy.Evaluate` as the per-example runner now.**

- The metric core stays a single hand-rolled pure callable `score(gold, pred, trace=None) -> float | bool` in `src/knotica/evals/scorer.py`. It accepts `trace` and branches (float per-example quality when `trace is None`; bool `= quality >= threshold` when `trace` is set) so it is the native DSPy metric — one `if`, no dependency implication. `gold` is a duck-typed `dspy.Example`; `pred` a `dspy.Prediction`.
- The harness runs that metric over the golden devset via `dspy.Evaluate(devset, metric=score, num_threads=1)(program)`, reading per-example `(gold, prediction, q_i)` from `EvaluationResult.results` and recomputing the topic scalar itself. `program` is a minimal `dspy.Module` (`BaselineProgram`) wrapping the unchanged `BaselineRunner` — the Phase-3a swap point (a compiled DSPy program takes the same seam).
- **Boundary (crisp):** `dspy` is the **runner only** — no optimizers, no compilation, and **no `dspy.LM`/litellm**. LLM access stays `LLMClient`/`AnthropicClient`; the judge and `MessagesApiRunner` are unchanged; the artifact under test remains the vault's own `query.md` driven by the baseline runner. Verified against dspy 3.x source: `dspy.Evaluate` calls `program(**example.inputs())` then `metric(example, prediction)` (2-arg → float branch), does **not** deepcopy the program across threads, and needs **no `dspy.settings.lm`** because our program never calls `dspy.Predict`.

## User Override (2026-07-15)

The systems-architect drafted this ADR originally recommending a hand-rolled devset for-loop with `dspy.Evaluate` deferred to Phase 3a (to keep Phase 2 dspy-free). At the architecture checkpoint the **user overrode the build-vs-adopt sub-decision**: adopt `dspy.Evaluate` as the runner **now**, with `dspy` added to the `evals` dependency group (`dec-draft-c2ad09bc`). The metric core stays hand-rolled; only the loop mechanism and the timing of the dspy dependency changed. `made_by` is set to `user` to record the decision authority. The health-guard spirit is preserved: no DSPy program/optimizer is written, and the artifact under test is unchanged — only the "no dspy dependency in Phase 2" clause is lifted.

## Considered Options

### Option A — hand-rolled metric core + `dspy.Evaluate` as the runner now (chosen, user override)
- **Pros:** the DSPy metric leg is *exercised* now (not just shape-compatible) and the Phase-3a optimizer reuses the exact runner + metric; parallel devset eval + result collection for free; the metric core is still hand-rolled (no control inversion of the objective function — dspy calls our `score`); Phase-3a swap is a drop-in behind the `program` seam.
- **Cons:** pulls `dspy` (large transitive tree incl. litellm) into the `evals` group now — mitigated because the group is off the built wheel (cold start untouched, `dec-draft-c2ad09bc`); binds Phase 2 to the dspy 3.x `Evaluate` API surface.

### Option B — hand-rolled metric core + hand-rolled devset for-loop; defer dspy to Phase 3a (original architect recommendation)
- **Pros:** Phase 2 dspy-free (no large tree, no API-drift exposure); the for-loop is ~5 lines.
- **Cons:** the DSPy leg is unexercised until 3a; the runner wiring is re-done at 3a; a subtle drift between "our loop" and "dspy's loop" could hide a metric-shape mismatch until the optimizer runs.

### Option C — adopt a full eval framework (Inspect AI / DeepEval) as the core
- **Pros:** mature rubric/metric libraries, sandboxing.
- **Cons:** control inversion fights all three consumers; heavy; framework-shaped lock-in for a need expressible in ~1 function. Rejected (unchanged from the original analysis).

## Consequences

- **Positive:** one metric, exercised by the same runner Phases 2 and 3a share; parallel eval available; the Phase-3a swap is a literal module replacement; the objective function stays a hand-inspectable pure function.
- **Negative:** Phase 2 depends on dspy 3.x (`Evaluate` API surface — pinned floor `dspy>=3.2`, drift risk tabled); `evals` group resolution is heavier (off the wheel, so cold start is unaffected); tests must import dspy (isolated to the eval subset).

## Disconfirmation

- **Falsifier:** if `dspy.Evaluate`'s contract proves unstable across 3.x minors (e.g. `.results` shape changes again, as `return_outputs` was already removed) such that the harness needs frequent rework, adopting it now over a stable ~5-line hand-rolled loop was the wrong call.
- **Steelmanned runner-up (Option B):** the per-example loop is trivial to hand-roll and fully under our control; deferring dspy would keep Phase 2 lean, drift-free, and dependency-light, and the Phase-3a wiring is small — the "exercise the DSPy leg now" benefit may not justify importing a large, fast-moving framework a phase early.
- **Reversal trigger:** revert to the hand-rolled loop (Option B) if (a) dspy `Evaluate` API churn imposes recurring maintenance, or (b) the dspy import/resolution cost becomes a real drag on the eval test loop, or (c) a future dspy version couples `Evaluate` to LM configuration in a way that breaks our no-`dspy.LM` boundary.
