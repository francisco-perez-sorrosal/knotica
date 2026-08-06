---
id: dec-081
title: A forced eval clears the pacing holds
status: accepted
category: behavioral
date: 2026-08-06
summary: "`observe_default(force=True)` now clears the blocked/failure retry floor as well as cadence, because both pace the unattended watcher and the floor could not observe its own precondition being fixed; the observation hold (live ingest, quiet window) still applies, and the two-phase legs are now logged distinctly."
tags: [loop, retry-backoff, billed-action, two-phase-confirm, observability, human-intent, dashboard]
made_by: agent
agent_type: orchestrator
branch: fix-forced-eval-observability
pipeline_tier: standard
affected_files:
  - src/knotica/core/loop.py
  - src/knotica/mcp_server/dispatch_telemetry.py
  - src/knotica/mcp_server/tools_vault.py
  - src/knotica/mcp_server/tools_gaps.py
  - dashboard/src/LoopPane.tsx
  - tests/test_loop_blocked_failure_backoff.py
dissent: "The 3600s floor exists because an unattended loop once wrote 14.8k commits on an unevaluable topic; any hole in it is a hole in that defence, and 'the human asked' is exactly the argument someone will later extend to the daemon."
---

## Context

A user froze a golden set — the correct and only remedy for a topic blocked on
*"No golden set exists"* — then clicked **Run eval now** and **Confirm — run and bill**.
The dialog vanished in about a second and nothing ran. Repeatedly.

Three separate defects stacked into one unreadable experience:

1. **The retry floor could not see its own precondition being fixed.** A non-retryable failure
   carries a 3600 s floor, held while `is_same_content_retry` reports the content unchanged. That
   check compares *page content*; freezing a golden set is a `.knotica/` bookkeeping write, which it
   ignores by design. So the fix was structurally invisible to the guard, and **no correct action
   could clear it** — only waiting out the hour.
2. **The outcome was reported 800px from the click.** `confirmRunEval` cleared the preview banner
   and wrote the reason to `actionNote` at the foot of the pane. A confirm that billed nothing looked
   identical to one that worked: banner gone, no visible answer.
3. **The log could not tell the legs apart.** `record_dispatch` emits `tool/action/topic`, which is
   byte-identical for a free preview, a confirm that billed, and a stale confirm that silently fell
   back to a preview. Diagnosing this required standing up an instrumented server and driving the
   real UI through it, because no log line could answer *"did that click cost anything?"*.

## Decision

**`force=True` clears both pacing holds — cadence and the retry floor — not cadence alone.**

The retry floor and the cadence throttle are both pacing heuristics for the *unattended* watcher;
neither is a correctness gate. `force` is the human-intent signal and reaches `observe_default`
through exactly one caller: the two-phase, cost-quoted confirm. Every autonomous caller
(`service/manager`, `cli/loop`, `run_once`) leaves it false, so the watcher this floor was built to
restrain is untouched.

`_observation_hold` is deliberately **not** cleared. A live ingest or an unsettled quiet window says
the vault is mid-write — a fact about the data, not a pace, and not something human intent makes
safe to evaluate through.

Alongside it: the confirm's outcome is rendered where the click happened, with a headline that
answers only *did this bill*; and `dispatch_telemetry.record_two_phase` logs
`preview` / `confirmed` / `stale-confirm` for all three billed actions, the stale leg at `warning`.

## Considered Options

### Leave the floor, tell the user to wait

Honest and zero-risk to the 14.8k-commit defence. Rejected: the wait is not a remedy the user can
shorten by doing anything correct, and the thing they *did* do — freezing the golden set — was the
actual fix. Being told to wait an hour after fixing the problem is the definition of a stuck system.

### Clear `pending_retry` when a golden set appears

Targets this exact case. Rejected as too special-cased: it fixes *one* precondition failure and
leaves every other non-retryable cause (a missing credential, say) with the same trap, while adding
a coupling from the loop to the datasets layer.

### Widen `content_changed` to count `.knotica/` writes

Would make the floor see the fix. Rejected outright — that check is load-bearing elsewhere: counting
bookkeeping as content is precisely what made the loop eval its own writes, which the
"only loop bookkeeping changed" guard exists to prevent.

## Consequences

**Positive**

- Freezing a golden set and asking for an eval now works immediately, which is what the user
  expects and what every doc implies.
- The unattended path is byte-identical; the incident defence is intact where it matters.
- A billed click always states whether it billed, at the button and in the log.

**Negative**

- A determined human can now re-run a failing eval without the hour of enforced patience, and each
  attempt costs money. Mitigated by the two-phase confirm and its cost estimate, not by a timer.
- Two behaviours previously described as "forcing bypasses cadence only" have changed; three
  documentation sites and one docstring said so and are corrected.

## Disconfirmation

**Falsifier.** A blocked topic re-attempted *by the watcher* inside the floor — visible as a burst of
`observing …` / `observation eval error …` commit pairs — would mean `force` leaked into an
autonomous path and the defence is breached. `test_a_forced_eval_overrides_the_blocked_floor` pins
the unattended leg as still held, in the same test that pins the forced leg as passing.

**Steelmanned runner-up.** Leaving the floor absolute is genuinely defensible: it needs no argument
about who is asking, and "the human asked" is exactly the reasoning someone will later stretch to
cover the daemon, at which point the 14.8k-commit incident returns. The counter is that `force` is
not a mood but a specific parameter with one caller, and the test pins that.

**Reversal trigger.** If a second caller ever passes `force=True` — particularly anything on a timer
— this decision should be revisited before that lands, because the whole argument rests on `force`
meaning "a human is watching and has seen the price".
