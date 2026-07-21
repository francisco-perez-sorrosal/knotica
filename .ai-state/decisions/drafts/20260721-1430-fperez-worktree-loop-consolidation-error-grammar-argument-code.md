---
id: dec-draft-19d50c6b
title: Add INVALID_ARGUMENT error code — separate argument validation from cursor staleness
status: proposed
category: architectural
date: 2026-07-21
summary: Introduce a dedicated INVALID_ARGUMENT error code so bad mode/status/limit/action/reference_pages arguments stop masquerading as INVALID_CURSOR (whose canonical fix text is actively wrong for them).
tags: [error-grammar, agentic-interface, contract, self-recovery]
made_by: agent
agent_type: interface-designer
branch: worktree-loop-consolidation
pipeline_tier: full
re_affirms: dec-001
affected_files:
  - src/knotica/core/errors.py
  - src/knotica/mcp_server/tools_suggestions.py
  - src/knotica/mcp_server/tools_source_ingest.py
  - src/knotica/mcp_server/tools_status.py
dissent: "One more enum value is surface the model must learn; INVALID_CURSOR with a call-specific fix string already carries the recovery action, so the split may be ceremony over a working contract."
---

## Context

The shared error contract (dec-001, `core/errors.py`) is otherwise exemplary:
`{code, message, fix, retryable}` with the grammar "X failed because Y. To fix:
Z." The `ErrorCode` enum has no generic argument-validation code, so every
adapter shoehorns plain bad-argument validation into `INVALID_CURSOR`:

- `tools_suggestions.py`: bad `status`, `limit`, `action`, `mode`,
  `reference_pages` → `INVALID_CURSOR`
- `tools_source_ingest.py`: bad `mode`, missing source/pages → `INVALID_CURSOR`
- `tools_status.py`: bad `limit`, negative `before_generation` → `INVALID_CURSOR`

`INVALID_CURSOR`'s canonical fix text (`DEFAULT_FIX`) is **"Restart the search
without a cursor."** For a bad `mode='aply'` argument, that instruction is
actively wrong — it tells the self-recovering model to do something unrelated to
the real fix. The call sites pass a corrected per-call `fix` string, but the
**code** the model branches on still says "cursor problem," conflating two
recovery classes: *stale/opaque-token* (restart the paginated read) versus
*malformed-argument* (fix this specific arg and re-call). This violates Bloch's
least-astonishment and consistency, and weakens same-turn self-recovery — the
whole point of agent error ergonomics.

## Decision

Add `INVALID_ARGUMENT` to `ErrorCode` with canonical fix text *"Correct the named
argument and call again"* and `retryable=False`. Reassign every plain
argument-validation failure (bad `mode`, `status`, `action`, `limit`,
`before_generation`, `reference_pages` shape) from `INVALID_CURSOR` to
`INVALID_ARGUMENT`. `INVALID_CURSOR` returns to meaning exactly one thing: a
stale or malformed **pagination cursor** whose correct fix is to restart the read
without a cursor.

The message stays call-specific ("mode must be 'dry-run' or 'apply', got
'aply'"); the `fix` stays the exact next action; only the **code** — the stable
discriminator — is corrected so it no longer contradicts the fix.

## Considered Options

### Option A — Add INVALID_ARGUMENT (chosen)
Minimal, additive enum change. Consumers tolerant of unknown codes (see the
growth ADR) are unaffected; consumers that branch on `INVALID_CURSOR==restart`
stop mis-recovering on argument errors. Restores one-code-one-recovery-class.

### Option B — Keep INVALID_CURSOR, rely on the per-call fix string
Rejected: the code is the machine-parse surface; a code that contradicts its own
default fix text is a latent trap for any consumer that branches on code before
reading `fix`, and the model re-reads the code every call.

### Option C — A broad VALIDATION code covering cursors too
Rejected: merges the two recovery classes the other direction — a cursor restart
and an arg fix are genuinely different next-actions; collapsing them re-creates
the ambiguity.

## Consequences

**Positive:** every error code maps to exactly one recovery class; the
self-recovery grammar is honest end-to-end; new validation sites have an obvious
correct code to reach for (prevents the overload recurring as the surface grows).

**Negative:** one more enum value; a one-time reassignment sweep across three
adapter modules plus their tests. Any external consumer that hardcoded
`INVALID_CURSOR` for argument errors (should be none — internal contract) would
need updating.

## Disconfirmation

- **Falsifier:** if no consumer ever branches on `code` before reading `fix`
  (i.e. `fix` alone always drives recovery), the split buys nothing and is pure
  ceremony.
- **Steelmanned runner-up:** Option B — the contract already ships a correct
  per-call `fix`; a disciplined consumer reads `fix` and recovers correctly today,
  making the code's meaning cosmetic.
- **Reversal trigger:** if the enum grows unwieldy and evidence shows models
  recover from `fix` text regardless of code, collapse the validation codes back.

## Prior Decision

Re-affirms **dec-001** (agent error grammar + idempotency): the
`{code, message, fix, retryable}` envelope and the "X failed because Y. To fix: Z"
grammar are kept verbatim. This ADR only corrects a code-assignment inconsistency
that accreted as the surface grew — it strengthens dec-001 rather than changing it.
