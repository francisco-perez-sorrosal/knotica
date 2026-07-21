---
id: dec-draft-4f342a52
title: Vault-mutation lock scope — widen the flock to bracket the full real-vault git-mutation span per loop pass, reentrantly
status: proposed
category: architectural
date: 2026-07-21
summary: Realize the "mutating ops are flock-guarded" invariant at the correct granularity — the existing vault_lock brackets only the final state commit, leaving the loop's checkout/merge/delete spans unguarded; widen it to the full contiguous real-vault git-mutation span per loop pass, made reentrant so nested VaultTransactions reuse the held lock, released git-clean, with span-entry self-heal for crash recovery and a bounded acquire-timeout → retryable LOCK_BUSY.
tags: [loop, locking, flock, concurrency, daemon, git-corruption, vault-transaction, invariant-realization]
made_by: agent
agent_type: systems-architect
branch: worktree-loop-consolidation
pipeline_tier: full
re_affirms: dec-008
affected_files:
  - src/knotica/core/lock.py
  - src/knotica/core/transaction.py
  - src/knotica/core/loop.py
  - src/knotica/core/source_gate.py
  - src/knotica/core/vcs.py
dissent: "A single coarse span lock held across a whole loop pass's git sequence serializes the daemon and the synchronous MCP-gate hard; if the bracketed span is drawn even slightly too wide (accidentally enclosing clone-based eval/arena compute), the synchronous gate call blocks for minutes and the UX regresses worse than the corruption it fixes."
---

## Context

A P-A integration finding (reproducible ~60–65% under barrier-synced contention;
`.ai-work/loop-consolidation/TEST_RESULTS_implementer.md` Step 11 + LEARNINGS § Edge
Cases) falsified the SYSTEMS_PLAN risk row that claimed "the existing flock serializes all
three writers — the daemon adds no new race." It does not: `core/lock.py::vault_lock`
(acquired by `VaultTransaction` at `__enter__`, always against the canonical `vault_root`)
brackets only its **own commit**. The loop's real-vault git-mutation spans —
checkout/merge/delete in `observe_default` (fetch `loop/r` → checkout default → non-ff
merge → prune → write state) and in `_keep`/`_discard`/`_race_then_resolve` (promote
winner / ff-merge / delete branch) — issue raw `VaultVcs` git calls **outside** any
transaction. Two passes contending on the same vault interleave those spans and produce
real corruption: `MERGE_HEAD exists`, `cannot do a partial commit during a merge`,
`cannot lock ref`.

Pre-existing (not a P-A regression), but **the daemon (dec-draft-64a38a63) makes it
live**: a supervised background watcher pass and a synchronous MCP-gate pass
(`tools_source_ingest.py::_run_gate`) are separate processes contending on one vault. An
interim mitigation (`72bdc73`) broadened `VaultVcs._run`'s retry predicate to the
transient-race signatures — reduces, does not eliminate. This must be fixed before the
daemon (P-D Step 44) can proceed.

## Decision

**Widen the existing `vault_lock` flock to bracket the full contiguous real-vault
git-mutation span per loop pass, reentrantly.** This *realizes* dec-008's documented
"single vault-mutation path, flock-guarded" invariant at the correct (logical-operation)
granularity — it was implemented at the (single-commit) granularity, leaving the loop's
multi-step git spans partially guarded. Six properties:

1. **Bracket the real-vault git span, not clone compute.** The corruption comes from
   concurrent *git* operations on the *real* vault. Eval and the arena race run on a
   **clone** (they never touch the real vault's git state) — so they run **unlocked**;
   only the contiguous real-vault git sequences (fetch-home → checkout → merge → prune →
   state-commit; promote-winner-commit; ff-merge/delete) are bracketed. This bounds
   hold-duration to git operations (sub-second-to-seconds), never an LLM arena/eval
   (minutes). The synchronous source-gate never enters the arena (dec-036), so its locked
   span is small.
2. **One lock, widened — not a second lock layer.** Reuse `core/lock.py`'s flock (the
   right primitive: cross-process, and **auto-released by the OS on process death**).
   Rejected a separate per-vault mutation lock: a second layer adds lock-ordering /
   deadlock surface for no benefit; there is exactly one lock, always acquired at span
   start → no hold-and-wait, no ordering deadlock.
