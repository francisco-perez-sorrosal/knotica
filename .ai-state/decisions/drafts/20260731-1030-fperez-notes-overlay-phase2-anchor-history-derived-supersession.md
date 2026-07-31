---
id: dec-draft-a7f3c218
title: Note anchor history is append-only with per-page derived supersession
status: proposed
category: architectural
date: 2026-07-31
summary: A correction appends an anchor bearing an optional trailing kind token; supersession and detachment are derived per distinct page from document order and never stored, because storing them requires rewriting an earlier anchor.
tags: [notes, anchoring, append-only, supersession, backward-compatibility]
made_by: agent
agent_type: orchestrator
branch: worktree-notes-overlay-phase2
pipeline_tier: full
dissent: A stored superseded_by would make liveness an O(1) field read instead of a per-page scan, and the scan runs on every drift-queue open — the cheapest correct design was rejected on an invariant, not on cost.
affected_files:
  - src/knotica/core/notes/anchor.py
  - src/knotica/core/operations/reanchor_note.py
re_affirms: dec-058
---

## Context

`dec-058` established the bi-partite anchor: an immutable record plus a projection
derived at read time. Phase 2 added corrections (`reanchor`, `detach`, `archive`), which
forced a question `dec-058` left open — how a *history* of anchors is written and how a
reader knows which pin is current.

Both `IMPLEMENTATION_PLAN.md` and `INTERFACE_DESIGN.md` § 7 specified strikethrough
rendering plus a stored `superseded_by` field. A test author refused to build it, correctly.

## Decision

**The bullet grammar gains an optional trailing `kind` token** (`pinned` · `reanchored` ·
`kept` · `detached`):

```
- [[page#heading]] — `fidelity` · pinned@`sha` [· at=N] [· kind]
```

- **An absent token means `pinned`**, so every note already on disk parses unmigrated.
- **`kind` is an opaque `str`**, not a closed literal — a later-generation value round-trips
  through an older reader, exactly as `fidelity` already does.
- **`pinned@<sha>` stays the signature for every kind.** A bullet is an anchor iff it carries
  a backticked fidelity *and* `pinned@<sha>`. A `reanchored@` variant would make newer
  bullets invisible to older readers.
- **Supersession is derived, never stored.** The key is the `page` when non-empty and
  `("", quote)` otherwise; for each key the newest record wins, and a `detached` record
  terminates that key's chain only.
- `anchor_of_record` remains index 0 forever; `live_anchors` is the liveness primitive;
  `effective_anchor` answers the narrower "newest pin regardless of page" and is not a
  liveness primitive.

## Considered Options

### Option A — stored `superseded_by` with strikethrough rendering (rejected; was the plan of record)

- **Pro** — liveness is a field read, not a scan; the history is visually obvious in Obsidian.
- **Con, decisive** — marking an earlier anchor superseded means **rewriting that bullet's
  bytes**. AC-09 states the anchor of record is never modified or removed. The plan's own
  defence — that replacing a record inside a frozen dataclass's tuple is "not a mutation" —
  confuses Python-level immutability with the on-disk contract; the tuple is not the artifact.
- **Con** — capture's idempotency fingerprints index 0 through `anchor_of_record`, so a
  rewritten index 0 silently stops every previously captured note from matching itself.

### Option B — append-only with derived supersession (chosen)

- **Pro** — the append-only invariant holds literally, not approximately.
- **Pro** — a reader still sees the history: anchors append, the newest pin for a page is last,
  and the new record's own `kind` says what happened.
- **Con** — liveness is a per-page scan, and the drift queue opens run it.

### Option C — key page-less anchors by position rather than quote (rejected)

- **Con** — page-less anchors would then be undetachable: a `detach` record copies its
  target's quote, which is what lets it land in the same group and terminate that chain.

## Consequences

**Positive** — no migration; forward-compatible with later `kind` values; the append-only
invariant is structural rather than conventional.

**Negative** — `live_anchors` is O(anchors) per note per read. Two defects were found and
fixed in it during Phase 2 (note-scoped liveness, and `page=""` collapsing every
topic-fidelity anchor into one bucket), both of which a stored field would not have had.

## Disconfirmation

- **Falsifier** — if a real vault reaches note counts where per-page derivation measurably
  slows the drift queue, the derived design costs more than the invariant is worth.
- **Steelmanned runner-up** — Option A is genuinely simpler to read and to render, and its
  invariant violation is invisible in practice: users rarely inspect an anchor's bytes. If
  AC-09 were weakened to "the anchor's *content* is never changed, though its rendering may
  be", Option A would be correct and cheaper.
- **Reversal trigger** — a persisted projection index under `.knotica/` (already a designed
  seam) would make stored liveness natural; if that index is ever built, revisit this.

## Prior Decision

Re-affirms `dec-058`'s append-only mandate and derived-projection principle, and *restates*
its on-disk rendering for the second time — the first restatement (callout → markdown list)
is recorded in `dec-058`'s own amendment note. `INTERFACE_DESIGN.md` § 7 carries a
correction note; its example predated shipped Phase 1 code and showed a status token the
parser never accepted.
