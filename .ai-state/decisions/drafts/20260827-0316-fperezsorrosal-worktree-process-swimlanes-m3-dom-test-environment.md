---
id: dec-draft-783494cb
title: Add jsdom + @testing-library/preact to dashboard/ now, at M3
status: proposed
category: implementation
date: 2026-08-27
summary: M3 adds a DOM test environment (jsdom + @testing-library/preact) instead of deferring it again, closing the Step-6 residual gap where a rendered slot→content link could not be asserted.
tags: [dashboard, testing, process-swimlanes, m3]
made_by: agent
agent_type: implementation-planner
branch: worktree-process-swimlanes
pipeline_tier: full
affected_files:
  - dashboard/package.json
  - dashboard/vitest.config.ts
  - dashboard/src/lanes/LaneRail.tsx
---

## Context

M0's Step 6 needed to assert that a billed quote renders in a preview slot, never an outcome slot,
but `dashboard/` has no DOM environment — vitest runs against plain modules only. Step 6 substituted
a data-shape assertion ("the quote lands in the preview slot" as a property of the returned object)
for the intended rendered-DOM assertion, and explicitly deferred the DOM-environment decision to
"M3's decomposition pass," naming it as an M3 entry condition rather than silently absorbing the gap.

M3 is the first milestone where a shared rail component (`LaneRail.tsx`) actually renders, and where
two pre-existing components (`LoopPane`'s stepper, `IngestPane`'s pipeline) must be characterized by
their rendered output — not just their derived data — before either is rewritten.

## Decision

Add `jsdom` and `@testing-library/preact` (plus the peer `@testing-library/dom`) as `devDependencies`
in `dashboard/package.json`, and set `test.environment: "jsdom"` in `vitest.config.ts`, as M3's own
Step 58 — before any characterization or rail-rendering test is written.

## Considered Options

### Option A — Add jsdom + @testing-library/preact now (chosen)

- **Pros**: closes the Step-6 residual gap for good; enables the M3 entry-condition characterization
  net to assert rendered output, not just data shape; enables `LaneRail.tsx`'s accessibility-floor
  assertions (ARIA attributes, `data-state`) which are unassertable without a DOM.
- **Cons**: a new dependency pair; a small amount of test-suite runtime overhead (jsdom setup per
  test file).

### Option B — happy-dom instead of jsdom

- **Pros**: lighter weight, faster test runs.
- **Cons**: `@testing-library/preact`'s own documentation and ecosystem assume `jsdom`; using
  `happy-dom` would mean debugging compatibility edge cases this project has no prior experience
  with, for a marginal runtime saving on a test suite that is not yet large enough for that to
  matter.

### Option C — Defer again, to M4 (Learn)

- **Pros**: keeps M3's dependency surface minimal.
- **Cons**: M4's `IngestPane` rail rendering is exactly the second consumer this milestone's
  characterization net already protects — deferring again would re-litigate the same decision one
  milestone later with strictly less context (the reasoning for why it was deferred twice would need
  re-deriving), and M3 cannot satisfy its own entry condition (pinning `LoopPane`'s rendered stepper)
  without a DOM environment regardless.

## Consequences

- Positive: the rail component's accessibility floor becomes testable; the Step-6 gap closes;
  future lane rendering work (M4/M5) inherits a working DOM test setup at zero marginal cost.
- Negative: two new `devDependencies` to keep current; a small increase in `npm test` wall-clock
  time.

## Disconfirmation

- **Falsifier**: if `jsdom` proves incompatible with `@preact/preset-vite`'s JSX transform in
  practice (discovered during Step 58's implementation), the DOM-environment choice — not just the
  library — would need revisiting.
- **Steelmanned runner-up**: Option C (defer to M4) is the strongest alternative — it is the
  status-quo-preserving choice and does not force this milestone to absorb infrastructure work.
  It loses because M3's own entry condition (characterizing `LoopPane`'s rendered stepper before
  rewriting it) cannot be satisfied without a DOM environment regardless of which milestone adds it.
- **Reversal trigger**: if `dashboard/`'s test suite runtime becomes a measured bottleneck and
  profiling attributes it specifically to `jsdom` setup/teardown cost (not to the number of test
  files), revisit in favor of `happy-dom` or a lighter alternative.
