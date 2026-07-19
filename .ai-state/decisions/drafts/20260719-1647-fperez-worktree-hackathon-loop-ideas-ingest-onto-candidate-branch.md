---
id: dec-draft-0a5dd23b
title: Client-driven source ingest lands on a loop/c/* candidate branch via a server-managed git worktree keyed by suggestion_id
status: proposed
category: architectural
date: 2026-07-19
summary: The interactive, client-as-brain, multi-commit ingest of an approved gap-fill source lands on an isolated candidate branch through a server-managed git worktree keyed by suggestion_id (resolved per call from an explicit id / opaque handle — no server session state), building on a private loop/wip/* branch that source_ingest_submit publishes atomically to loop/c/<topic>/source-<id8>; the default working tree and default-branch ref are never touched during ingest, statelessness (dec-004) and one-commit-per-mutation (dec-008) both hold, and the branch is natively gate-eligible with no push/fetch.
tags: [gapfill, phase-p4, source-gate, ingest, worktree, stateless-server, client-as-brain, dec-004, dec-008, dec-014, one-way-door]
made_by: agent
agent_type: systems-architect
branch: worktree-hackathon-loop-ideas
pipeline_tier: standard
affected_files:
  - src/knotica/core/vcs.py
  - src/knotica/core/transaction.py
  - src/knotica/core/source_ingest.py
  - src/knotica/mcp_server/tools_write.py
affected_reqs: [REQ-01, REQ-02, REQ-03]
re_affirms: dec-004
dissent: A git worktree adds a lifecycle to manage (orphan worktrees, WIP branches, a worktree-aware transaction seam); a single fat suggestion_ingest transaction carrying the whole page bundle in one call would need no worktree, no readiness boundary, and no new transaction target — at the cost of the iterative client-as-brain store_source→pages→wikilink→index flow that read_protocol and the MCP server contract mandate.
---

## Context

P4 closes the gap-fill loop: an approved suggestion is ingested by the interactive client and
the shipped clone→eval→gate spine decides whether the source earns a merge. The ingest is
**client-as-brain** (dec-014: the MCP server exposes deterministic tools only; the client's
LLM writes the grounded prose), **multi-step** (store_source → entity pages → wikilinks →
index — the MCP server's own instructions forbid stopping after store_source), and **spread
across many turns** with arbitrary human-paced gaps. Today every mutating MCP tool commits to
the **default branch** of the live vault through `VaultTransaction` (flock, one commit). P4
must land the same writes on a `loop/c/*` **source candidate** branch **without disturbing
the default-branch working tree** the watcher (`observe_default` reads `head_sha()`) and every
other stateless MCP session depend on.

Hard invariants in tension: statelessness — no session state between calls, git + config are
the only state (dec-004); one commit per mutating op (dec-008); observe-safety — candidate
writes must not re-trigger `observe_default`; and the gate must never process a half-built
candidate. The interface-designer shadow (dec-draft-9a95faae) independently required **per-call
scoping** — rejecting any session-long flock or persistent switched checkout as de-facto
session state that would starve the watcher for the minutes-to-turns a long ingest takes. This
is a **one-way-door**: the branch-landing mechanism is the load-bearing contract every P4
behavior sits on.

## Decision

Land the ingest on the candidate branch through a **server-managed git worktree keyed by
`suggestion_id`**, resolved **per call** from an explicit id (surfaced to the client as the
opaque `candidate` handle of dec-draft-9a95faae):

1. `source_ingest_open(suggestion_id)` (refuses a non-approved suggestion with a typed
   `SUGGESTION_NOT_APPROVED`) creates a worktree at a deterministic server-managed path
   checked out on a **private** `loop/wip/<topic>/source-<id8>` branch off default HEAD, and
   returns the handle.
2. Each mutating ingest tool carries the handle; the write executes as its **own**
   `VaultTransaction` (dec-008) into the worktree working directory, committing on the WIP
   branch, while the flock is acquired **per call** at the **canonical** vault root (serializes
   with default-branch writers; the client's cognitive work happens *between* calls, never
   under the lock). The empty-handle path of every existing tool stays byte-identical.
3. `source_ingest_submit(candidate)` is the **readiness boundary**: it publishes the WIP branch
   as `loop/c/<topic>/source-<id8>` in one ref op and prunes the worktree. Only now is the
   candidate visible to `_next_candidate` (which scans the `loop/c/` prefix), so the gate never
   sees a partial branch — deterministic readiness, no heartbeat race.
4. Statelessness holds because the target is a pure function of `suggestion_id` plus the
   worktree's on-disk registration in `.git/worktrees/` (recoverable via `git worktree list`) —
   no server memory of "which branch is open." The branch lives in the shared repo, so it is
   natively gate-eligible with no push/fetch and no clone duplication.

## Considered Options

### Option A — server-managed git worktree keyed by suggestion_id (chosen)
- Pro: default working tree and default ref provably untouched; per-call flock (no starvation);
  one commit per mutation; branch natively visible to the loop; deterministic readiness via
  WIP-publish; reuses the project's "loops work on a branch, return as branch" physics.
- Con: a worktree lifecycle to manage (orphan prune, abandon path) and a worktree-aware
  transaction target (write to worktree dir, lock canonical root).

### Option B — branch-switch-under-flock for the whole ingest session
- Pro: no worktree; reuses the loop's own `checkout_branch`.
- Con: mutates the **shared** working tree; leaving HEAD on the candidate between calls breaks
  every stateless reader and the watcher's `head_sha()` model, and holding the lock across the
  session starves all sessions. Directly violates dec-004 and the interface-designer's AC-1.
  Rejected.

### Option C — branch-context arg committed via git plumbing (commit-tree/update-ref), no working dir
- Pro: no separate working directory.
- Con: the client authors *files*; there is no way to stage client-written file content onto a
  branch without a working directory. Rejected.

### Option D — single fat suggestion_ingest transaction (whole page bundle in one call)
- Pro: no worktree, no readiness boundary, one call, trivially stateless.
- Con: fights the iterative read_protocol flow (wikilinks need pages that exist; the client
  writes prose turn by turn) and forces whole-corpus assembly into one giant tool call.
  Rejected as an interface/UX regression against client-as-brain.

### Option E — full clone keyed by suggestion_id
- Pro: strongest isolation.
- Con: duplicates the whole vault per ingest (heavy) and needs an explicit publish (push/fetch)
  to make the branch visible; a worktree shares objects and publishes natively. Rejected on
  cost.

## Consequences

**Positive:** the ingest is isolated by construction; statelessness, one-commit-per-mutation,
and observe-safety all hold without new session state; the candidate is a normal `loop/c/*`
tip the existing gate consumes verbatim; readiness is deterministic (no heartbeat). Aligns
with the interface-designer's per-call-scoping requirement with zero surface change.

**Negative / costs:** a worktree/WIP-branch lifecycle (create/publish/abandon/orphan-prune)
and a worktree-aware `VaultTransaction` target are new surface. A crashed ingest can leave an
orphan worktree + WIP branch — mitigated by a staleness-bounded prune mirroring
`_prune_result_branches` and an explicit abandon path; both are recoverable git state.

## Disconfirmation

- **Falsifier:** if worktree writes cannot preserve `VaultTransaction`'s single-writer /
  path-scoped-rollback invariants (e.g. the canonical-root flock does not actually serialize a
  worktree commit against a default-branch commit, or a rollback in the worktree touches the
  default tree), the isolation claim is false and the mechanism is wrong. A byte-stability
  integration test over a full ingest is the load-bearing check.
- **Steelmanned runner-up (Option D, fat transaction):** if the client can be trusted to
  assemble a complete, wikilinked page bundle in one shot, a single `suggestion_ingest`
  transaction needs no worktree, no WIP branch, no readiness boundary, and no worktree-aware
  transaction seam — the smallest possible P4 mechanism, trivially stateless, one commit. It
  loses only the iterative, turn-by-turn authoring the MCP contract mandates; a future
  code-execution-mode client that composes the whole ingest programmatically would make it the
  better choice.
- **Reversal trigger:** if worktree lifecycle bugs (orphans, lock contention, rollback
  leakage) prove costly in practice, or a code-execution ingest client lands, revisit the fat
  single-transaction path (Option D). If the interactive client ever runs same-process with the
  watcher, re-examine the isolation requirement entirely.

## Prior Decision

Re-affirms **dec-004** (stateless server): the worktree target is derived per call from
`suggestion_id` + on-disk git worktree registration, holding no server session state — the
same discipline dec-004 applies to topic/vault resolution, extended to ingest-branch context.
Depends on **dec-008** (one commit per mutation, preserved: each ingest write is its own
transaction) and **dec-014** (server-side LLM boundary, untouched: the ingest path is
deterministic; the client's LLM does all cognitive work). Cross-references the
interface-designer's **dec-draft-9a95faae** (the opaque `candidate` handle + open/submit tools
this mechanism sits behind).
