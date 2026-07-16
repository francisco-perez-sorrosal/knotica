---
id: dec-draft-ee0f5832
title: Frozen-corpus mechanism — VaultVcs.clone_to plus a determinism kit
status: proposed
category: architectural
date: 2026-07-15
summary: Add a clone_to(dest, ref=None) -> VaultVcs method to core/vcs.py so the eval harness runs against a fresh git clone pinned at a SHA (corpus_ref = git:<sha>), never the live vault. Keep all git surface in one module; the clone root is the same shape as the live vault so existing store/search/transaction primitives work unchanged. Pin SHA + MANIFEST + temperature 0 + seeded RNG for a reproducible harness.
tags: [evals, phase-2, frozen-corpus, git, clone, vcs, determinism, immutable-harness]
made_by: agent
agent_type: systems-architect
branch: worktree-pipeline-eval-harness
pipeline_tier: standard
affected_files: [src/knotica/core/vcs.py, src/knotica/evals/harness.py]
affected_reqs: [REQ-CORPUS-01, REQ-CORPUS-02, REQ-CORPUS-03]
dissent: A git worktree (git worktree add) rooted at a SHA is cheaper than a full clone (shares the object store, no network/copy cost) and would pin the corpus just as well; a full clone was chosen for isolation simplicity and Phase-4 relocation symmetry, accepting the extra copy cost of a local-only MVP.
---

## Context

The "immutable harness" half of the autoresearch triad literally has no infrastructure yet: research found **zero** clone/checkout capability anywhere in the tree (`grep -rn "clone" src/knotica/` → nil; `core/vcs.py` is deliberately incapable of clone — only read methods `head_sha`/`current_branch`/`unpushed_count`/`is_dirty` and the transaction-only mutators `commit_paths`/`rollback_paths`). The frozen corpus is the single largest net-new surface. The `MetricsRecord.corpus_ref` field already expects `git:<sha>`. Loops-always-on-a-clone is a locked invariant (PRE_PLAN): the live vault is never a loop's working tree; a run clones to a temp dir, works there, and returns a branch — this also makes the Phase-4 remote lift a pure relocation.

## Decision

Add `VaultVcs.clone_to(dest_root: Path, ref: str | None = None) -> VaultVcs` to `core/vcs.py` (all git subprocess stays in one module). It performs `git clone <source> <dest>` and, if `ref` is given, checks it out; it returns a `VaultVcs` bound to the clone. Because `VaultVcs`'s constructor already accepts an arbitrary git-work-tree root, the clone root is the *same shape* as the live vault — `LocalFSStore(clone)`, `RipgrepBackend(clone)`, and `VaultTransaction(clone_store, clone, "eval", …)` all work unchanged against it. The eval flow: config-resolve the **source** vault → `clone_to(tmp)` at HEAD (or `--ref`) → set `corpus_ref = "git:" + clone.head_sha()` → run the evaluator against the clone → append metrics via `VaultTransaction` (one `eval` commit on the clone) → leave the source byte-identical.

`clone_to` is a **read/checkout** method, deliberately **not** added to `MUTATING_VCS_METHODS` (it does not mutate the live vault — it creates a fresh tree elsewhere), so `evals/` may call it under the extended fitness test (`dec-draft-a6f575c0`).

**Determinism kit** for the "stable scalar on a frozen corpus" success criterion: pin the SHA (corpus_ref), seed all RNG, `temperature=0` on every model call, and record `deterministic: true` plus the golden-set `MANIFEST` (`sha256`, `version`, `source`, `split`) in the per-run manifest so a run is fully reconstructable from `{corpus_ref, dataset_sha, harness_version}`.

## Considered Options

### Option A — `VaultVcs.clone_to` full clone + determinism kit (chosen)
- **Pros:** all git surface stays in one module (matches the existing design); the clone is fully isolated from the source (no shared object store to corrupt); the clone root is drop-in compatible with every existing vault primitive; Phase-4 remote symmetry (a remote run clones the same way); `corpus_ref` format already anticipated.
- **Cons:** a full local clone copies the object store (cost grows with vault history) — negligible for an MVP-scale vault, more later.

### Option B — `git worktree add` at a SHA
- **Pros:** cheaper (shares the source object store, no copy); pins the corpus equally well.
- **Cons:** the worktree shares `.git` with the live vault — a subtle coupling that risks the loops-on-a-clone isolation guarantee (a bad eval op could touch shared git state); complicates the Phase-4 remote model (worktrees don't relocate cleanly); the isolation story is harder to reason about.

### Option C — a new `core/clone.py` module
- **Pros:** keeps `core/vcs.py` focused on live-vault read/commit; clone is arguably a different concern.
- **Cons:** splits git-subprocess across two modules, weakening the "all git in one place" property the fitness test relies on (`test_core_transaction_is_the_only_caller_of_mutating_vcs_methods` reasons over the git surface); one more module for a single method.

## Consequences

- **Positive:** the immutable-harness infrastructure exists as a small, well-placed addition; `corpus_ref` is populated correctly; every downstream primitive works against the clone with zero changes; the determinism kit makes the scalar reproducible; the Phase-4 lift is a relocation, not a redesign.
- **Negative:** full-clone copy cost scales with vault history (fine now, revisit at scale); `core/vcs.py` grows a method that shells `git clone` (still within its remit — it owns git).

## Disconfirmation

- **Falsifier:** if full-clone copy cost becomes a real drag on eval-run latency at vault scale (many topics × large history), the full-clone choice was too heavy and a worktree/shallow-clone would have been the right pin.
- **Steelmanned runner-up (Option B):** `git worktree add <dir> <sha>` pins the corpus with no object-store copy, is near-instant regardless of history size, and — with care — the shared `.git` never sees an eval mutation because eval commits happen on the worktree's own branch; for a fast inner loop that re-evaluates many generations, the worktree's speed could dominate the isolation nicety of a full clone.
- **Reversal trigger:** switch to a worktree or shallow/partial clone if (a) clone copy cost measurably slows eval runs, or (b) the Phase-4 remote model turns out to prefer worktrees, or (c) disk pressure from many per-run clones becomes an operational issue.
