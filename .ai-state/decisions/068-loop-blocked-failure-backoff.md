---
id: dec-068
title: Pace a failed observation eval by the error's own retryable contract
status: accepted
category: architectural
date: 2026-08-02
summary: The loop now reads KnoticaError.retryable to tell a transient eval failure from a blocked precondition, holding the blocked case for an hour instead of a minute, and build_loop_runner resolves the [loop] cadence config itself so no call site can silently disable the throttle.
tags: [loop, eval, retry, backoff, cadence, config-wiring, vault-hygiene, single-source-of-truth]
made_by: agent
agent_type: orchestrator
branch: main
pipeline_tier: standard
affected_files: [src/knotica/core/loop.py, src/knotica/core/loop_retry_backoff.py, src/knotica/core/loop_state.py, src/knotica/core/loop_factory.py, tests/test_loop_blocked_failure_backoff.py, tests/test_loop_factory_cadence_wiring.py]
dissent: "A one-hour blocked floor is a magic number defended by no measurement, and it is the wrong shape of fix: the loop still writes two commits per attempt, so the same class of bug returns at 1/24th the rate the moment another permanent failure appears that the error contract does not mark non-retryable. Suppressing no-op bookkeeping commits outright would have fixed the family rather than this instance."
---

## Context

A live vault reached **14,845 commits, of which ~14 were content**. The rest were loop bookkeeping: `observing …` and `observation eval error …`, two per attempt, roughly one attempt per minute per topic, for five days.

Two independent defects combined.

**The loop treated every failure as transient.** The re-arm design (`td-011`) deliberately leaves the cursor unadvanced when an observation eval raises, so the content is retried rather than silently skipped — correct for a 429 or a flaky clone. `_FAILURE_RETRY_FLOOR_SECONDS = 60` bounded that retry, and its comment names the risk precisely ("a real spend/log-noise risk"). But a topic with no frozen golden set raises `GoldenSetMissingError` on *every* attempt: a precondition that no retry can satisfy, paced as if it were a blip.

**The cadence config reached nothing.** `[loop] eval_min_interval_hours` is parsed, validated, unit-tested, and editable through the `loop action=cadence` MCP tool. Both real construction sites — `cli/loop.py` (the watcher) and `service/manager.py` (the daemon) — called `build_loop_runner` without it, so every runner ran at the `0.0` eager default. Measured: a vault configured `eval_min_interval_hours = 1` re-attempted 15 times in 20 minutes on a freshly restarted daemon.

The information needed to tell the two failure kinds apart already existed and was simply unread: `KnoticaError` carries a `retryable` contract, and `NOT_CONFIGURED` is not in `RETRYABLE_CODES`.

## Decision

**Pace the retry by the failure's own declared retryability**, and **resolve cadence config in the shared factory**.

- New leaf `core/loop_retry_backoff.py` owns retry pacing: `FAILURE_RETRY_FLOOR_SECONDS = 60` (transient), `BLOCKED_RETRY_FLOOR_SECONDS = 3600` (blocked), `is_retryable_failure(exc)`, `retry_floor_seconds(retryable=)`.
- `is_retryable_failure` reads `getattr(exc, "retryable", True)`. An exception making no claim is treated as transient — the safe default, since misclassifying a transient failure as blocked would stall real recovery for an hour.
- `LoopState` gains `last_failure_retryable: bool = True` (additive, defaulting to the pre-existing meaning).
- `build_loop_runner` resolves `resolve_loop_cadence_config()` when the caller passes no `eval_min_interval_hours`. An explicit value still wins.

The blocked floor is long but **finite**: the operator action that unblocks a topic (freezing a golden set) writes under `<topic>/.knotica/`, which `_content_changed_since` ignores by design, so a never-expiring block would outlive its own cause and need a restart.

## Considered Options

### Default the cadence at each call site
Pass `resolve_loop_cadence_config()` in `cli/loop.py` and `service/manager.py`. Rejected: per-call-site defaulting is exactly what failed — two sites, both forgot, silently. Defaulting in the one shared factory makes the omission unexpressible.

### Suppress no-op bookkeeping commits
Skip the state write when the new state is materially identical to the stored one. Strictly better in kind — it fixes any permanent-failure family, not just the ones the error contract marks. Rejected *for now*: the attempt sequence writes `evaluating` before the eval and `failed` after, so both writes differ from the prior state and a correct suppression needs a notion of "materially identical" that spans the pair. Larger and riskier than the defect warranted today. Recorded as the `dissent` and as tech debt.

### Never retry a blocked failure until content changes
Simplest to reason about, and wrong: the unblocking write is bookkeeping the content check ignores, so the loop would stay blocked after the operator fixed it.

## Consequences

**Positive**
- A blocked topic writes ~2 commits/hour instead of ~2/minute — a 60x reduction, before cadence config is even considered.
- `eval_min_interval_hours` becomes real; the documented feature now behaves as documented.
- Transient recovery is untouched: same 60s floor, same re-arm semantics.
- Retry pacing is a named, testable leaf rather than two constants inside a 1145-line module.

**Negative**
- `BLOCKED_RETRY_FLOOR_SECONDS = 3600` is a chosen constant, not a measured one.
- A permanent failure raised as a bare `Exception` (no `retryable` claim) still paces at 60s. The safe default protects recovery at the cost of leaving that case unfixed.
- Vaults already bloated are not repaired; history rewriting is out of scope.

## Disconfirmation

**Falsifier.** A blocked topic that keeps growing history at a rate the hourly floor cannot explain would show the pacing is not where the commits come from — pointing instead at the per-attempt double write, i.e. the option deferred above.

**Steelmanned runner-up.** Commit suppression is the better fix and this ADR concedes it: it addresses the family (any attempt that changes nothing writes nothing) rather than the instance (failures the error contract happens to mark). This decision buys a 60x reduction cheaply and safely today; it does not claim to be the last word.

**Reversal trigger.** Revisit when a second permanent-failure class appears that `retryable` does not capture, or when no-op commit suppression lands — at which point the blocked floor may become redundant and should be re-derived rather than kept out of habit.