3. **Reentrant.** A loop pass acquires the span lock, then calls `VaultTransaction`
   (`write_loop_state`) *inside* it. `vault_lock` opens a fresh fd and `flock`s each call,
   so a naive nested acquisition **self-deadlocks in-process** (two fds, second `LOCK_EX`
   blocks). Add an in-process reentrancy guard (thread-local depth keyed by canonical
   `vault_root`): only the outermost acquisition flocks/unflocks; nested `vault_lock` /
   `VaultTransaction` acquisitions for the same root reuse it.
4. **Every release leaves the vault git-clean.** The correctness property that makes
   span-boundary interleaving safe: no lock release may leave a dangling `MERGE_HEAD`,
   partial merge, or held index lock. Passes acquire/release the span lock possibly more
   than once (merge-home span, then promote-winner span); each locked span is
   individually git-atomic.
5. **Span-entry self-heal (crash recovery).** A crash mid-span auto-releases the flock
   (OS) — **no stale-lock wedge** — but may leave the git tree dirty. At span entry, abort
   any dangling `MERGE_HEAD` and clear a stale `.git/index.lock` before proceeding
   (extends the existing `_ensure_union_log_merge` self-heal discipline). Composes with
   heartbeat liveness: a dead runner's heartbeat goes stale → the supervisor restarts it →
   the restarted pass self-heals at span entry. This satisfies "crash mid-span must not
   wedge the vault."
6. **Bounded acquire → retryable `LOCK_BUSY`.** Keep the existing bounded acquire-timeout
   (`vault_lock(timeout=…)` → `LockBusyError` → `KnoticaError(LOCK_BUSY, retryable=True)`,
   dec-001). A synchronous MCP-gate that cannot get the span lock returns `LOCK_BUSY`
   (retryable) rather than hanging; the `72bdc73` retry predicate stays as
   defense-in-depth.

## Considered Options

### Option A — Widen the existing flock to the real-vault git span, reentrant (chosen)
Fixes the corruption at the root, bounds hold to git ops (compute stays unlocked), reuses
the OS-auto-released flock, one lock (no deadlock surface), realizes dec-008's invariant.

### Option B — A separate per-vault coarse mutation lock, distinct from the transaction flock
Rejected: two lock layers → lock-ordering / nested-deadlock surface the coordinator
explicitly flagged; no benefit over widening the one lock that already exists.

### Option C — Rely on the `72bdc73` retry predicate alone
Rejected: retry reduces but does not eliminate the race (its own commit message says so);
it treats symptoms (retry the corrupted op) not the cause (the span was never atomic).

### Option D — Serialize the whole loop pass including clone eval/arena under one lock
Rejected (the dissent): drawing the bracket around clone compute makes the synchronous
gate block for minutes — a worse UX regression than the corruption. The clone-vs-real-vault
boundary is the load-bearing scoping line.

## Consequences

**Positive:** git corruption under daemon + MCP-gate contention is eliminated at the
source; the daemon (Step 44) is unblocked; dec-008's flock-guarded invariant is realized
uniformly; crash recovery is defined (OS auto-release + span-entry self-heal); the
synchronous gate degrades to a retryable `LOCK_BUSY`, never a hang.

**Negative:** loop passes and the synchronous gate now serialize at the git-span
granularity (acceptable — git spans are fast; compute stays parallel on clones); the
reentrancy guard adds in-process bookkeeping to the mutation path; the span boundaries must
be drawn carefully so no release leaves a dirty tree (the primary implementation risk,
covered by the deterministic contention test).

## Disconfirmation

- **Falsifier:** if the flock-contention integration test still shows `MERGE_HEAD`/
  `cannot lock ref` under barrier-synced contention after widening, the span boundaries are
  drawn wrong (a real-vault git op still sits outside a locked span) and must be re-traced.
- **Steelmanned runner-up:** Option C — if the `72bdc73` retry predicate, tuned wider,
  drove the observed failure rate to effectively zero across thousands of runs, the cheaper
  retry-only fix would avoid touching the lock/transaction layers at all.
- **Reversal trigger:** if the span lock is observed to block the synchronous gate for
  perceptibly long (client timeouts), the bracket is enclosing compute it should not, or a
  finer-grained (per-branch-ref) lock is warranted — revisit the scoping line.

## Prior Decision

Re-affirms **dec-008** (single vault-mutation path via `VaultTransaction`, one writer,
flock-guarded). dec-008 stays `accepted`; nothing about the single-writer path or the
import-boundary fitness test changes. This ADR **realizes** dec-008's "mutating ops are
flock-guarded" clause for the loop's multi-step git spans, which were guarded only at their
final commit — closing the gap between the invariant as written and as implemented.
