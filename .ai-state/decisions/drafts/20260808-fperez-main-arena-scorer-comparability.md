---
id: dec-draft-fbb07cef
title: The arena refuses a comparison it cannot make, and can now race on the real instrument
status: proposed
category: architectural
date: 2026-08-08
summary: A race carries its scorer's provenance and aborts rather than reverts when that scorer's scalars cannot be ranked against the gate baseline; `[loop] arena_scorer = "eval"` swaps the keyword heuristic for a real golden-set scorer.
tags:
  - arena
  - loop
  - eval
  - comparability
  - provenance
  - billed-action
  - configuration
made_by: agent
agent_type: orchestrator
branch: main
pipeline_tier: standard
affected_files:
  - src/knotica/core/arena.py
  - src/knotica/core/arena_eval.py
  - src/knotica/core/arena_resolve.py
  - src/knotica/core/loop_factory.py
  - src/knotica/core/loop_cadence_config.py
  - src/knotica/core/loop_state.py
  - src/knotica/evals/harness.py
dissent: Adding a billed per-variant eval path to a self-healing loop is how an unattended watcher becomes an unbounded spend; the abort alone fixes the reported incident and costs nothing.
---

## Context

Arena race `18c3899b843e` reverted all four prompt variants for failing to clear a baseline of 0.9548. It finished two seconds after gen 3 was written, in which the topic's own default branch scored **0.6562** on the same 21-question golden set. Every discarded variant beat the live corpus by 0.13–0.16.

The four scalars were `0.79`, `0.80`, `0.81`, `0.82` — exact 0.01 increments, on a topic whose real evals produce values like `0.6562261904761905`. That spacing is `heuristic_arena_score`'s signature, reproducible exactly: `0.40 + 0.28 + 0.06 + 0.04 = 0.78`, then `+0.01 × index`. The race was scored by a keyword heuristic and judged against an eval-derived bar.

The heuristic being a stub was never hidden — `CLAUDE.md` states that the arena's "only wired scorer is a keyword heuristic". What was hidden is that its output was being *ranked against a number from a different instrument*. And the outcome was unreadable: `reverted` is the normal terminal state for a race no variant won, so an unwinnable race and a fair loss produced the same word. Nothing on the record said which scorer ran, against how many examples, or on which golden set.

This is the same class of error the codebase already refuses elsewhere: `compute_gate` returns `unknown` rather than rank two scalars from different harness versions, and the dashboard renders `unknown` rather than imply a comparison it cannot make. The arena was the one place that ranked anyway.

## Decision

**A scorer travels with a descriptor.** `ScorerInfo` records the scorer id, whether its scalars are eval-comparable, the example count, the golden manifest, and the harness version. It is passed alongside the callable, never separately — a race recording one scorer's provenance beside another's scalars would be worse than recording none.

**An incomparable race aborts before scoring.** New stage `ArenaStage.aborted`, distinct from `reverted` precisely because that word already means "raced and nobody won". Nothing is measured, no variant is marked `lost`, and the message names the reason. Two ways to be incomparable: a scorer that is not eval-backed, or an eval-backed scorer on a demonstrably different golden set.

**Unknown provenance races and flags itself.** This deliberately diverges from `gate_inputs`, where an unknown component forces re-evaluation. The costs are not symmetric: there, proceeding replays a measurement nobody took and the fallback is one extra eval; here, refusing disables the topic's self-healing entirely, and would do so for every baseline frozen before `baseline_golden_manifest_sha` existed. So the race runs and sets `provenance_unverified`.

**A real scorer is available, opt-in.** `core/arena_eval.py` scores a variant by running the full golden-set harness with the variant body as `run_eval`'s new `instructions_override` — same retrieval, same judge, same golden set, same scalar formula, therefore the same `harness_version`. Only the prompt differs, which is exactly the comparison the arena exists to make. `[loop] arena_scorer` selects it; `heuristic` stays the default because a four-variant race over a twenty-one-question set is eighty-four worker+judge pairs, and that is a spending decision the operator makes.

**A failed eval-scorer build falls back to the heuristic without claiming comparability** — so the race aborts with a stated reason rather than quietly scoring on the wrong instrument.

Resolution happens in `build_loop_runner`, the one construction seam, for the reason its docstring already gives about the knobs before it: a call site that forgets is how documented config reaches no runner.

## Considered Options

### Provenance + abort + opt-in real scorer (chosen)

- Fixes the reported incident with the abort alone; the real scorer makes the arena mean something.
- Two new surfaces (a config value, a module) and a billed path that did not exist.

### Abort only; leave the arena permanently heuristic

- Smallest possible change, zero new spend.
- Leaves prompt self-healing structurally dead: with no comparable scorer, every race aborts forever. Honest, and useless.

### Make the eval scorer the default

- The arena works out of the box.
- Turns an unattended watcher into an unbounded spender on first regression, and breaks the lean install that has no `evals` extra.

### Rescale heuristic scores onto the baseline's range

- No new eval cost.
- Fabricates comparability. A keyword count mapped onto an eval range is still a keyword count, and the resulting number would be *harder* to distrust than the current one.

## Consequences

**Positive.** A race is interpretable after the fact — `scorer_id`, `n_examples`, `golden_manifest_sha` on both the race and each variant. Historical rows report `unverified: true` rather than being silently re-read as measurements. `run_eval`'s `instructions_override` generalizes the seam `CompiledRunner` already used at the runner level.

**Negative.** Any caller injecting an explicit `arena_score` without a descriptor now aborts — correct, but it surfaced as several test failures that had to declare their premise. `LoopState` gains `baseline_golden_manifest_sha`, which is `None` on every pre-existing state, so those topics race in the `provenance_unverified` path until their next re-freeze. And the arena can now spend money, gated only by config rather than by the two-phase confirm the other billed paths use.

## Disconfirmation

**Falsifier.** A topic configured with `arena_scorer = "eval"` produces variant scalars that still do not track its own eval scalars — which would mean `instructions_override` is not in fact the only difference between the two measurements.

**Steelmanned runner-up.** "Abort only" is defensible on cost grounds alone: the arena has never once healed anything real, its scorer was always a placeholder, and the honest move is to disable it loudly rather than build a billed path for a feature whose value is unproven. If the eval-backed arena runs for a quarter without promoting a variant that survives, this is the right answer.

**Reversal trigger.** An unattended loop bills a materially unexpected amount through the arena, or the config flag proves too weak a gate and the billed path needs the same two-phase confirm as `run_eval`.
