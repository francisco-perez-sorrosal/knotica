---
id: dec-025
title: Gap-record schema v1 — the persisted P1→P3 knowledge-gap contract
status: accepted
category: architectural
date: 2026-07-18
summary: Knowledge-cause fault verdicts (genuine_gap, dilution) are persisted as a new schema-versioned GapRecord to a committed append-only <topic>/.knotica/gaps/gaps.jsonl, written in its own VaultTransaction under a bookkeeping path that does not re-trigger observation; the record carries a stable gap_id/qa_id join key, a status lifecycle, and a self-contained evidence snapshot the P3 discovery queue consumes.
tags: [loop, phase-3a, gap-fill, gap-record, schema, records, dec-006, vault-transaction, stateless-server, p3-contract]
made_by: agent
agent_type: systems-architect
branch: worktree-hackathon-loop-ideas
pipeline_tier: standard
affected_files:
  - src/knotica/core/records.py
  - src/knotica/core/gap_classifier.py
affected_reqs: [REQ-06, REQ-08]
re_affirms: dec-006
dissent: A committed append-only jsonl grows unbounded and re-logs a persistent gap every cycle; an uncommitted staging file (the golden.staging.jsonl precedent) would avoid git-history bloat, at the cost of a stateless MCP tool being unable to read a pending queue that was never committed.
---

## Context

