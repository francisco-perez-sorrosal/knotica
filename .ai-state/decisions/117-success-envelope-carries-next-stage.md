---
id: dec-117
title: A lane success envelope carries next_stage, projected from the process model at one routing seam
status: accepted
category: architectural
date: 2026-08-31
summary: mcp_server/lane_next.py projects the rail position that follows an advancing verb from LANE_MEMBERSHIP/LANE_STAGES; envelope.with_next_stage attaches it at the single lane-routing seam, on success only, under the key next_stage because session_status already publishes a next
tags: [mcp, envelope, process-model, lanes, published-contract]
made_by: agent
agent_type: implementer
branch: main
pipeline_tier: standard
dissent: "The block is derived from a rail, not from the record the call just wrote, so it can name a following stage that this particular item does not actually owe — an ingest_progress event mid-run gets pointed at the gate."
affected_files:
  - src/knotica/mcp_server/lane_next.py
  - src/knotica/mcp_server/envelope.py
  - src/knotica/mcp_server/tools_dispatch_lane_common.py
  - tests/test_lane_next.py
  - docs/reference.md
---

## Context

The pre-release review's F-MS-08 named a design asymmetry: the surface answers "what next?" in two
of three places. An **error** envelope carries `fix`; the **dashboard** has carried a machine-gated
Phase 6 (`next`) for every process since `dec-106`, census-checked against `process_model.py`. A
**successful MCP call** carried neither. `fill action=suggestions_review decision=approve` returns
the record and stops; nothing in the payload says the approved source now owes
`fill action=source_ingest_open`. That is where a lane stalls one step short — the backlog the
`approved_awaiting_ingest` counter exists to measure.

## Decision

Add an optional `next_stage` block to the **success** envelope of lane calls, projected from the
declaration everything else on that surface is projected from.

- **`lane_next.py`** computes it. `LANE_MEMBERSHIP` gives the furthest rail index the verb acts on
  in that lane; `LANE_STAGES` gives what follows; the `why` is the narration already rendered in the
  lane description and `home`'s routing table. The shape mirrors the dashboard's `ProcessNext`
  union — `{"kind": "always", lane, stage, action, handoff, why}` or `{"kind": "terminal", lane,
  why}` — with **no null member**: `terminal` is an answer, absence is not.
- **One seam.** `_dispatch` in `tools_dispatch_lane_common.py` already knows the lane and the verb,
  which is exactly what the projection needs, so all six lanes inherit the block from one wiring.
- **Reads get none**, and that is the one thing declared rather than derived: `LANE_ADVANCING_VERBS`
  / `LANE_READ_VERBS` partition every lane action, and a census test fails on an unclassified verb.
  A read advanced nothing, so naming a successor would assert a transition that never happened.
- **A failure is untouched**, so its `fix` stays the one thing a caller acts on — the same rule
  `with_deprecation_note` follows.
- **`tend` never claims a successor.** `LANE_KIND["tend"] == "checklist"` — independent peers with
  no watermark — so it answers `terminal` with that reason rather than inventing an order the
  declaration explicitly denies.

**The key is `next_stage`, not `next`.** `fill action=session_status` already publishes a `next` of
its own (`{actor, do}` — per-session human guidance, a different shape answering a different
question). Overloading the key would put two incompatible shapes under one name on one surface,
forcing a model to discriminate by shape. This deviates from the review's suggested field name, and
that deviation is the decision.

## Considered Options

### Overload `next` and let `session_status` keep its own

Rejected. `session_status` is a read and would never receive the lane block, so nothing would clobber
*today* — but the surface would still publish one key with two shapes, which is the inconsistency
`agentic-interface-design` names as a tool-selection failure mode.

### Rename `session_status`'s `next` and take the good name

Rejected as out of scope and gratuitously breaking: it is a published field of a shipped payload,
and this change is additive by design.

### Emit the block from `envelope.success_result` for every verb, flat tools included

Rejected. A flat verb has no lane, so there is nothing to project from; a flat `write_page` belongs
to both `learn` and `fill` and cannot pick. The lane seam is the only place the input exists.

## Consequences

**Positive.** The dashboard's Phase 6 contract now has a server-side twin, so a Claude Desktop model
working from tool results alone no longer re-derives the next step from prose it read N turns ago.
The block is a projection: a rail edited in `process_model.py` moves it with no second edit.

**Negative.** Lane and flat payloads are no longer byte-identical — the equivalence suites now strip
`next_stage` before comparing, with the reason recorded at `_LANE_ONLY_KEYS`. The rail-derived `why`
is generic where a stage has no advancing verb to read prose off.

## Disconfirmation

**Falsifier.** Evidence that `next_stage` *misleads*: a session in which a model, having called an
advancing verb, follows the block to a stage the item does not actually owe — most plausibly the
sub-dispatchers, where the block is computed from the lane verb (`loop`, `notes`, `datasets`,
`vault_health`) and not from the inner action, so a *read* inner action (`loop_action=cadence`,
`notes_action=list`) still returns a "you advanced, go here" block. Dispatch telemetry showing a
rise in calls to `next_stage.action` immediately after such a read would falsify the classification
granularity; per-inner-action classification is the fix, and it is deliberately not paid for yet.

**Steelmanned runner-up.** Do nothing on the MCP side and let the tool *description* carry the
sequencing, as it does today. The description is already re-read on every call, costs no payload
bytes, and cannot go stale against a per-item state it never claims to know — whereas a per-call
block asserts something about *this* item that it derives only from the rail.

**Reversal trigger.** If the classification has to be split per inner action, or if a second
consumer needs the block on flat tools, the projection has outgrown its seam and belongs in the
process model itself (as a declared per-verb outcome) rather than in the MCP adapter.
