---
id: dec-097
title: The dashboard gets a test runner as a prerequisite, and the pane characterizations defer to the milestone that rewrites them
status: accepted
category: implementation
date: 2026-08-10
summary: "The architecture's characterization prerequisite names three TypeScript surfaces, but dashboard/ has no test runner of any kind, so that prerequisite is unwritable as stated; a vitest devDependency lands first, the two-phase client contract and the pane-routing allowlist are pinned in it, and the LoopPane stepper and IngestPane watermark characterizations move to a hard entry condition on the milestone that rewrites those files."
tags: [swimlanes, testing, dashboard, typescript, characterization, prerequisites, tooling]
made_by: agent
agent_type: implementation-planner
branch: worktree-process-swimlanes
pipeline_tier: full
affected_files:
  - dashboard/package.json
  - dashboard/vitest.config.ts
  - dashboard/src/paneRouting.ts
  - dashboard/src/toolClient.ts
  - .ai-state/TEST_TOPOLOGY.md
affected_reqs: [REQ-14, REQ-15, REQ-16]
dissent: "Adding a second test toolchain to a repository whose canonical verification chain is a single `make verify` creates a suite that `make verify` does not run and CI runs only on a path filter, so the most likely outcome is a vitest suite that rots — and the two-phase defect it is being stood up to cover is one whose real falsifier is a telemetry line observed at runtime, which the runner cannot assert either."
---

## Context

`SYSTEMS_PLAN.md § Prerequisites` P1 specifies a characterization net over four surfaces before
the redesign rewrites them: the `LoopPane` stepper's four `ready`/`current` derivations, the
`IngestPane` rail's monotonic watermark, the two working two-phase flows, and the `?pane=`
allowlist including its `golden → datasets` alias. Three of the four are TypeScript.

`dashboard/package.json` declares exactly two scripts, `build` and `dev`. There is no vitest, no
jest, no `@testing-library`. `.ai-state/TEST_TOPOLOGY.md` records the situation deliberately: the
repo-root Preact tree "has no pytest coverage at all", so `dashboard/src/**` is excluded from
`mcp-surface`'s file dependencies on the grounds that listing it would claim a Python run could
observe a change it cannot. **P1's TypeScript half is therefore not writable in the current
toolchain**, and the architecture did not notice.

A second finding sharpens it. `loop action=run_once` is **already two-phase on the server** —
`tools_dispatch_loop.py` accepts and threads `confirm`. The P2 defect is entirely client-side:
`toolClient.ts` never sends `confirm` and `LoopOnceResult` has no `confirm_nonce`. So the plan's
one money-semantics fix lands exclusively in TypeScript, in a tree with no way to assert anything
about it.

## Decision

**Stand up vitest in `dashboard/` as prerequisite P0**, before P1 needs it: a `devDependency`, a
minimal config reusing the existing `@preact/preset-vite` aliases, a `test` script, and one smoke
test. No runtime dependency, no bundle impact, no change to `build`. The current version is
verified before pinning.

**Pin in it what the current step actually needs**: the `?pane=` allowlist and its alias — after a
small behaviour-preserving extraction of the inline logic from `App.tsx` into a pure
`paneRouting.ts` — and `toolClient`'s two-phase methods over an injected fake transport, which is
the contract the `run_once` fix must reproduce.

**Defer the `LoopPane` stepper and `IngestPane` watermark characterizations to M3**, as a hard
entry condition on that milestone rather than a prerequisite of M0. Neither file is touched by M0
or M1; both are rewritten by M3. Pinning them now and carrying them unchanged through a milestone
that does not touch them buys nothing, while their extraction cost is real — these derivations are
inline JSX and would each need a refactor or a component-render harness to become assertable.

## Considered Options

### Ship P1 as written, without a runner

Impossible. Three of its four surfaces have nowhere to run.

### Skip the TypeScript characterization entirely

Rejected. It would leave the one money-semantics fix in the plan — a billed action whose confirm
leg is currently dropped on the floor — with no automated assertion at any level, in a project
whose stated invariant is that billed actions stay two-phase.

### Add vitest *and* a component-render harness now, and pin all four surfaces

Rejected as premature. `@testing-library/preact` plus render harnesses for two 1 400-line
components is a substantial addition whose only consumer is a milestone two steps away, and those
components are being deleted by that milestone.

### Add vitest now, pin the pure surfaces, defer the component ones to M3 *(chosen)*

The smallest thing that unblocks the current work while putting the harness in place for the
milestone that genuinely needs it.

## Consequences

**Positive.** The two-phase client contract becomes assertable, which is the point. `paneRouting`
becomes a pure module, which M1's lane alias map extends rather than re-deriving. M3 inherits a
working runner instead of standing one up under time pressure, in the milestone with the largest
rewrite in the plan.

**Negative.** A second toolchain exists that `make verify` does not invoke; `.github/workflows/
dashboard.yml` runs on a `dashboard/**` path filter only. The suite can rot unobserved. Two
mitigations are available and neither is taken here: adding `npm test` to `make verify` (which
would make the canonical chain depend on Node) or adding a `dashboard` group to
`TEST_TOPOLOGY.md` (which the topology's own reasoning currently argues against). This is left as
an explicit open item rather than resolved silently.

**Negative.** Deferring two of P1's four surfaces is a plan amendment against the architecture and
needs approval. If M3 is entered without them, the largest rewrite in the plan proceeds with no
safety net over the exact code it replaces.

**Neutral.** AC-03's real falsifier — `record_two_phase` emitting `outcome=confirmed` — remains a
runtime observation against the telemetry sink, not something either toolchain asserts. The runner
covers the call shape; the sink covers the effect.

## Disconfirmation

**Falsifier.** If `npm test` is not run by anything a developer or CI does by default within one
milestone of landing, the suite is decoration. The check is cheap: does the dashboard workflow, or
`make verify`, or a hook, invoke it without a human choosing to?

**Steelmanned runner-up.** Skipping the runner and verifying P2 by hand is defensible for exactly
one step — the change is six files and a human can click preview-then-confirm and read the
telemetry line. The counter is that M3 rewrites 2 800 lines of the same tree, and the runner has
to exist by then regardless; standing it up on a six-file change is the cheapest moment to learn
it.

**Reversal trigger.** If M3 is descoped or the dashboard re-cut is abandoned, the vitest suite has
one consumer and should be re-evaluated rather than grown.
