---
id: dec-draft-0d6786dd
title: A stage-body presentation grammar for the dashboard
status: proposed
category: architectural
date: 2026-08-28
summary: Every stage interior is composed from four shared primitives (SectionCard, Stat/StatGrid, TermHint, StateList) over a shared dismissal hook, rather than each stage formatting its own readouts.
tags: [dashboard, ui, components, progressive-disclosure, accessibility, stage-interiors]
made_by: agent
agent_type: interface-designer
branch: worktree-dashboard-ui-redesign
pipeline_tier: standard
dissent: Six stage components are few enough that each could keep formatting its own readouts; four more shared modules is a second primitives tier stacked on the one shipped a day earlier.
affected_files:
  - dashboard/src/SectionCard.tsx
  - dashboard/src/Stat.tsx
  - dashboard/src/TermHint.tsx
  - dashboard/src/StateList.tsx
  - dashboard/src/useOverlayDismiss.ts
  - dashboard/src/InfoPopover.tsx
  - dashboard/src/lanes/improve/ObserveStage.tsx
  - dashboard/src/lanes/improve/InstrumentStage.tsx
  - dashboard/src/lanes/improve/GateStage.tsx
  - dashboard/src/lanes/improve/HealStage.tsx
  - dashboard/src/lanes/improve/PromoteStage.tsx
  - dashboard/src/lanes/improve/ProveStage.tsx
  - dashboard/src/app.css
---

## Context

Round 1 of the dashboard redesign built a shared primitives tier (`icons`, `InfoPopover`,
`LoopStrip`, `EmptyState`, `CopyBlock`) and rebuilt the chrome, Home, the loop strip and the
rail — but deliberately excluded stage *interiors* (its `U8`) to bound the change.

Those interiors are now the whole remaining problem. Each of the six Improve stage components
formats its own readouts inline as prose with `<strong>` in it (`Latest: gen 12 · scalar 0.66`,
`Held-out: 40`, `1 recent race(s)`, `cadence: every 6h · window 7d · threads 4`), places its
buttons wherever the flex container put them rather than beside the data they act on, and carries
its explanations in `title=` attributes that are invisible on touch, keyboard-unreachable, and — on
`Freeze golden` — paired with `disabled`, the exact anti-pattern round 1 banned in the rail.

One failure is not even stylistic: `app.css` styles `.arena-lane .lane-meta` / `.lane-track` /
`.lane-fill` / `.lane-baseline`, a sub-structure `HealStage` has not rendered since the arena was
absorbed into it. Grep confirms zero TS consumers. The variant list therefore inherits nothing and
renders as `variant-1pending…`.

Rebuilding six interiors ad hoc would produce six more divergent implementations of the same four
ideas, in the same directory, three weeks after extracting the first primitives tier for that exact
reason.

## Decision

Introduce a **stage-body presentation grammar**: four shared primitives plus one extracted hook,
from which every stage interior is composed, in a fixed scan order — *status → data → configuration
→ action*.

- `SectionCard.tsx` — a titled section: uppercase micro-label header, right-aligned header actions,
  bordered body, optional footer. Carries **no** `aria-expanded` and is never a disclosure.
- `Stat.tsx` — `StatGrid` + `Stat`: label/value pairs, mono, `tabular-nums`, `—` in the neutral tone
  for absence. Replaces every `Label: <strong>value</strong>` prose fragment.
- `TermHint.tsx` — a new inline overlay affordance: a dotted-underline term or value that opens a
  small explanatory panel on click. Shares `infoPopoverState.ts`'s module signal with `InfoPopover`,
  so at most one overlay is ever open; `role="note"`, Escape/outside-click/focus-out dismissal,
  keyboard-reachable, never hover-only, never a confirmation surface.
- `StateList.tsx` — the multi-item live readout: state icon + name + state chip + right-aligned
  tabular value + optional quiet row action. Replaces the arena variant list, the gate candidate
  list, and Fill's ingest list.
- `useOverlayDismiss.ts` — the dismissal effect, extracted so `InfoPopover` and `TermHint` cannot
  diverge.

