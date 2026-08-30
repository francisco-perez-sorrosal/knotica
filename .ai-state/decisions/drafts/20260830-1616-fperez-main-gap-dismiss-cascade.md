---
id: dec-draft-ac066305
title: Gap dismissal cascades to its still-open suggestions, and refusals name the legal exits
status: proposed
category: behavioral
date: 2026-08-30
summary: dismiss closes the gap's pending/approved/deferred suggestions as rejected in the same commit, and a refused suggestion transition now names the decisions legal from the record's actual status
tags: [gap-fill, lifecycle, fill, suggestions, mcp, error-ergonomics]
made_by: agent
agent_type: orchestrator
branch: main
pipeline_tier: lightweight
affected_files:
  - src/knotica/core/gapfill.py
  - src/knotica/core/process_model.py
  - src/knotica/mcp_server/tools_gaps.py
---

# Gap dismissal cascades to its still-open suggestions, and refusals name the legal exits

## Context

A field report from a Claude Desktop user surfaced two joined defects. First,
an approved suggestion read as terminal: `suggestions_review` refused `reject`
and `defer` from `approved` with a fix text naming only the *attempted*
decision's legal sources ("Only a pending/deferred suggestion can be
rejected"), and the `fill` lane narration listed only "approve, reject or
defer" — so `withdraw`, the un-approve that has existed since v0.2.0, was
undiscoverable from the lane surface, which drops each verb's own rich
description by construction. Second, dismissing a gap left its suggestions
untouched: nine suggestions sat `approved` against gaps that no longer wanted
filling, and with `withdraw` unfound they were unreachable.

## Decision

- `apply_gap_decision(decision="dismiss")` closes the gap's still-open
  suggestions (`pending`/`approved`/`deferred` → `rejected`, reason
  `gap dismissed: <reason>`) inside the same `VaultTransaction`, returning
  their ids as `cascaded_suggestion_ids`. `ingested` records keep their
  status; `reopen` resurrects nothing — a rejected record does not dedup
  discovery, so re-draining a reopened gap re-proposes its sources.
- A refused suggestion transition appends the decisions legal *from the
  record's actual status*, derived from `_ALLOWED_FROM` so the hint can never
  drift from the state machine; the unknown-decision enumeration is likewise
  derived rather than hand-listed (it had gone stale, omitting `withdraw`).
- The `fill` lane narrations now name `withdraw` on `suggestions_review` and
  the cascade on `review_gap`.

## Considered Options

### Cascade to `rejected` in the same transaction (chosen)

- Pro: symmetric with the machine path — a merged gate verdict closes its
  originating gap inside the mutation span (dec-087); one commit, no
  half-state to strand.
- Pro: stays inside the five-status vocabulary; counts, filters, and the
  dashboard need no schema change.
- Con: a reopen does not restore the queue — accepted, since re-discovery
  re-proposes with fresh ranking.

### Flag orphaned at read time

- Pro: no write amplification.
- Con: invents a sixth visible state, every consumer must learn it, and the
  queue still holds records nobody can act on — the strand persists, labeled.

### Demote to `pending`

- Con: dishonest — the gap is dismissed, so the queue would show actionable
  cards for questions nobody wants answered; noise instead of closure.

## Consequences

- A dismissed gap's queue drains in the same commit; nothing is left that
  only `withdraw` could move.
- Cascade-closed records are auditable: their `decided_reason` names the
  dismissal that closed them.
- The refusal hint makes any future decision added to `_ALLOWED_FROM`
  self-advertising at the point of a dead-end call.
