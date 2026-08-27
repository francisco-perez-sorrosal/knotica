---
id: dec-088
title: Lane membership is a set, not a scalar — and the conversational core stays flat and lane-less
status: accepted
category: architectural
date: 2026-08-10
summary: "Eleven code-grounded multi-lane verbs (one routing into four lanes by a runtime argument) make an owning-lane-per-verb partition unrepresentable, so lane membership is declared as a set of (lane, stage, narration) tuples; and dec-045's conversational core is held as a boundary the lane-dispatcher rename may not cross, widening the flat tier to every high-density conversational verb."
tags: [mcp, tool-surface, swimlanes, facet, published-contract, routing, conversational-core]
made_by: agent
agent_type: systems-architect
branch: worktree-process-swimlanes
pipeline_tier: full
affected_files:
  - src/knotica/core/
  - src/knotica/mcp_server/
  - src/knotica/mcp_server/tools_write.py
  - src/knotica/mcp_server/tools_gaps.py
  - src/knotica/mcp_server/tools_notes.py
  - docs/reference.md
dissent: "Widening the flat tier from nine tools to roughly fourteen spends most of the margin the lane-dispatcher consolidation was bought for — it lands the surface near the upper edge of the range where tool-selection quality is claimed to hold, on a conversational/operator distinction that is a judgement about call frequency this project has never measured, and if that judgement is wrong the surface carries five extra always-loaded schemas for nothing."
---

## Context

The swimlane redesign's locked decision 4 renames the published surface so every entry point speaks in
lanes. Parallel research then falsified the presupposition a rename rests on — that each verb has exactly
one owning lane.

There are **eleven multi-lane verbs**, each traced to a declaration site or a finalized ADR, plus **six
cross-lane primitives** belonging to no lane at all. The decisive case is `notes action=promote`
(`tools_dispatch_notes_mutations.py:17, 71-72, 562`): one action string routes into Tend, Answer, Improve
or Fill **depending on its `target` argument**. A name is fixed at registration; that verb's lane is
not. `curate_example` is simultaneously Learn's terminal state, Answer's reaction and Improve's
precondition (`tools_write.py:76`). Terminal states are shared — Learn terminates in a committed page and
so does Fill, by merge.

The redesign's own locked design rule already describes a facet: *"shared objects are projected per-lane,
never duplicated — the gate is one mechanism; Improve narrates it as 'candidate queue', Fill as 'your
approved source is being measured'."*

The sibling decision `dec-094` (interface-designer) rules on the **shape** the rename takes:
a tiered surface of flat cross-lane primitives plus six lane dispatchers, in which a multi-lane verb
becomes an action in each lane that serves it — one tuple entry per lane, zero added schema weight,
because a dispatcher action is a free-form `str` and not a schema enum. That shape makes the facet free.
It assumes the facet; it does not establish it, and it does not fix where the flat tier ends.

This record decides those two things.

## Decision

**1. Lane membership is a set, declared as data.** In `core/process_model.py` (`dec-089`),
each verb carries a set of `(lane, stage, narration)` memberships:

- Membership is keyed on `(verb, discriminator)` where a runtime argument decides the lane, so
  `notes action=promote target=…` is expressible rather than approximated.
- A verb with several memberships appears as an action in **each** lane dispatcher that serves it, with
  that lane's own narration — one implementation, N faces. This is the mechanical form of the locked
  design rule, and the reason no verb is ever assigned an arbitrary owning lane.
- A verb with no membership is explicitly classified `primitive` (cross-lane read) or `infrastructure`
  (unlaned). "No lane" is a declared state, never an omission.

`LANE_MEMBERSHIP` is therefore not merely UI metadata: it is what generates each lane dispatcher's action
table, so the tool surface and the rails are projections of the same declaration.

**2. The conversational core is a boundary the rename may not cross.** `dec-041`/`dec-045` established a
two-tier surface in which *"thin conversational tools carry the high-density verbs; dispatchers route the
operator long tail."* That split is by **caller and call frequency**, not by topic, and a lane re-cut
must preserve it. `dec-094`'s Tier 1 (`search`, `read_page`, `list_topics`, `list_links`,
`read_protocol`, `write_page`, `store_source`, `query`, `wiki_status`) is drawn on lane-lessness alone
and would move genuinely conversational verbs into dispatchers.

