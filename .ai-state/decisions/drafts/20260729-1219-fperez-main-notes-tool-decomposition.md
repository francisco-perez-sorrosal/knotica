---
id: dec-draft-81415016
title: Notes overlay tool decomposition -- one flat capture tool plus one notes dispatcher
status: proposed
category: architectural
date: 2026-07-29
summary: Note capture gets a flat conversational tool (note_capture); all note management collapses into a new notes action-dispatcher.
tags: [mcp, tool-surface, notes, interface-design, dec-045]
made_by: agent
agent_type: interface-designer
branch: main
pipeline_tier: standard
dissent: The flat core is already at 31 tools, well past dec-045's ~20-25 selection-quality ceiling; adding a 24th flat tool degrades selection for every tool, not just the new one.
affected_files:
  - src/knotica/mcp_server/server.py
  - src/knotica/mcp_server/tools_notes.py
  - src/knotica/mcp_server/tools_dispatch_notes.py
  - dashboard/src/toolClient.ts
---

## Context

The notes overlay adds a personal marginalia layer over KB topics. It has two distinct
interaction shapes:

- **Capture** happens mid-answer, unprompted, selected by the client model from ordinary
  conversation. It is the most conversational act in the product.
- **Management** (list, read, drift review, re-anchor, detach, promote, archive) is operator
  work, invoked from the dashboard or an explicit ask.

`dec-045` established the surface topology: the conversational core stays flat/thin because the
client model must select among the full tool set on every turn and selection quality degrades
past roughly 20-25 tools; operator/long-tail capabilities collapse into `action=` dispatchers,
where the enum cost is paid at low frequency. `dec-050` removed the deprecated flat aliases, and
the live surface is now 31 tools (23 flat + 8 dispatchers) -- already above the ceiling
`dec-045` cited.

The research pass on capture-friction prior art is unambiguous that annotation systems die when
capture costs more than the thought itself. That makes capture latency the feature's survival
condition, not a preference.

## Decision

Add exactly two tools:

1. **`note_capture`** -- a flat, conversational tool. One shot: topic, the user's verbatim note,
   the verbatim passage the agent displayed, the agent's claimed page provenance, and an intent.
   Returns the resolved anchor plus a pre-composed one-line `placement` sentence.
2. **`notes`** -- a new action-dispatcher with seven actions: `list`, `read`, `drift`,
   `reanchor`, `detach`, `promote`, `archive`. Mutating actions carry the standard verbatim
   read/offer guard clause and the `dry-run`/`apply` mode pair.

Net surface: 31 -> 33 tools (24 flat + 9 dispatchers).

Recall ("what did I note about X?") deliberately does **not** get a second flat tool; it routes
to `notes action=list`.

## Considered Options

### A. One flat `note_capture` + one `notes` dispatcher (chosen)

- **Pro:** capture -- the only latency-critical act -- is reachable in one selection hop with no
  action enum to reason over. Management pays the dispatcher hop, which it can afford.
- **Pro:** matches `dec-045` on both sides of the split rather than picking one side for the
  whole feature.
- **Pro:** the `vault` dispatcher is precedent for a new dispatcher appearing after the
  consolidation, so this is a followed pattern, not a new one.
- **Con:** spends one more slot in an already-over-ceiling flat core.

### B. Everything on a `notes` dispatcher (`action=capture`, `action=list`, ...)

- **Pro:** zero growth in the flat core; the cleanest reading of `dec-045`'s consolidation
  direction.
- **Con:** capture pays an extra reasoning hop (select dispatcher, pick from a 7-action enum,
  fill a union-of-all-actions argument set) at the exact moment friction is fatal.
- **Con:** dispatchers are documented as "rarely conversational, invoked by dashboard/CLI or an
  explicit operator ask" -- capture is the opposite of that, so the placement would contradict
  the dispatcher's stated purpose even while satisfying its letter.

### C. Two flat tools (`note_capture` + `note_list`) plus a dispatcher for the rest

- **Pro:** recall is genuinely conversational too, and notes are invisible to `search` (they live
  outside the retrieval corpus and topic scan dirs), so `notes action=list` is the *only* recall
  path.
- **Con:** spends two slots instead of one for a deliberate, user-initiated act that can afford
  the hop. Once two flat tools are justified by "it is somewhat conversational," the ceiling has
  no defender.

### D. Fold note management onto an existing dispatcher

- **Pro:** zero new dispatchers.
- **Con:** no honest home. `vault` is config-level and never touches vault contents; `datasets`
  and `golden` are eval-corpus surfaces and notes are never scored; `vault_health` is mechanical
  page conformance and notes are exempt from the page contract. Each would import the notes
  lifecycle into a domain whose invariants exclude it.

## Consequences

**Positive**

- Capture is one tool call from the user's sentence to a durable note -- no protocol load, no
  confirmation turn, no dispatcher hop.
- Management growth is absorbed by action-enum growth on one dispatcher, not by further flat-core
  growth.
- The dashboard `toolClient` maps 1:1 onto dispatcher actions, matching every existing pane.

**Negative**

- The flat core grows to 24, worsening an already-exceeded selection-quality ceiling for every
  tool on the surface.
- The precedent invites "my capability is conversational too" arguments. Mitigated by making the
  recall carve-out explicit: notes get exactly one flat tool, and recall was the strongest
  candidate for a second and still did not get one.
- `dispatch_telemetry`'s rejection counts become the watch instrument for whether the split was
  right (per `dec-045`'s own falsifier).

**Adjudicated refinements (2026-07-29, orchestrator-mediated loop-back with systems-architect).**
The seven-action set is unchanged, but two action semantics were fixed outside this ADR and are
recorded here so the decomposition record is not read as owning them: (a) `promote` targets the
**trainset** via `curate_example` -- grounded in the note's anchored wiki pages, never the note
path -- with golden promotion deferred behind `golden_review`; (b) `reanchor` and `detach` are
**append-only** (`detach` appends a terminal `detached` record), per the architect's anchor
invariant. Neither changes the flat-vs-dispatcher split this ADR decides.

## Disconfirmation

**Falsifier.** `dispatch_telemetry` shows the model mis-selecting or mis-arg-ing tools measurably
more often after `note_capture` and `notes` land than before -- or, more specifically, shows
`note_capture` being called for management intents (or `notes action=capture`-shaped attempts on
a dispatcher that has no such action). Either pattern says the split does not match how the model
actually reasons about the domain.

**Steelmanned runner-up (option B, everything on the dispatcher).** The ~20-25 ceiling is not a
soft guideline -- it is a measured degradation, and the surface is already at 31. Every flat tool
added taxes selection accuracy for all 33, including the high-stakes mutating ones. Against that
system-wide cost, the benefit claimed here is one reasoning hop on one action. Models are also
demonstrably competent at action-enum dispatch -- the eight existing dispatchers are evidence --
and "capture must be one hop" is an assertion about latency that has not been measured in this
system. A disciplined reading says: hold the line, put capture on the dispatcher, and let
telemetry prove the hop actually hurts before spending a slot.

**Reversal trigger.** Revisit if (a) the flat core is ever consolidated again for
selection-quality reasons -- `note_capture` should be evaluated with everything else and given no
special standing; (b) telemetry shows capture mis-selection at or above the dispatcher baseline,
meaning the hop was not the bottleneck; or (c) a second latency-critical conversational
capability appears, at which point the right move is a "conversational core" grouping decision,
not another one-off slot.
