---
id: dec-draft-0991c12f
title: An unreachable baseline is refused at freeze time and surfaced on Home — never auto-lowered
status: proposed
category: behavioral
date: 2026-08-30
summary: rebaseline mode=best refuses to freeze a high-water mark above the newest measurement (the one entry point that can create the baseline_unreachable state); the finding joins the Home attention inbox as a blocked row deep-linking Improve→Gate; drift-created unreachability still requires a human rebaseline by design
tags: [loop, gate, baseline, attention, home, ux]
made_by: agent
agent_type: orchestrator
branch: main
pipeline_tier: lightweight
affected_files:
  - src/knotica/core/loop.py
  - src/knotica/core/status.py
  - dashboard/src/lanes/home/attentionRows.ts
  - dashboard/src/lanes/home/attentionMeta.ts
---

# An unreachable baseline is refused at freeze time and surfaced on Home — never auto-lowered

## Context

A field report: under `baseline_policy=best`, a frozen baseline of 0.9581 sat
above the default branch's own measured 0.8923, so every candidate and arena
variant failed by construction. The server detected it after the fact
(`baseline_unreachable`) but only on the Improve→Gate stage of the full status
view; Home read "nothing needs you" over a jammed pipeline. Two code claims
contradicted each other: `rebaseline`'s payload comment called freezing the
stale high-water "a legitimate outcome", while `_baseline_unreachable`'s
docstring called the resulting state "always a misconfiguration".

## Decision

- **Freeze-time guard.** The observation freeze paths can never freeze above
  the branch (they freeze the measurement itself); `rebaseline mode=best` was
  the one entry point that could, and now refuses with a typed
  `INVALID_ARGUMENT` naming both scalars and pointing at `mode=latest`. The
  field report resolves the documented contradiction in favor of refusal.
- **Home surfacing.** The attention view's per-topic row carries
  `gate.baseline_unreachable` (same withholding rules as the full view:
  cross-instrument and probe anchors are unknown, not unreachable), and the
  dashboard derives a `blocked` row deep-linking Improve→Gate, where the full
  alert and the exact fix command already render.
- **No auto-rebaseline.** Drift *after* a legitimate freeze (the corpus
  regressed) still requires a human `rebaseline`: lowering the bar forgives a
  regression, so making it automatic would let the gate excuse exactly what it
  exists to catch.

## Considered Options

### Refuse at freeze + surface on Home, keep the human in the drift path (chosen)

- Pro: the only *creatable* unreachable state is blocked at its entry point;
  the *drift-created* one becomes visible where the user already looks, with a
  one-click route to the decision only they should make.

### Auto-rebaseline when unreachability is detected

- Rejected: a self-lowering bar is no bar — a regression would silently
  re-freeze the gate to its own degraded level.

### Warn at freeze but allow it

- Rejected: the repo's own detector calls the state "always a
  misconfiguration"; permitting a knowingly-jammed freeze with a warning makes
  the warning the reader's problem.

## Disconfirmation

- **Falsifier**: a real operator workflow that legitimately needs an
  aspirational bar above current measurement (e.g. a deliberate
  quality-recovery campaign) and is blocked by the refusal.
- **Steelmanned runner-up**: warn-but-allow preserves operator freedom and the
  `best` mode's literal semantics; the refusal narrows a documented behavior.
- **Reversal trigger**: a field report of the refusal blocking an intentional
  workflow — the fix would be an explicit override argument, not removing the
  guard.