Plus one placement rule with the force of the primitives: **every button belongs to the
`SectionCard` whose data it acts on** — primary right-aligned in that card's footer, quiet actions
as ghost buttons, billed controls preceded by a sibling `billed` chip. `ArmedButton` /
`TwoPhaseAction` semantics, labels and test ids are untouched; the chip is never a child of the
button, because that would change its accessible name.

## Considered Options

### A. Rebuild each of the six interiors in place, no new primitives

- **Pro**: no new modules; each stage free to present its own domain as it sees fit; smallest
  possible diff surface per file.
- **Con**: six implementations of stat rows, six of state lists, six of the explanatory overlay.
  The `TermHint` dismissal semantics alone (Escape, outside-click, focus-out, single-open, focus
  return) would be duplicated six times or, more realistically, implemented well once and badly five
  times. It also leaves no answer for the priority-2 lanes, which need the same shapes.

### B. CSS-only: restyle the existing markup without touching the components

- **Pro**: zero component risk against four test suites that pin billed two-phase behaviour; fixes
  the dead-`.arena-lane` bug immediately.
- **Con**: cannot fix what is actually wrong. `Latest: gen 12 · scalar 0.66` is one `<span>` — no
  stylesheet can turn a sentence into a labelled grid. `title=` attributes cannot be made
  keyboard-reachable by CSS. Buttons cannot be moved next to their data without moving them in the
  DOM. It would make the current structure prettier and leave it illegible.

### C. Shared stage-body grammar (chosen)

- **Pro**: ~325 LOC across five modules, none over 100 lines, zero new dependencies; one place to
  enforce the tabular-numeral, absence-is-neutral, never-colour-alone and target-size floors; the
  same grammar serves the priority-2 lanes and round 3 without a second design pass; each stage
  component's diff becomes a `return (…)` rewrite with nothing above the return touched, which is a
  sharp and checkable scope line next to four billed-action test suites.
- **Con**: a second primitives tier landing one round after the first; the `TermHint` copy (roughly
  25 short explanations) becomes content that has to be kept true as the domain changes.

## Consequences

**Positive.** Stage interiors become scannable in a fixed order; every button acquires a visible
relationship to the data it changes; explanations move out of `title=` into a keyboard- and
touch-reachable overlay, which also retires the `disabled`+`title` pairing on `Freeze golden`; the
dead `.arena-lane` rules are deleted rather than left as a trap; three numbers that the loop turns on
(the gate baseline in Observe, the baseline in Promote, the overlap counts in Instrument) become
visible for the first time.

**Negative.** Nine shared presentation modules now exist where there were none two rounds ago, and
the boundary between "primitive" and "lane component" needs to hold. The `TermHint` copy is domain
knowledge living in the presentation layer: if the loop's semantics change, roughly 25 short strings
go stale silently — no test can catch a sentence that is merely no longer true.

**Neutral.** Two loose-regex assertions in the existing suites are sensitive to values being
rendered twice, and one (`ObserveStage`'s cadence assertion) is already vacuous — it resolves against
the scalar, not the cadence. The grammar forces both into the open, which is a cost this round pays
and a benefit the next one keeps.

## Disconfirmation

**Falsifier.** If, after the six Improve interiors are rebuilt, two or more stages need a section
shape that `SectionCard` + `StatGrid` + `StateList` cannot express — a genuinely tabular comparison,
a timeline, a nested tree — then the grammar was fitted to Improve rather than to stage interiors,
and the abstraction is wrong. Equally falsifying: if `TermHint` panels routinely need more than a
short paragraph, the affordance is doing `InfoPopover`'s job and should not have been a separate
component.

**Steelmanned runner-up.** Option A is defensible on the rule of three: `StateList` has exactly
three call sites (arena variants, gate candidates, Fill's ingest list) and `SectionCard` is thin
enough to be a CSS class rather than a component. If the priority-2 lanes had been left out of scope,
A would probably win — the primitives would be extracted after the interiors proved they recur,
rather than before. The counter is that the recurrence is already enumerated, not predicted, and that
`TermHint`'s dismissal semantics are the one part nobody should write twice.

**Reversal trigger.** If a later round finds most lanes bypassing the grammar — composing raw markup
because the primitives get in the way — dissolve them back into CSS classes and keep only
`TermHint` and `useOverlayDismiss`, which are behaviour rather than layout.
