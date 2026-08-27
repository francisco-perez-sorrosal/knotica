---
id: dec-101
title: Close the type-orphan defect class via tsconfig flags, not a full linter
status: accepted
category: implementation
date: 2026-08-27
summary: M3 enables noUnusedLocals/noUnusedParameters in dashboard/tsconfig.json as the cheapest mechanical fix for the type-orphan defect class Step 5's light-review found; td-053's fuller linter/formatter gap stays open.
tags: [dashboard, typescript, tech-debt, process-swimlanes, m3]
made_by: agent
agent_type: implementation-planner
branch: worktree-process-swimlanes
pipeline_tier: full
affected_files:
  - dashboard/tsconfig.json
affected_reqs: []
---

## Context

Step 5's front-door deletion in `VaultPane.tsx` left two type-level orphans behind (an unused
`LoopOnceResult` import and a dead `ActionBusy` union member), caught only by an independent
reviewer — not by `tsc --noEmit` (which runs without `noUnusedLocals`), not by a linter
(`dashboard/` has none, `td-053`), and not by the built artifact diff (esbuild erases both
constructs, so the artifact was byte-identical before and after the fix). M3 dissolves nine files
(~5,700 lines) in one pass (Step 79), making this the milestone's dominant defect class by volume.

`td-053` already documents the broader gap (`dashboard/` has no linter or formatter config at all)
and recommends adopting the canonical `skills/typescript-development/assets/` baseline. That row is
`important`, not urgent, and was deliberately declined for the swimlanes project cycle on scope
grounds (named as "the sixth CI/quality item declined on scope grounds" alongside several others).

## Decision

Enable `"noUnusedLocals": true` and `"noUnusedParameters": true` in `dashboard/tsconfig.json` as
M3's Step 60, before the dissolution steps run. This converts the specific defect class Step 5's
review found (unused imports, dead union members) from a review-dependent finding into a mechanical
compiler gate. `td-053` (the fuller linter/formatter gap) is explicitly left `open` — this decision
does not resolve it.

## Considered Options

### Option A — tsconfig flags only (chosen)

- **Pros**: zero new dependencies; no blocker found (the tree already has zero known violations of
  the flags, verified by grep before deciding); converts exactly the defect class that bit this
  project into a gate, at the cost of two lines in an existing config file.
- **Cons**: does not catch formatting drift, style inconsistency, or any defect class outside unused
  declarations — a narrower gate than a full linter would provide.

### Option B — Resolve td-053 in full (adopt the canonical biome/eslint baseline)

- **Pros**: closes the broader gap in one motion; canonical assets already exist and install
  idempotently, per the coding-style rule.
- **Cons**: larger, unrelated surface for this milestone — a full linter also enforces formatting
  and style conventions M3 has no functional need to touch, and touching it here would produce a
  diff dominated by reformatting noise on the same commits that are already doing a large structural
  deletion, making the deletion itself harder to review.

### Option C — Do nothing, rely on light-review

- **Pros**: no config change at all.
- **Cons**: Step 5's own history is the counter-evidence — the defect was found only by an
  independent reviewer, not by any existing gate, and M3's deletion is an order of magnitude larger
  in file count than Step 5's single-file case.

## Consequences

- Positive: the M3 dissolution steps (79/80) get a mechanical backstop for the exact defect class
  most likely to occur at that scale; the fix is two lines with no new dependency.
- Negative: `td-053`'s broader gap (no formatter, no style linting) remains unaddressed by this
  decision; a future contributor could still introduce style drift undetected.

## Disconfirmation

- **Falsifier**: if enabling the flags surfaces existing violations elsewhere in `dashboard/src`
  during Step 60's implementation (contradicting this decision's stated expectation of zero), the
  narrower-scope framing would need revisiting — a nonzero pre-existing violation count would suggest
  the type-orphan problem is broader than Step 5's single known instance.
- **Steelmanned runner-up**: Option B (resolve `td-053` in full) is the strongest alternative — it
  is strictly more thorough and the canonical assets already exist, so the marginal authoring cost
  is low. It loses here specifically because of timing: introducing a formatter mid-dissolution would
  make the milestone's already-large deletion diff harder to review, not easier.
- **Reversal trigger**: if a second defect class (not caught by `noUnusedLocals`/`noUnusedParameters`)
  is found in a future milestone's dissolution — e.g., inconsistent formatting silently accepted
  across many new files — that is the signal to stop declining `td-053` and adopt the full baseline.

`td-053` (`.ai-state/TECH_DEBT_LEDGER.md`) remains the tracking record for the broader linter/
formatter gap this decision deliberately does not close.
