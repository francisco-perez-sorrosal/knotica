---
id: dec-draft-14fe025b
title: Agent error grammar (structured, in-result) + idempotency-by-result-state
status: proposed
category: architectural
date: 2026-07-03
summary: MCP tools return a structured {code,message,fix,retryable} error object in the tool result (not a transport exception); all mutating tools are idempotent by resulting vault state with no client-managed keys.
tags: [mcp, error-handling, idempotency, agent-interface, stateless, git]
made_by: agent
agent_type: interface-designer
branch: pipeline-wiki-mvp-core
pipeline_tier: standard
affected_files:
  - src/knotica/mcp/
  - src/knotica/core/
dissent: A fixed enum of error codes plus result-state idempotency adds ceremony a small MVP could skip by raising plain exceptions and letting retries re-commit.
---

## Context

The tool consumer is a language model that must **self-recover from an error in the same turn** with no human and no doc lookup, and that will **retry** after transport failures. Two contracts must be fixed: (1) how errors are shaped and delivered, and (2) what a retry of a mutating tool does. The server is stateless (no key store), and every mutating op makes one git commit (the audit unit).

## Decision

**Error grammar:** every failure returns, *in the tool result content*, `{"error": {"code, message, fix, retryable}}` where `code` is a stable enum (`NOT_CONFIGURED`, `TOPIC_NOT_FOUND`, `PAGE_NOT_FOUND`, `RESERVED_NAME`, `SOURCE_EXISTS`, `INVALID_FRONTMATTER`, `LOCK_BUSY`, `GIT_ERROR`, `INVALID_CURSOR`), `message` follows "X failed because Y", `fix` gives the exact next action (Z), and `retryable` is a bool (`true` only for `LOCK_BUSY`). Delivering the error *in the result* (not as an MCP transport exception) guarantees the model sees the actionable text. `SECRET_SCRUBBED` rides on a *successful* write as a `warnings` field, not an error.

**Idempotency:** all four mutating tools are idempotent by **resulting vault state** — no client-supplied idempotency keys (a key store would break statelessness). `write_page` → content-hash (identical → no commit, `changed:false`); `store_source` → citation-key + content (identical → no-op; different content, same key → `SOURCE_EXISTS`, immutable); `create_topic` → existence (`existed:true`); `curate_example` → hash of (query+answer+verdict) (`appended:false`). A no-op makes **no commit**, keeping "one commit per effective mutation" exact.

## Considered Options

### A. Structured in-result error + result-state idempotency (CHOSEN)
- Pros: model self-recovers in-turn; retries are safe with zero key management; audit log has no duplicate/no-op commits; stateless-clean.
- Cons: an error enum to maintain; each tool computes a content/existence hash before committing.

### B. Raise transport exceptions; no idempotency
- Pros: least code.
- Cons: error text may not reach the model as actionable content; retries double-commit (duplicate pages, duplicate qa.jsonl lines) — silent corruption of the flywheel and audit trail.

### C. Client-managed idempotency keys (Stripe-style)
- Pros: classic, explicit dedup.
- Cons: requires a server-side key→result store = session/persistent state → **violates the stateless-server principle**. Rejected on constraint grounds.

## Consequences

- Positive: retry-safe mutations without state; clean git history; predictable model recovery; one shared error module across all tools (consistency).
- Negative: content-hashing on the write path; the `LOCK_BUSY` retryable path must be honored by client retry logic (the flock guard is load-bearing given long-lived shared stdio servers).

## Disconfirmation

- **Falsifier:** if in practice the model ignores the `fix` field and flails anyway, or if mutating-tool retries are so rare that double-commits never occur, the structure/idempotency machinery earned nothing.
- **Steelmanned runner-up:** Option B (raise + no dedup) is the smallest thing that could work for a single-user local MVP where retries are hand-driven and rare; git itself is the undo, so an occasional duplicate commit is cheap to revert manually.
- **Reversal trigger:** if the stateless-server constraint is ever relaxed (Phase 4+ with a session store), reconsider Option C's explicit idempotency keys for the network-retry case where result-state hashing is insufficient (e.g., partially-applied multi-file ops).
