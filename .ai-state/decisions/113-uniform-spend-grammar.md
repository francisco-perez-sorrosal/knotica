---
id: dec-113
title: Every billed click arms first — the dashboard's spend grammar has no exception
status: accepted
category: behavioral
date: 2026-08-31
summary: The two query-class one-click spends (Answer's Ask, Improve's Probe it) are given the client-side ArmedButton arm→confirm the rest of the billed surface already uses, and the `acknowledged` preview mode is removed so the billed⇒two-phase census covers the whole registry
tags: [dashboard, spend, confirm, query, accessibility, process-lifecycle]
made_by: user
branch: main
pipeline_tier: standard
dissent: A `query` is a read that stores nothing and returns in seconds; making it two clicks taxes the single most-used control on the surface to prevent a spend the user deliberately initiated by typing a question and pressing the only button in the panel.
affected_files:
  - dashboard/src/lanes/answer/AnswerLane.tsx
  - dashboard/src/lanes/improve/ProveStage.tsx
  - dashboard/src/lanes/processContract.ts
  - dashboard/src/lanes/processRows/answer.ts
  - dashboard/src/lanes/processRows/improve.ts
  - dashboard/src/lanes/ProcessBrief.tsx
---

# Every billed click arms first — the dashboard's spend grammar has no exception

## Context

`dashboard/CLAUDE.md` states the house rule plainly: *"Billed actions are
two-phase. A single click must never bill."* After round 2 of the pre-release
review every billed control on the surface honored it **except two**:
`Ask` (`answer.ask`) and `Probe it` (`improve.probe`), both of which call the
billed `query` tool immediately on one click.

The exemption was legible rather than hidden — the process registry named it
`previewMode: "acknowledged"`, the census machine-required each exempt row to
state its cost in `willDo`, and both wore a visible `costs tokens` chip. But
it was still an exemption, and `INTERFACE_DESIGN_2 §9 CH-3` escalated the
question to the user rather than settling it: the `query` tool mints no
`confirm_nonce`, so the server cannot gate these the way `run_eval` and
`compile run` are gated. Recorded as `td-064`.

The user decided: arm them.

## Decision

Both `query`-class spends take the client-side `ArmedButton` arm→confirm
treatment. A first click arms the control and relabels it (`Confirm ask —
costs tokens`, `Confirm probe — costs tokens`); a second, explicit click
spends. A sibling `Cancel` un-arms. Editing the question un-arms, because the
armed preview described a question that no longer exists.

Because no billed row is exempt any more, the `acknowledged` preview mode is
**removed from the grammar** rather than left unused: `PreviewMode` is now
`nonce | armed | dry-run | none`, and the census assertion drops its
allow-list entry so *any* billed or arms-billing row without a two-phase
preview fails the build. The exempt-row chip copy goes with it — both rows
now wear the ordinary `billed` chip and carry the cost in the armed label,
where it is read at the moment of the decision rather than beside it.

## Considered Options

### (a) Accept and document the exemption (the round-2 status quo)

- **Pro**: `Ask` is the most-used control on the surface; two clicks per
  question is real friction, and `query` writes nothing, so a mis-click costs
  tokens and nothing else.
- **Pro**: The exemption was already honest — named in the registry, chipped
  in the UI, machine-required to state its price.
- **Con**: The house rule then reads "billed actions are two-phase, except
  where they are not", and an invariant with a standing exception is one a
  future control can quietly join.
- **Con**: The census has to carry the exception in its allow-list, which is
  where enforcement mechanisms rot.

### (b) Arm both, client-side (chosen)

- **Pro**: One grammar. Every billed click on the surface behaves the same
  way, which is what makes the behavior learnable rather than per-control.
- **Pro**: The census becomes exception-free — `nonce | armed` are the only
  previews a billed row may carry, so a new billed control cannot ship
  single-click even by omission.
- **Pro**: No server change; `query` keeps its nonce-less contract.
- **Con**: Two clicks on the surface's highest-frequency action.

### (c) Mint a `confirm_nonce` for `query` server-side

- **Pro**: Would make the preview a server-quoted number rather than a
  client-side assertion, matching `run_eval`.
- **Con**: A round-trip and a nonce lifecycle for a read whose cost is not
  meaningfully estimable in advance — the quote would carry no information
  the chip does not already carry.
- **Con**: Changes a public tool contract to serve a client-side UX rule.

## Consequences

**Positive.** The billed⇒two-phase invariant is now total and
machine-checked with no allow-list. `PreviewMode` loses a member, so the
type system carries one fewer state to reason about. The `acknowledged`
chip vocabulary disappears — one chip word (`billed`) for one meaning.

**Negative.** `Ask` costs two clicks. Every test that drove `Ask` or
`Probe it` had to learn the second click, which is a small ongoing tax on
Answer-lane and Prove-stage test authoring.

**Neutral.** The arming is client-side only and therefore defeatable by a
caller that bypasses the UI. That is true of every `armed` control on the
surface and is not a new property.

## Relationship to dec-111

Sibling, not supersession — nothing in `dec-111` changes. That decision
gated the arena scorer switch behind the *server's* confirm-nonce envelope,
settling the question for a write that enables future autonomous spend; this
one settles the *client* side for the last two spends the server cannot gate,
because `query` mints no nonce. Both answer the same question — "what may
bill on one click?" — with the same answer: nothing. Read together they are
the complete spend-gating policy, one half per layer.