**Tier 1 is widened to include every verb the client-as-brain calls mid-conversation**, whether or not it
is lane-less: `curate_example`, `gap_report`, `note_capture`, `ingest_progress`. Each is called by the
model in the middle of an ingest, an answer or a curation turn — the exact traffic `dec-045` kept flat —
and each is multi-lane, so a lane prefix would state something false. `create_topic` is a judgement call
left to the interface layer.

Everything else — the operator long tail — moves into the six lane dispatchers as `dec-094`
specifies. Its rulings on deprecation (tool names removed without alias; actions aliased in `_ACTIONS`;
`argparse` aliases; slash-command tombstones; a `?pane=` alias map) and on instrumenting before renaming
are adopted unchanged.

## Considered Options

### Option 1 — Facet as a declared set, with the flat tier widened to the conversational core (chosen)

Expresses every multi-lane verb, including the four-lane one, with no duplication and no arbitrary
assignment. Preserves the one property `dec-045`'s tiering was designed for. Cost: roughly fourteen flat
tools instead of nine, which spends part of the consolidation's margin.

### Option 2 — Owning lane per verb (the literal partition)

Rejected: arbitrary in eleven of thirty-five cases and unrepresentable in one. Recorded here because it
is the reading locked decision 4 invites, and because its falsification is the reason this ADR exists.

### Option 3 — Facet as a set, Tier 1 exactly as `dec-094` draws it (nine tools)

The smallest surface (~17 tools) and the strongest position against tool-count degradation. Rejected:
it turns `curate_example`, `gap_report`, `note_capture` and `ingest_progress` — verbs the model calls
mid-turn, guided by a protocol prompt that names them — into `learn(action=…)` / `answer(action=…)` /
`fill(action=…)`. That inverts `dec-045`'s own split, adds an argument-validation failure mode to the
highest-frequency conversational path, and puts a lane label on four verbs that serve several.

### Option 4 — Register each multi-lane verb under several lane-prefixed flat names

Rejected: `dec-050`'s deleted alias layer in a new costume, at a worse ratio — a three-lane verb costs
three always-loaded schemas.

## Consequences

**Positive.** Every multi-lane verb keeps one implementation and gains N narrations. The four-lane verb
is representable. The conversational path the client-as-brain uses most keeps its names, its flat shape
and its learned routing, so the rename's risk is concentrated on the operator surface where a wrong
action is loud (`INVALID_ARGUMENT` with a `fix=` hint, plus `record_rejected_action`) rather than on the
conversational surface where a wrong call is quiet. `LANE_MEMBERSHIP` becomes a generator rather than
documentation, so a lane's action table cannot drift from its rail.

**Negative.** The flat tier grows from nine to roughly fourteen, which is most of the margin the
consolidation was bought for; the surface lands near the upper edge of the count where selection quality
is claimed to hold rather than comfortably below it. The conversational/operator line is a judgement
about call frequency that this project has never measured — `dispatch_telemetry` covers the dispatchers
only, so the very evidence that would place the line correctly is the evidence the prerequisite fix is
about to start collecting, and not before this boundary must be drawn.

## Disconfirmation

- **Falsifier:** telemetry, once it covers all tools, showing that the four verbs added to Tier 1 are in
  fact low-frequency operator traffic rather than mid-turn conversational traffic. That would make the
  widening five wasted schemas and Option 3 correct. The measurement is cheap and arrives with the
  prerequisite instrumentation — this boundary should be re-drawn on it rather than defended.
- **Steelmanned runner-up:** Option 3. Surface size is the one property with independent evidence behind
  it, and every other argument here is an appeal to a distinction this project has never quantified.
  `dec-045` drew the conversational/operator line by judgement too, and a line drawn twice by judgement
  is not more reliable for having been drawn twice. Nine tools is a defensible schelling point; fourteen
  is a negotiation.
- **Reversal trigger:** a twelfth multi-lane verb turning out to be a modelling error rather than a fact
  (which would weaken the facet ruling), **or** the first telemetry window showing the Tier-1 widening
  unjustified by call frequency (which would restore Option 3).

## Relationship to sibling decisions

- `dec-094` (interface-designer) owns the **shape** of the rename — tiered surface, six lane
  dispatchers, deprecation per surface, description standard. Adopted, with the Tier-1 widening above as
  the one amendment. Its CH-01 challenge in `INTERFACE_DESIGN.md` is accepted `adopt-with-modification`.
- `dec-089` owns **where the declaration lives** and how each surface reads it.
- `dec-090` owns whether an **alias layer** is restored for the removed tool names.
