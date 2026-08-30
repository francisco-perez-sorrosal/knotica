---
id: dec-draft-30a462fd
title: A shared presentation-primitives tier for the dashboard
status: proposed
category: architectural
date: 2026-08-27
summary: Add icons/InfoPopover/LoopStrip/EmptyState/CopyBlock as a dashboard-wide primitives tier every lane inherits, rather than per-lane ad-hoc markup.
tags: [dashboard, ui, components, progressive-disclosure, accessibility]
made_by: agent
agent_type: interface-designer
branch: worktree-dashboard-ui-redesign
pipeline_tier: standard
dissent: A six-lane app can carry its explanatory copy in plain inline markup; five new shared modules is machinery a 3100-line stylesheet did not previously need.
affected_files:
  - dashboard/src/icons.tsx
  - dashboard/src/InfoPopover.tsx
  - dashboard/src/infoPopoverState.ts
  - dashboard/src/EmptyState.tsx
  - dashboard/src/CopyBlock.tsx
  - dashboard/src/lanes/LoopStrip.tsx
---

## Context

The dashboard redesign must make the self-improvement loop legible and let the user learn
what each lane and stage does without leaving the surface. Today there is no shared
vocabulary for any of that: contextual help is carried by native `title=` attributes
(invisible on touch, delayed on hover, unstyleable, keyboard-unreachable), empty states are
bare `<p>` elements, there are no icons at all, and remediation commands are rendered as
muted prose rather than copyable code.

Adding these per-lane would produce six divergent implementations of the same four ideas
across six lane directories — the exact low-cohesion pattern `laneRailState.ts`,
`ArmedButton.tsx` and `HandoffStage.tsx` were each extracted to prevent.

Two hard constraints shape the options: the artifact is single-file and CSP-safe (no
external font or asset fetch, so no icon font), and the project forbids native dialogs
(a sandboxed MCP-App iframe has no `allow-modals`).

## Decision

Introduce a **shared presentation-primitives tier** at the `dashboard/src/` root and
`dashboard/src/lanes/` root, which every lane consumes and no lane re-implements:

- `icons.tsx` — 26 inline stroke SVG glyphs behind one `<Icon name size>` component
  (`viewBox 0 0 24 24`, `stroke-width 1.5`, `currentColor`, `aria-hidden`). No icon font.
- `InfoPopover.tsx` + `infoPopoverState.ts` — a click-toggled, **non-modal** popover
  (`role="note"`, never `role="dialog"`) with a fixed three-slot body: *What this is* /
  *What the states mean* / *What to do next*. A module-level signal enforces at most one
  open. Explicitly **never** a confirmation surface.
- `LoopStrip.tsx` — the cycle visualization shared by all five railed lanes.
- `EmptyState.tsx` — the centred icon / title / one-sentence / one-action template.
- `CopyBlock.tsx` — a mono code block with a copy affordance.

`ArmedButton` remains the sole confirmation grammar; `InfoPopover` carries no destructive
action, no focus trap, and no backdrop, so the two cannot be confused.

## Considered Options

### A. Per-lane ad-hoc markup (status quo, extended)

- **Pro**: no new modules; every lane free to diverge as its content demands.
- **Con**: six implementations of the same four ideas; the reference design language cannot
  be applied consistently; help copy stays trapped in `title=` attributes the existing
  `LaneRail.test.tsx` already forbids pairing with disabled buttons.

### B. Adopt a component library (Radix, shadcn, Headless UI)

- **Pro**: accessible primitives for free; popover positioning solved.
- **Con**: fatal against the constraints. The artifact is a single self-contained file
  mounted in a sandboxed iframe; adding a library inflates the committed `app.html` that CI
  diff-gates, and most popover libraries reach for portals, floating-ui measurement, or the
  native `popover`/`<dialog>` primitives the project's no-native-dialogs invariant rules
  out. Preact + signals is the whole current dependency surface; keeping it that way is a
  standing property of this codebase.

### C. Shared primitives tier, hand-built (chosen)

- **Pro**: ~600 LOC total across five small modules, each under the 220-line mark; zero new
  dependencies; positioning is three static CSS variants with no measurement, which is what
  keeps it iframe-safe and unit-testable; one place to enforce the accessibility floor.
- **Con**: we own the accessibility semantics ourselves (focus return, Escape, outside
  click, single-open). Mitigated by keeping the primitive deliberately non-modal — the hard
  parts of an accessible dialog (focus trap, `inert`, restoration ordering) are the parts we
  are declining to build.

## Consequences

**Positive.** Every lane inherits the design language for free; a copy fix lands once;
the accessibility floor (24px targets, `:focus-visible`, never-icon-alone, reduced-motion)
is enforced in one place rather than audited six times; help moves out of `title=` and
becomes reachable by keyboard and touch.

**Negative.** Five new modules to maintain, and the popover's dismissal semantics are ours
to keep correct. The `role="note"` choice means assistive tech announces it as a note rather
than a dialog — intentional, but it does mean the panel is not automatically focused when
opened, so a screen-reader user must navigate into it.

## Disconfirmation

**Falsifier.** If, after the redesign, three or more lanes need popover content that does
not fit the three fixed slots, or need positioning the three static variants cannot express,
the primitive is under-powered and the fixed-slot contract was the wrong abstraction.

**Steelmanned runner-up.** Option A is genuinely defensible at this size: six lanes is not
many, the app has exactly one developer, and shared primitives extracted before the third
real call site violate the rule of three. The counter is that the third call site is already
visible in the design — six lane cards, six-plus rail stages, and every chrome chip all need
the same popover, and the empty-state template already recurs across four surfaces.

**Reversal trigger.** If the dashboard ever gains a genuine modal need (a full
confirmation flow that cannot be expressed as two-phase arming), revisit — at that point a
real accessible-dialog primitive is required and the case for a library reopens.
