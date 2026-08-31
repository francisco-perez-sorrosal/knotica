---
id: dec-087
title: The Fill lane terminates on the gap record — the gate closes it, and a human can dismiss it
status: accepted
category: architectural
date: 2026-08-10
summary: "GAP_STATUSES declares open/resolved/dismissed but no code path in src/knotica ever writes the two terminal values, so the Fill lane's terminal state does not exist; a merging source gate now closes its originating gap inside the existing mutation span, and a review action on the Fill lane dispatcher gives a human the dismiss transition."
tags: [gap-fill, lifecycle, fill, source-gate, mcp, tool-surface, swimlanes]
made_by: agent
agent_type: systems-architect
branch: worktree-process-swimlanes
pipeline_tier: full
affected_files:
  - src/knotica/core/records.py
  - src/knotica/core/gapfill/
  - src/knotica/core/source_gate.py
  - src/knotica/mcp_server/tools_gaps.py
dissent: "Closing a gap on a merge assumes one merged source answers the whole gap, which is a modelling claim nothing verifies — a gap can be broad enough that one ingested source addresses a third of it, and this design will report it closed while the knowledge hole is still open, silently converting a monotonic-but-honest counter into a non-monotonic and possibly optimistic one."
---

## Context

`core/records.py:87` declares `GAP_STATUSES = frozenset({"open", "resolved", "dismissed"})`, and the
comment above it states the intended lifecycle: *"P1 writes `open`; P3/P4 flip it terminal."*
`mcp_server/tools_gaps.py:68` exposes all three as read filters with a stable `status_counts` breakdown.

**No code path in `src/knotica/` ever writes `"resolved"` or `"dismissed"`.** An exhaustive grep over the
package finds exactly two sites: the vocabulary declaration and the read filter. Every writer —
`gap_classifier`'s eval-path writes, `gapfill.report_gap`, `gapfill.file_retracted_gap` — constructs an
`open` record. The consequence is that `gaps.open_total` only ever grows, `status_counts` reports
permanent zeros for two of three buckets, and the promise in the comment describes an intention rather
than a behaviour.

This was invisible while the surface was tool-shaped. It stops being invisible under the swimlane
redesign, whose locked design rule is that **a lane may not terminate in another lane** and whose Fill
lane is declared `gap → discover → approve → ingest → gate → closed | quarantined`. The rail cannot
render a terminal state that nothing writes. Today the nearest thing to closure lives on the *suggestion*
record — `approved → ingested` with a `merged` gate outcome (`core/source_gate.py:119-169`) — which is
the gate's output and therefore Improve's object. Terminating Fill there would encode the defect as
the design.

## Decision

**Fill terminates on the gap record, and the gap lifecycle gets its two missing writers.**

1. **Machine path — the gate closes the gap.** `gapfill.apply_gate_outcome`, on `verdict="merged"`,
   resolves the suggestion's `gap_id` (`SuggestionRecord` already carries it, `records.py:505`) and flips
   that gap record `open → resolved`. It lands inside the same `runner._mutation_span()` that already
   brackets `_keep` and the gate-outcome stamp (`source_gate.py:145-166`), so the close is atomic with
   the merge it follows and adds no commit of its own. A refusal writes nothing — the suggestion stays
   re-workable and the gap stays open, which is correct.

2. **Human path — a review action on the Fill lane dispatcher.**
   `fill(action="review_gap", topic, gap_id, decision, reason, mode, vault)`, mirroring the semantics
   of the existing `suggestions_review`. `decision="dismiss"` is legal only from `open` and requires a
   reason; `decision="reopen"` is legal only from `dismissed`. Any other source status is rejected with
   `INVALID_ARGUMENT`, matching the `_ALLOWED_FROM` / `_TARGET_STATUS` lifecycle-table discipline
   `apply_decision` already uses for suggestions.

This adds **zero registrations**. Under `dec-094` the operator surface is six lane
dispatchers, and gap review is operator traffic that belongs to exactly one lane — so it is an action
entry, not a tool. The conversational gap verb `gap_report` is unaffected: `dec-088` keeps it
in the flat tier because the client-as-brain calls it mid-answer.

