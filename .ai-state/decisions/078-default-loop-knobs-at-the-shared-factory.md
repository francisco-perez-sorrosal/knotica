---
id: dec-078
title: Default every loop knob at the shared factory, not at each call site
status: accepted
category: behavioral
date: 2026-08-05
summary: "`build_loop_runner` now defaults `eval_window` and `arena_score` alongside `eval_min_interval_hours`, and `harness_evaluate` resolves `[models]` — closing three silent no-ops where config that parsed and validated reached no runner."
tags: [loop, arena, cadence, models, silent-no-op, factory-defaults, unattended-behaviour]
made_by: agent
agent_type: orchestrator
branch: main
pipeline_tier: standard
affected_files:
  - src/knotica/core/loop_factory.py
  - src/knotica/core/loop.py
  - src/knotica/service/manager.py
  - src/knotica/mcp_server/tools_vault.py
---

## Context

A documentation pass re-derived the loop's behaviour from source and found three knobs that parse,
validate, round-trip through an MCP tool, and reach no runner:

| Knob | Symptom |
|---|---|
| `[loop] eval_window` | Fully implemented in `LoopRunner` (`_cadence_hold`, `_within_window`, midnight wrap) and resolvable from `[loop]`, but no caller ever passed it. A documented quiet-hours strategy was inert. |
| `arena_score` | The OS daemon and `loop action=run_eval` built runners without it. Both guard sites require `arena_enabled AND arena_score is not None`, so the arena silently never ran for either. |
| `[models]` | `harness_evaluate` never resolved `ModelsConfig`, so the watcher, the daemon, `run_once`, and the ingest candidate gate all evaluated with packaged pins regardless of the operator's config. |

Each was invisible: nothing errored, nothing warned, and the surface that *reads* the config kept
reporting the value back. `loop action=cadence` would faithfully echo an `eval_window` that changed
nothing.

The arena case additionally contradicted the record. `dec-044` scoped the daemon supersession as
lifecycle-only and listed "heals prompt regressions in the arena" among the semantics **explicitly
unchanged**; `.ai-state/DESIGN.md` § 3b lists `service/` under arena prompt-healing as Built. The code
disagreed with both.

This is the same failure the codebase already diagnosed once. `loop_factory.py` carries a comment
saying `eval_min_interval_hours` "silently ran at the 0.0 default and never throttled anything — config
that parses, validates, and is editable through an MCP tool, yet reaches no runner", and fixed it by
defaulting in the factory. The fix was correct and did not generalize: its own sibling in the same
config object was left behind.

## Decision

**Default at the one shared seam, not at each call site.** `build_loop_runner` now resolves
`eval_window` from the same `[loop]` table it already resolved `eval_min_interval_hours` from, and
defaults `arena_score` to `heuristic_arena_score`. `harness_evaluate` resolves `[models]` once, which
covers every evaluating call site at once rather than requiring six of them to remember.

An explicit argument still wins in every case, so `--no-arena` continues to work (it flips
`arena_enabled`, which both guards also require) and tests that inject a stub are unaffected.

The generalized rule: **a knob whose default lives at the call site is a knob that will be forgotten.**
When a value must reach every runner, resolve it in the factory.

## Considered Options

### Fix each call site

Rejected. It is what produced the defect three times. Six sites pass `evaluate=harness_evaluate`
explicitly; two more omit the arena; a seventh will be added later by someone who reads none of this.
The factory exists precisely so that forgetting is not expressible — `dec-043` says it closes "the
silent-config-drift autonomy risk", which is this risk, named.

### Document the behaviour instead of fixing it

Rejected for the arena, because the documentation would have to contradict an accepted ADR and the
design doc. If the daemon genuinely should stay conservative, the honest expression is still a code
change — pass `arena_enabled=False` explicitly with a comment and amend `dec-044` — because today "off"
was expressed by an omission indistinguishable from an oversight. Rejected for the other two because
describing a config key as inert is worse than deleting it, and neither key deserves deletion.

### Remove `eval_window` rather than wire it

Rejected. It is fully implemented, tested at the unit level, exposed through `loop action=cadence`, and
solves a real problem — batching billed evals into quiet hours. The missing piece was one line.

## Consequences

**Positive.** Three documented capabilities become real. The daemon's behaviour matches `dec-044` and
`DESIGN.md` rather than contradicting them. The `arena_enabled=True` / `arena_score=None` footgun — a
signature that read "arena on" and behaved "arena off" — is gone.

**Negative, and worth stating plainly.**

- **The daemon now mutates prompts unattended.** On an arena win it overwrites
  `<topic>/.knotica/prompts/query.md` and writes several arena-state commits per race. This is what the
  CLI watcher already did and what the ADR always said the loop does, but it is newly true of the
  *supervised* path. `--no-arena` has no daemon equivalent; if that becomes a problem, the remedy is a
  config knob, not a re-omission.
- **An install that already has a `[models]` table gets a one-time baseline refreeze**, because the
  resolved snapshots rotate the harness fingerprint. That is the designed instrument-change path and is
  explicitly not a regression, but it is a visible one-off. Installs with no `[models]` table see no
  change at all: the resolved base is field-identical to the packaged default and the fingerprint is
  byte-unchanged.
- **`eval_window` now actually holds evals.** An operator who set it experimentally, saw no effect, and
  left it in place will find their loop deferring for real.

## Relationship to prior decisions

This supersedes nothing. `dec-043` introduced `build_loop_runner` to close the silent-config-drift
autonomy risk and stands unchanged; this decision extends its mechanism to the two knobs and the one
resolver that were left outside it. `dec-044` is likewise unchanged — the arena fix makes the code
match what that decision already said, rather than deciding anything new about the daemon.

Reversal trigger: an operator reporting that unattended arena promotion degraded a topic's prompts.
The remedy would be a daemon-side opt-out knob, not a return to expressing "off" by omission.