P1's classifier (companion ADR) produces knowledge-cause verdicts that must survive as the input to P3's
suggestion/discovery queue. Knotica is a stateless server: the vault (git) is the only state, so the queue
must be a committed vault file, not server memory (dec-004). Two persisted-record precedents exist:
`metrics.jsonl` (committed, append-only, `schema_version`-carrying — dec-006) and `golden.staging.jsonl`
(uncommitted, human-review-before-freeze). The gap queue is machine output consumed per-call by a P3 MCP
tool and a dashboard panel (dec-020) — so it must be *committed* and *self-describing*, which lands it on
the dec-006 record discipline. dec-023 supplies the stable `qa_id` join key the record keys on. No
suggestion/gap record schema exists today (research §Data-Gaps #4 — green field).

## Decision

Introduce **GapRecord (schema_version 1)**, a new self-versioned record in `core/records.py` alongside
`QARecord`/`MetricsRecord`, persisted one-per-line to a committed append-only
`<topic>/.knotica/gaps/gaps.jsonl`.

Fields: `schema_version`, `gap_id` (= `sha1(topic|qa_id|fault_class)[:16]`, the stable dedup + P3 join
key), `topic`, `qa_id` (= golden `QARecord.id`), `fault_class` (`genuine_gap` | `dilution` — only
knowledge-cause classes are ever written), `status` (`open` at write; P3/P4 own `resolved`/`dismissed`),
`classifier_version` (a second capability probe, independent of `schema_version`), `detected_generation`,
`detected_at` (ISO 8601 UTC), `scalar_at_detection`, `baseline_scalar`, `question` (human-readable, not a
join key), `reference_pages` (`QARecord.pages_used`), `reference_pages_exist` (the genuine-gap
discriminator, frozen into the record), `evidence` (score deltas, `retrieval_trace`, `pages_added`,
`pages_removed`, `prior_generation` — a self-contained snapshot at detection), and `manifest_ref` (a
clone-relative provenance hint, may be pruned).

`fault_class` and `status` are persisted as bare tagged strings (not a `StrEnum`): the value is read
out-of-process by P3, and a plain string round-trips without enum-coercion failure on an unknown value —
consistent with the codebase's `verdict`/`source` string-constant convention on `QARecord`.

**Write transaction shape.** The write is its own `VaultTransaction(store, root, "gap_record", topic,
title)` on the live vault (one-commit-per-mutation; never piggybacked on the loop-state or metrics
commit). The block reads any existing `gaps.jsonl`, filters out `(qa_id, fault_class)` pairs that already
have an `open` record (dedup — a persistent regression does not spam the queue), appends the new records,
and writes the file once.

**Observe-safety.** `gaps.jsonl` lives under `.knotica/gaps/` — a `.knotica/`-but-not-`prompts/` path,
which `_content_changed_since` (`loop.py:439-462`) classifies as bookkeeping. The gap-record commit
therefore does not re-trigger `observe_default`, so writing a gap record cannot induce an observe loop.

## Considered Options

### Option A — committed append-only jsonl at `.knotica/gaps/gaps.jsonl`, schema-versioned (chosen)

- Pro: a stateless MCP tool reads the pending queue per-call (P3/dashboard need this); permanent audit
  trail; dec-006 record discipline gives P3 a capability probe; observe-safe location.
- Con: unbounded growth; re-logs handled by the `open`-dedup guard, but resolved records still accumulate
  until a future prune.

### Option B — uncommitted staging file (the `golden.staging.jsonl` precedent)

- Pro: no git-history bloat; pending records never enter history until approved.
- Con: a stateless server cannot read an uncommitted file that a *different* process (the loop) wrote —
  the loop and the MCP server are separate processes over the same vault; only committed state is shared.
  Fatal for the P3 read path. Rejected.

### Option C — fold gap records into `metrics.jsonl` or loop-state

- Pro: no new file.
- Con: pollutes a dec-006-frozen record (metrics) or the single loop-state blob with an unrelated concern;
  violates one-commit-per-mutation and the record's cohesion. Rejected.

### Option D — no `schema_version` (rely on tolerate-unknown parsing)

- Pro: least code.
- Con: P3 has no read-time probe to distinguish a v1 record from a future shape; dec-006's whole point is
  that machine-readable records self-version. Rejected.

## Consequences

**Positive:**
- P3 reads one committed artifact per topic with a stable `gap_id`/`qa_id` join key and a
  fault-class filter; the evidence snapshot is self-contained (no dependency on P2's candidate schema).
- dec-006 discipline extended cleanly to a new record kind; the P3 contract is explicit and probeable.
- The write is invariant-clean: own transaction, observe-safe path, live-vault (readable by the stateless
  server), one commit per detection event.

**Negative / costs:**
- A new committed file grows with distinct knowledge gaps; the `open`-dedup guard bounds per-cycle growth
  but resolved-record pruning is deferred (a future maintenance pass, mirroring `loop/r/*` pruning).
- The record shape is now a P3 consumer contract; changing it later needs a `schema_version` bump.

## Disconfirmation

- **Falsifier:** If P3 turns out to need to read *pending, pre-commit* gaps (e.g. the loop proposes gaps
  the human vetoes before they ever commit), a committed-only queue is wrong and a staging tier (Option B)
  or a two-file open/committed split would have been required.
- **Steelmanned runner-up:** Option B (uncommitted staging) is strongest if the loop and the reader were
  the *same* process — then in-process or uncommitted state would suffice and avoid history bloat. It fails
  only because knotica's stateless-server invariant puts the writer (loop) and reader (MCP server) in
  different processes sharing state solely through committed git.
- **Reversal trigger:** If `gaps.jsonl` grows past a few hundred lines in practice, add a prune step
  (drop `resolved`/`dismissed` records beyond a retention window) — additive, no schema change. If a
  pending-veto workflow materializes, revisit the staging split.

## Prior Decision

Re-affirms **dec-006** (freeze machine-record schemas with `schema_version` at Phase 0). dec-006 froze
five record kinds and deliberately left room for new ones to adopt the same discipline. GapRecord is a new
record kind that adopts it verbatim (per-record `schema_version`, tolerate-unknown-fields parse,
documented shape). It does not reopen or modify any dec-006-frozen shape; it adds a sibling under the same
rule, exactly as dec-023 did for the manifest. A future supersession would require evidence that gap
records should not self-version or should not be committed.
