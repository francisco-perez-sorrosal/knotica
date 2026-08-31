---
id: dec-104
title: A client-owned focus axis on lane rails, orthogonal to server-declared stage state
status: accepted
category: architectural
date: 2026-08-27
summary: Lane rails gain a second, client-owned `focus` axis so a user can open any stage, while the server keeps sole ownership of `state` (process position).
tags: [dashboard, ui, lane-rail, state-machine, interaction]
made_by: agent
agent_type: interface-designer
branch: worktree-dashboard-ui-redesign
pipeline_tier: standard
dissent: Adding a second state axis to a rail deliberately built around one monotonic watermark risks re-introducing exactly the "two stages read in play at once" ambiguity `laneRailState.ts` was written to make unrepresentable.
affected_files:
  - dashboard/src/lanes/stageFocus.ts
  - dashboard/src/lanes/improve/ImproveLane.tsx
  - dashboard/src/lanes/LoopStrip.tsx
---

## Context

`ImproveLane.tsx` mounts a stage's real, interactive body only when the server-declared
state is `active` or `blocked`; every other stage renders a one-line summary. The server
derives that state in `core/status_lanes.py::_improve_watermark`, which — by its own
docstring, deliberately — returns a position **only** when `LoopStage.evaluating`.

The consequence, visible in the user's screenshot: all six Improve stages read `Pending`,
no stage mounts an interactive body, and clicking a stage does nothing. The product's
flagship lane — the entire self-improvement loop — is non-interactive by construction in
the overwhelmingly common case. This is not a styling defect; no amount of visual redesign
reaches it.

The same shape affects `answer` (whose server adapter passes `None` unconditionally: every
stage is always pending) and any lane whose watermark is idle.

## Decision

Introduce a **second axis on lane rails — `focus` — client-owned and orthogonal to the
server-declared `state`.**

- `state` ∈ `pending`/`active`/`complete`/`blocked` = *where the process is*. Server-owned,
  rendered verbatim, unchanged. `aria-current="step"` stays bound to this axis alone.
- `focus` = one stage id or `null` = *what the user is looking at*. Client-owned, per lane,
  reset on lane/topic/vault change. Rendered as `data-focus` plus `aria-expanded` on the
  stage's disclosure control.

Render rule: a stage mounts its real body when it is `active`/`blocked` (as today) **or**
when it is focused. Unfocused `pending`/`complete` stages keep the one-line summary.
Initial focus is the `active`/`blocked` stage if one exists, else `null`. A newly-active
stage arriving from the server **never steals** focus from a stage the user is reading.

This is not a new concept in the codebase: `laneRailState.ts::deriveChecklistStages`
already documents exactly this separation ("`activeId` is UI focus, not a process
position"). The decision generalises the project's own existing idea from checklist lanes
to sequence lanes.

Precondition, gating the implementation: audit each stage component's mount-time effects.
Mounting must perform reads only. Any stage that fires a mutating or billed call on mount
is excluded from focus-mount and renders explainer-only. Billed *controls* are unaffected —
`ArmedButton` two-phase is untouched and one click still cannot bill.

## Considered Options

### A. Do nothing at the client; fix the server watermark instead

- **Pro**: addresses the root cause; the rail's "server is the one source of truth for rail
  position" property stays absolute.
- **Con**: it is a `core/status_lanes.py` change with its own test suite, landing inside a
  presentation-layer pass — mixing a behavioural derivation change into a visual diff. And
  it does not actually solve the interaction problem: even a perfectly-derived watermark
  leaves exactly one stage interactive, so a user still cannot open Gate to *read* it while
  Observe is running. Registered separately as an architecture challenge and recommended as
  the immediate follow-on; it is complementary, not substitutable.

### B. Mount every stage's body unconditionally

- **Pro**: trivially simple; no new axis.
- **Con**: discards the progressive-disclosure property the lane was designed around
  (`ImproveLane.tsx`'s own docstring, rule 4), turns a six-stage rail into six simultaneous
  live panels, and multiplies mount-time reads sixfold on a 2s poll.

### C. Make pending stages expand to an explainer only, never the real body

- **Pro**: zero behavioural risk; no mount-effect audit needed.
- **Con**: the user asked to "be a participant when required". An explainer answers *learn
  what is going on* but not *participate* — the user still cannot start a cycle from an idle
  lane. Retained as the per-stage fallback when the mount-effect audit fails.

### D. Client-owned focus axis (chosen)

- **Pro**: makes every stage reachable without claiming the process is there; keeps the
  server as sole owner of process truth; survives option A landing later (the two axes are
  orthogonal, so widening the watermark improves the rail without touching focus); reuses a
  separation the codebase already blessed for checklists.
- **Con**: two axes are harder to reason about than one, and the rendering matrix must be
  written down or it will drift.

## Consequences

**Positive.** The flagship lane becomes usable in its common state. Loop-strip nodes and
rail disclosures both become real affordances — a click produces visible response, closing
the reported defect. The design remains correct after the server-side widening lands.
Progressive disclosure is preserved: exactly one stage body is mounted at a time.

**Negative.** Two state axes on one rail. The risk that they are conflated is real, and is
mitigated structurally: `state` and `focus` are separate DOM attributes (`data-state` /
`data-focus`), `aria-current="step"` is bound to `state` only (already test-pinned), and
focus is never derived from state after initialisation. Mount-time effects of six stage
components must be audited before wiring, which is real work the other options avoid.

## Disconfirmation

**Falsifier.** If a user (or a test) can reach a screen where a focused `pending` stage
reads as though the process is at that stage — or where `aria-current="step"` follows focus
rather than the watermark — the two axes have been conflated and the separation failed.

**Steelmanned runner-up.** Option C is the conservative choice and it is genuinely close: it
delivers the entire "learn what is going on" half of the brief at zero behavioural risk,
and the "participate" half is arguably blocked on the server watermark anyway. If the
mount-effect audit turns up even one offending stage, C is what that stage gets — so the
design already concedes C is viable per-stage. The reason it is not the default is that
"open the stage to read what it does, but you may not act from here" is an interface that
teaches helplessness, which is the opposite of the brief.

**Reversal trigger.** If the server watermark is widened to name a position for every
stage from real evidence (the follow-on recommended in the architecture challenge), re-open
this: focus may then be reducible to pure scroll-and-expand with no body-mounting rule at
all, which would be strictly simpler.