*(Earlier drafting proposed a new flat `gap_review` tool. That predated the tiered lane surface; with six
lane dispatchers the action form is strictly cheaper and lands the verb in its lane.)*

## Considered Options

### Option 1 — Gate closes the gap, plus a `review_gap` action on the Fill lane for dismissal (chosen)

Makes both declared terminal values live. Fill terminates on its own record, and the human transition
lands in the lane that owns it at zero registration cost.

### Option 2 — Redefine Fill's terminal state as suggestion-terminal (`ingested`)

Zero new code, zero new tools. Rejected: it makes Fill terminate on the gate's output, which the
swimlane model assigns to Improve, and so violates the locked rule the redesign exists to enforce. It
also leaves `GAP_STATUSES` permanently two-thirds dead and `gaps.open_total` permanently monotonic —
encoding the bug as the design and removing the pressure to ever fix it.

### Option 3 — Fold `gap_report` into the Fill lane too, so every gap verb is one dispatcher

Net a further −1 registration. Rejected: `gap_report` is the Answer→Fill bridge the client-as-brain
calls mid-answer, and `dec-045`/`dec-003` keep high-density conversational verbs flat —
`dec-088` holds that boundary explicitly. A schema-weight win bought with routing risk on the
conversational path is a bad trade.

### Option 4 — Close the gap automatically on suggestion `reject`

Rejected: rejecting one candidate source says nothing about whether the knowledge hole remains. The two
records have genuinely different lifecycles, which is why they are two records.

### Option 5 — Derive closure at read time by joining suggestions → gaps on `gap_id`

No writer, no schema change. Rejected: it makes every gap read pay a join, leaves `status_counts`
reporting a value no record holds, and produces a terminal state that vanishes if a suggestion is
withdrawn — a lane whose terminal state can un-happen is not a terminal state.

## Consequences

**Positive.** Fill's rail renders a real terminal state for the first time. Two of three declared gap
statuses stop being dead vocabulary, so `status_counts` becomes informative and `gaps.open_total` stops
being a ratchet. The close is atomic with the merge, so no partial state exists where a source is merged
and its gap is still open. The guillotine's `retracted` gaps gain a closure path for free: a re-grounding
source merging through the gate closes the gap the retraction filed.

**Negative.** A new human decision the user must actually make — an un-dismissed, un-resolvable gap now
looks stale rather than merely accumulating. `core/gapfill.py` is already 935 lines against an 800
ceiling (td-042) and grows further. And a gap now has two writers to its terminal status, machine and
human, so the lifecycle table must be read in two places. This also supersedes the workaround
`INTERFACE_DESIGN.md § 2.5` adopted (keying Fill's terminal state on the *suggestion*) — that surface
must be re-pointed at the gap record.

## Disconfirmation

- **Falsifier:** if a gap that a merged source only *partially* answers gets closed and then has to be
  re-filed — the same knowledge hole appearing as a second `gap_id` — then one-merge-closes-one-gap is
  the wrong model, and closure should be a human decision (or an eval-proven one: the regression that
  created a `measured` gap no longer reproducing) rather than a side effect of a merge. Watch for
  duplicate gaps whose evidence overlaps a recently-resolved one.
- **Steelmanned runner-up:** Option 5, read-time derivation. A gap is not really a record with a
  lifecycle — it is a *claim about the corpus* that is true or false at any moment, and the honest way
  to answer "is this gap closed?" is to ask the corpus, not a stored flag that can go stale the instant
  a page is edited. Option 5 loses only because a lane rail needs a stable terminal state that survives
  a suggestion being withdrawn, and because the join cost lands on every read.
- **Reversal trigger:** the first re-filed gap that duplicates a `resolved` one, or a `measured` gap
  reproducing in a later eval after being auto-closed. Either shows the merge is the wrong closure
  signal and that closure should move to an eval-proven or human-confirmed event.
