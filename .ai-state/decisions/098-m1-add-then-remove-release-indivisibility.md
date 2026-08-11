---
id: dec-098
title: M1's indivisibility is a release property, and the rename lands add-then-remove
status: accepted
category: implementation
date: 2026-08-10
summary: "The architecture declares that M1 must land whole, which read as a commit-level constraint would make the largest milestone in the plan undecomposable; it is satisfied instead by one merge and one feat! release, with the six lane dispatchers registered additively and proven equivalent before any flat tool is removed, so no intermediate commit ever holds a half-renamed surface."
tags: [swimlanes, step-decomposition, release, rename, mcp, tool-surface, sequencing]
made_by: agent
agent_type: implementation-planner
branch: worktree-process-swimlanes
pipeline_tier: full
affected_files:
  - src/knotica/mcp_server/server.py
  - CONTRIBUTING.md
# `src/knotica/core/process_model.py` was listed here and has been removed: it is
# an M1 deliverable that does not exist yet, and this field is gated on resolution
# so a rename or deletion cannot leave a dangling claim behind. The list is for
# files on disk; a file the decision anticipates is recorded here in prose until
# it exists. Add it back when the keystone module lands.
affected_reqs: [REQ-09, REQ-09b, REQ-09c, REQ-27]
re_affirms: dec-094
dissent: "Registering six lane dispatchers alongside the twenty-one flat tools they replace puts the surface at forty-one registrations for the length of two batches — more than double the count the whole rename exists to reduce — and if the pipeline is interrupted or partially merged in that window the project ships the exact failure the consolidation was meant to prevent, with no gate that would notice."
---

## Context

`SYSTEMS_PLAN.md § Sequencing` declares that M1 "must land whole — a half-renamed surface is the
one state with no defensible story", and that it is released as a single `feat!:`. Read as a
constraint on commits, that makes M1 a single ~20-file commit spanning a new `core/` module, six
new dispatcher modules, twenty-one removals, a ~180-reference description rewrite, and the docs —
which violates the known-good-increment rule the planning discipline is built on, and would put
the project's most consequential change beyond incremental review.

The tension is real, not a misreading. A surface where some operator verbs are reachable only as
flat tools and others only as lane actions is genuinely indefensible: it is worse for a routing
model than either endpoint.

## Decision

M1's indivisibility is satisfied at the **release** boundary, not the commit boundary. Steps commit
individually on the pipeline branch, each leaving `make verify` green; the branch merges to `main`
once, as one `feat!:` commit carrying a `BREAKING CHANGE:` footer with the full old→new mapping
table.

The shape that makes this true is **add-then-remove**:

1. Register the six lane dispatchers **additively** (Step 29). Nothing is removed. The surface is
   temporarily forty-one registrations, a state that exists only on the pipeline branch.
2. Prove payload equivalence for every `LANE_MEMBERSHIP` action against the flat tool it will
   replace (Step 30), with the equivalence table derived from the declaration rather than
   hand-written.
3. Remove the twenty-one operator flat tools as a **pure deletion** (Step 31), which the
   equivalence proof has already made safe.

At no intermediate commit is the surface half-renamed. It is only ever complete-and-larger, or
complete-and-smaller.

## Considered Options

### One large M1 commit

Literal compliance with "land whole". Rejected: it is unreviewable, it cannot be bisected, and an
intra-step review of the two RISKY steps (the removal and the description corpus) becomes
impossible because they are not separable from the additions.

### Swap in place, dispatcher by dispatcher

Replace one topical dispatcher with its lane successor at a time. Rejected: this is exactly the
half-renamed surface the architecture forbids, sustained across several commits, and it is the
state a routing model handles worst.

### Add-then-remove behind a feature flag

Register lane dispatchers behind an env flag, flip at the end. Rejected: a flag on the tool
registration surface is itself a schema decision, it doubles the states the tests must cover, and
`dec-050`'s reasoning applies — the branch already provides the isolation a flag would buy.

### Add-then-remove on the branch, one release *(chosen)*

Gets the reviewability of small steps and the atomicity of one release, at the cost of a
temporarily inflated surface that is never published.

## Consequences

**Positive.** Every M1 step is a known-good increment with `make verify` green. The removal step
is a pure deletion whose safety is proven by a prior step rather than argued. The two RISKY steps
(31 and 38) become independently reviewable, which is what makes intra-step review possible at
all. Rollback is per-step on the branch and per-milestone after merge.

**Negative.** The forty-one-registration window is real, and nothing gates against merging inside
it. The mitigation is procedural — the branch merges once, at Step 42 — not mechanical. A stronger
mitigation would be a pre-merge check asserting the registration count is at or below twenty-two,
which the surface-consistency gate (Step 13) can carry once the target count is known.

**Neutral.** This decision does not touch what the rename *is*; it governs how it lands. It
re-affirms `dec-094`'s tiered shape rather than modifying it.

## Disconfirmation

**Falsifier.** If a lane dispatcher's payload cannot be made equal to its flat predecessor's for
some action — because the flat tool carried behaviour the action table cannot express — then Step
30's equivalence proof fails and the removal is not a pure deletion. The add-then-remove shape
would then be hiding a redesign inside a sequencing decision.

**Steelmanned runner-up.** One large commit is honest about what M1 is: a single atomic change to a
published contract. Splitting it creates a real window in which the repository holds a surface the
project would never ship, and the only thing preventing that window from being merged is that
nobody merges it.

**Reversal trigger.** If a future breaking surface change is small enough to review in one commit,
prefer the one commit — this decision buys reviewability, and it should not be applied where there
is none to buy.
