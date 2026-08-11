---
id: dec-draft-58b8a899
title: One lane-rail contract with two derivations and two rail kinds
status: proposed
category: architectural
date: 2026-08-10
summary: The six process swimlanes share a single LaneRail contract whose state is derived from one monotonic watermark; the existing server-side ingest stage rail is generalized into it, the LoopPane inline stepper is replaced, and a second rail kind (checklist) exists for Tend because forcing it into a sequence would assert something false.
tags:
  - dashboard
  - swimlanes
  - interface-design
  - stage-rail
  - information-architecture
made_by: agent
agent_type: interface-designer
branch: worktree-process-swimlanes
pipeline_tier: full
dissent: A single rail kind would be simpler; adding `checklist` for one lane (Tend) risks a per-lane-kind proliferation that dissolves the abstraction the decision exists to create.
affected_files:
  - dashboard/src/LoopPane.tsx
  - dashboard/src/IngestPane.tsx
  - dashboard/src/VaultPane.tsx
  - src/knotica/core/ingest_activity.py
---

## Context

Knotica is replacing eight tool-shaped dashboard panes with six process-shaped lanes. A lane needs a
stage rail, and **two incompatible stage-rail implementations already exist**:

- `dashboard/src/LoopPane.tsx:842-960` builds an inline array literal every render — `{id, title, ready,
  current, body}`. `ready` and `current` are independent booleans with **no ordering invariant** (Observe
  can be `current` while Heal is `ready`), there is **no `blocked` state** (a stuck step's reason lives
  only in a `title` tooltip at `:879-880`), and the derivation is hardcoded to `status.loop.*`.
- `src/knotica/core/ingest_activity.py::_run_summary` (`:254-314`) computes `{current_stage, stage_index,
  terminal, stages_seen}` from an append-only event journal with a **monotonic watermark**, so a late
  cognitive stage never rewinds the rail. It is correct, subtle, and server-declared —
  `IngestPane` renders it with a bundled client fallback.

Shipping a third rail, or picking one of the two to carry all six lanes, both produce two competing
stage-rail models in one codebase. The lanes also differ structurally: Improve has no run and no events
(its stages are simultaneous conditions of one topic), Learn's `fetch`/`parse` exist **only** as
client-reported journal events, and Tend's five checks are unordered peers.

## Decision

**One `LaneRail` contract; two derivations; two rail kinds.**

1. **Contract** — `{lane, kind, cardinality, scope, watermark, outcome, stages[]}` where each stage is
   `{id, title, state, fact, count, blocked{what,why,fix}, handoff, actor}`. `state` ∈
   `pending | active | complete | blocked`. Lane-level `outcome` carries the terminal state, which is why
   four stage states suffice (Fill's `quarantined` and Improve's `merged` are lane outcomes, not stage
   states).
2. **Derivations** — the server-side journal rail is **generalized, not replaced**: `_run_summary`'s
   output gains a thin projection into the contract and its watermark logic is untouched. The client-side
   inline literal is **replaced** by a declarative stage table derived by the same rules.
3. **Ordering invariant** — the sequence rail is derived from a single integer `watermark`, never
   per-stage: `index < watermark ⇒ complete`; `index > watermark ⇒ pending`; `index == watermark ⇒ active`,
   or `blocked` if a precondition is unmet. `blocked` is a modifier on the active position, never a
   separate position. The watermark is monotonic within a run and returns to 0 only on a lane reset.
