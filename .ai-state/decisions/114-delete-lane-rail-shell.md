---
id: dec-114
title: The generic lane-rail shell is deleted; every railed lane hand-rolls against the class contract
status: accepted
category: architectural
date: 2026-08-31
summary: dashboard/src/lanes/LaneRail.tsx had no production consumer across a full redesign — each railed lane needs a different body-swap rule — so the shell is removed and the shared layer keeps only the framework-free state derivation
tags: [dashboard, lanes, rail, dead-code, accessibility]
made_by: agent
agent_type: implementer
branch: main
pipeline_tier: standard
dissent: A shared rail is the natural home for the `.lane-stage` class contract and the "state is never colour-alone" floor; deleting it leaves four hand-rolled rails free to drift apart, and the next lane has no shell to start from.
affected_files:
  - dashboard/src/lanes/laneRailState.ts
  - dashboard/src/lanes/improve/ImproveLane.tsx
  - dashboard/src/lanes/answer/__tests__/AnswerLane.test.tsx
  - dashboard/CLAUDE.md
supersedes: dec-093
---

# The generic lane-rail shell is deleted; every railed lane hand-rolls against the class contract

## Context

`dec-093` chose a single lane-rail contract with `laneRailState.ts` as the
framework-free derivation and `LaneRail.tsx` as its renderer. The derivation
half succeeded — `LearnLane`, `AnswerLane` and `TendLane` all consume
`deriveSequenceStages`/`deriveChecklistStages` today.

The renderer half never landed a consumer. `ImproveLane` documented why in its
own docblock: the shell always renders a disclosure toggle for
`active`/`complete` rows, whereas Improve must swap in one of six different
real components at exactly one position. Every other railed lane found its own
version of that reason. Across the whole lane-rail redesign and the swimlane UX
redesign that followed, `LaneRail.tsx` was rendered by exactly one thing: its
own test suite. `dashboard/CLAUDE.md` said so in parentheses.

Two ledger rows depended on the resolution. `td-063` recorded two imprecise
assertions inside `LaneRail.test.tsx`; `td-060` recorded that the
"state is never colour-alone" accessibility invariant was pinned *only* by
that same suite — a pin over code nobody ships.

## Decision

Delete `dashboard/src/lanes/LaneRail.tsx` and `LaneRail.test.tsx`, and the
`.lane-rail-shell` rule they were the only consumer of. Keep
`laneRailState.ts` (three live consumers) and `LoopStrip.tsx`.

The shared layer is now **derivation only**: the state vocabulary is computed
once and each lane renders it against the `.lane-stage` / `aria-current="step"`
class contract by hand. That contract, not a component, is what holds the rails
consistent — which is what was actually true before this decision, now written
down.

Before deleting, the colour-alone invariant was re-pinned on a live surface:
`AnswerLane.test.tsx` asserts, over a real lane's three stage states, that each
carries `data-state`, a visible state word, and a glyph. `td-063` is closed by
the deletion of the assertions it described.

## Considered Options

### (a) Adopt the shell in at least one lane

- **Pro**: Honors `dec-093`'s intent; gives the class contract an executable home.
- **Con**: Every candidate lane rejected it for a concrete reason already in its
  docblock. Adoption means bending a lane to the shell, not the reverse.

### (b) Keep it unused as a reference implementation

- **Con**: A component only its own tests render is a component whose tests
  gate nothing. It also parked the accessibility invariant somewhere the
  shipped code could regress past.

### (c) Delete it, re-pin the invariant on a live lane (chosen)

- **Pro**: Removes a component and a suite that gated nothing; moves an
  accessibility invariant onto code that ships.
- **Con**: The class contract is now a convention plus per-lane tests rather
  than one component. A fifth railed lane starts from a sibling, not a shell.

## Consequences

**Positive.** One fewer component and one fewer test suite to keep current.
The colour-alone floor now fails the build when a *shipped* lane regresses.
`td-063` and the `td-060` mitigation both resolve.

**Negative.** Four hand-rolled rails can drift from the class contract
independently; only per-lane tests catch it. If a fifth railed lane appears and
a third of it is copied markup, the shell question is worth re-opening — with
the body-swap rule generalized this time, which is the thing that killed the
first attempt.

## Disconfirmation

- **Falsifier.** A new railed lane whose body-swap rule matches an existing
  lane's exactly, so the copied markup has no lane-specific reason to differ.
  That would show the shell was abandoned prematurely rather than correctly.
- **Steelmanned runner-up.** (a) is strongest read as "the shell was never
  adopted because nobody was made to": `dec-093` created it up front, before
  any lane existed to constrain it, so it encoded guesses rather than shared
  requirements. Adopting it *after* four lanes exist would produce a different,
  grounded shell.
- **Reversal trigger.** Two lanes' rail markup diverging from the class
  contract in a way a per-lane test does not catch — or a fifth railed lane
  landing with copied rail markup.

## Prior Decision

`dec-093` is flipped to `status: superseded` / `superseded_by: dec-114` as of
this record's finalize. `dashboard/src/lanes/LaneRail.tsx` was also removed
from the `affected_files` of `dec-093`, `dec-100` and `dec-104`: the health
check requires those paths to resolve on disk, and this decision is the record
of why the file no longer does.

`dec-093` chose one contract with two halves. The derivation half is retained
unchanged and remains the single source of the rail state vocabulary; only the
renderer half is withdrawn, for the reason its own consumers recorded — the
body-swap rule is per-lane and was never shared. What changed is evidence, not
preference: a full redesign passed with zero adoptions.