4. **Two kinds** — `sequence` (Learn, Answer, Improve, Fill) and `checklist` (Tend: unordered peers,
   no watermark, per-stage health chip reusing `VaultPane`'s existing `ok`/`warn`/`bad` vocabulary).
5. **Single declaration** — the six lanes and their stage vocabularies are declared **once,
   server-side**, and the dashboard treats the declaration as data with a bundled fallback (extending
   `IngestPane`'s existing precedent). This is the structural expression of the task's "one process model
   declared once, every entry point a projection of it", and avoids paying a committed-artifact rebuild
   for a stage-label change.
6. **Interactivity follows the watermark** — a stage is interactive iff `index <= watermark`;
   ahead-of-watermark stages render their **precondition as content** rather than a disabled control with
   the reason hidden in a `title` attribute.

## Considered Options

### A. Generalize the server-side journal rail to all six lanes

Pros: one model, already correct, already server-declared, already polled by a pane.
Cons: Improve has no run id and no events — a journal would have to be manufactured for stages that are
simultaneous conditions; Answer's `ask`/`cite` would force `query` (the highest-frequency read in the
system) to become a journal writer. Rejected: it makes read paths writers to serve a rendering concern.

### B. Generalize the client-side `LoopPane` stepper to all six lanes

Pros: it already hosts interactive bodies and a terminal state, which the journal rail cannot.
Cons: it discards the monotonic watermark, has no ordering invariant and no `blocked` state, and cannot
express Learn's `fetch`/`parse`, which exist only as journal events. Rejected: its looseness is a defect,
and ~450 of its 1480 lines are the only reusable part.

### C. One contract, two derivations, two kinds (**chosen**)

Pros: covers all six lanes; keeps the correct watermark logic; makes the ordering invariant
unrepresentable-if-violated; the four-state machine renders from one integer plus one optional reason;
"why is this stuck" is answerable in exactly one place.
Cons: one indirection, and the discipline of keeping two producers honest against one contract.

### D. Keep Tend as the nested tab widget it is today

Pros: zero change; tabs are the correct affordance for peers.
Cons: a tab inside a lane is a fourth navigation level (lane → stage → tab → content) — precisely the
pattern that made `VaultPane` a catch-all hosting KB creation, vault stats, the metadata tree, a compile
panel, a scoreboard **and** a 4-tab Checks widget. Rejected.

## Consequences

**Positive**

- The illegal `LoopPane` state combination (Observe active while Heal ready) becomes unrepresentable —
  the rail renders from one integer.
- `blocked.{what,why,fix}` is the same three-part grammar as the CLI error contract and the MCP
  `{message, fix}` envelope: one grammar, three consumers.
- Every non-complete stage names an `actor`, so a stage with no next actor is caught at the contract
  level rather than in review.
- Disabled-with-tooltip is replaced by precondition-as-content, which is reachable by touch and by
  screen readers.
- A stage-vocabulary change no longer requires a dashboard rebuild (the built artifact is committed and
  CI-gated).

**Negative**

- Two producers must be kept honest against one contract; drift between them is a new failure mode with
  no existing gate.
- `_run_summary` gains a consumer it was not written for; its `stage_index`/`stages_seen` semantics
  become a published shape rather than an internal detail.
- The Improve rail's derivation is still snapshot-based and therefore still coupled to `wiki_status`'s
  single-topic gate — the contract does not fix that, it only isolates it.

## Disconfirmation

**Falsifier.** A seventh lane, or a change to any existing lane, that fits neither `sequence` nor
`checklist` — for example a rail whose stages can be *in progress simultaneously and ordered*. One such
case makes `kind` an open enum rather than a closed two-value discriminator, and the "one contract"
claim collapses into "a union type per lane".

**Steelmanned runner-up.** Option A is genuinely stronger than it looks: a journal is the only
representation that survives a page reload, gives every lane a free audit trail, and already has
out-of-order handling, terminal detection and a read tool. If the project later wants per-lane history
("show me last week's Improve cycles"), A is the shape that provides it and C does not — C's snapshot
lanes can only ever render *now*. The cost A was rejected for (making `query` a writer) applies to
exactly one stage of one lane.

**Reversal trigger.** Revisit if (a) any lane needs durable per-run history that a snapshot cannot
provide, (b) a third rail kind is proposed, or (c) the two derivations drift in observable ways — a rail
that reports different stages for the same underlying state depending on which producer rendered it.
