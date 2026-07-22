---
id: dec-051
title: loop.py cohesion extraction — three concern-named sibling modules (partial td-008)
status: accepted
category: architectural
date: 2026-07-22
summary: Extract build_loop_runner, the arena race-and-resolve core, and the candidate-gate cluster from loop.py into three concern-named sibling modules; partial resolution of td-008.
tags: [refactoring, loop, module-boundary, cohesion, td-008, code-quality]
made_by: agent
agent_type: systems-architect
branch: worktree-loop-py-extraction
pipeline_tier: standard
affected_files:
  - src/knotica/core/loop.py
  - src/knotica/core/arena_resolve.py
  - src/knotica/core/loop_factory.py
  - src/knotica/core/candidate_gate.py
re_affirms: dec-048
dissent: A collaborator-class shape for the candidate-gate cluster would give LoopRunner a cleaner composition seam than free-functions-reaching-into-runner-internals, at the cost of diverging from the established source_gate sibling pattern.
---

## Context

`src/knotica/core/loop.py` has grown to 1347 lines (hard ceiling 800), across
924 → 1047 → 1221 → 1347 over three passes (P1, P-A consolidation,
eval-cadence-model-config). td-008 tracks the ceiling breach. The P-A pass's
"drops-below-ceiling-incidentally" premise failed: `_run_arena_and_resolve` and
`build_loop_runner` *landed in* during P-A (consolidating duplication) rather
than shrinking the file. dec-048 deliberately deferred any extraction to keep
the eval-cadence-model-config pipeline feature-scoped.

The user scoped this pass to a **low-risk** subset: extract the two
ledger-named pieces plus the candidate-gate cluster (`poll_once` family), while
leaving the adversarially-reviewed `observe_default` cluster (billed-trigger
nonce, td-011 fix, cadence guards) untouched. This is a behavior-preserving
`[Phase: Refactoring]` sub-pipeline; characterization tests come first.

## Decision

Extract three clusters into three **concern-named sibling modules** under
`src/knotica/core/` (mirroring the P-A precedent `branch_namespaces.py` /
`best_effort.py`):

1. **`arena_resolve.py`** — `run_arena_and_resolve(...)` as a free function
   taking the six former `self.*` reads as explicit keyword params. Zero runtime
   dependency on `loop` (`LoopCycleResult` appears only in a `TYPE_CHECKING`
   annotation; the function returns `on_win(arena)`/`on_lose(arena)` and never
   constructs one). `_heal_prompts_after_regression` and `_race_then_resolve`
   stay on `LoopRunner` and call the free function.

2. **`loop_factory.py`** — `build_loop_runner(...)` moved verbatim (identical
   signature, body, defaults). `loop.py` re-exports it via a bottom-of-file
   import so every existing `from knotica.core.loop import build_loop_runner`
   call site (CLI, service, 5× tools_vault, tools_source_ingest, tests) resolves
   unchanged.

3. **`candidate_gate.py`** — `poll_once`, `next_candidate`, `process_candidate`,
   `keep`, `discard` as free functions taking the `LoopRunner` as their first
   parameter — the **same shape `source_gate.py` already uses** (it too takes a
   runner and calls `runner._keep`, `runner._mutation_span`, etc.).
   `LoopRunner.poll_once` (public) and `LoopRunner._keep` (called by
   `source_gate`) remain as thin delegator methods; the delegators lazy-import
   `candidate_gate` inside their bodies (the existing `source_gate` lazy-import
   idiom) to avoid a module-load cycle.

Three modules, not a combined "loop_construction" module: construction, the
arena algorithm, and the gate state machine change for different reasons and
share no knowledge (Balanced Coupling). This is a **partial** resolution of
td-008 — the file lands at ~1087 lines, still ~287 over the ceiling; td-008
stays `in-flight`.

## Considered Options

### Option A — Candidate-gate as free functions on `runner` (chosen)
Mirrors `source_gate.py`, the codebase's existing answer to "gate logic outside
`loop.py` that operates on a runner." Verbatim `self.`→`runner.` body moves.
`candidate_gate` + `source_gate` become a matched pair.
- Pro: consistent with an established sibling; smallest behavioral risk; the
  largest cluster leaves `loop.py`.
- Con: functions reach into `runner._private` across a module boundary (already
  accepted for `source_gate`; single-underscore, no mangling).

### Option B — Candidate-gate as a `CandidateGate` collaborator class
A class composed by `LoopRunner`, à la `best_effort.BestEffortAttempt`.
- Pro: cleaner composition seam; no cross-module private access.
- Con: still needs a runner back-reference for `_race_then_resolve`,
  `_mutation_span`, and the `source_gate(runner,…)` hand-off — a wrapper with no
  behavioral benefit; diverges from the `source_gate` twin. Recorded as `dissent`.

### Option C — Keep the cluster on `LoopRunner`, extract only pure helpers
- Con: the cluster is an all-`self` state machine with no pure helpers to peel
  off. Yields ~zero line reduction. Rejected.

### Option D — Combine factory + arena-resolve into one module
- Con: co-locates a build-time concern with a runtime algorithm; low cohesion.
  `arena_resolve` is also shared by the out-of-scope heal path, so it must stand
  alone regardless. Rejected.

## Consequences

**Positive:**
- ~260 lines leave `loop.py`; the two largest movable clusters and the shared
  arena core are now single-concern modules.
- `candidate_gate` ↔ `source_gate` symmetry makes future gate work obvious.
- Every extraction is behind unchanged black-box characterization tests; suite
  green between steps.

**Negative / accepted:**
- `loop_factory` requires a bottom-of-file re-export import in `loop.py` — a mild
  structural wart, sound only because `loop.py` is the sole entry point for
  `build_loop_runner` importers. A future pass repointing call sites to
  `loop_factory` directly removes it.
- td-008 is **not** closed; the file stays ~287 over ceiling. The deferred
  `observe_default` (397 lines) + gap-classification (147 lines) extraction is
  the named follow-up and is higher-risk (adversarially-reviewed dec-048 code).

**Re-affirms dec-048:** dec-048 deferred this extraction to protect the
billed-trigger review scope. This pass honors that boundary — it extracts only
the *non*-`observe_default` clusters and explicitly leaves the reviewed code in
place, deferring its extraction as a distinct follow-up rather than superseding
dec-048's deferral.

## Disconfirmation

- **Falsifier:** if extracting the candidate-gate cluster as `runner`-first free
  functions forces `source_gate` or the loop-state contract to change, or if any
  characterization test needs modification to stay green, the behavior-preserving
  premise is wrong and the shape (or the whole pass) should be reconsidered.
- **Steelmanned runner-up (Option B):** a `CandidateGate` collaborator class
  would give `LoopRunner` a genuine composition boundary instead of a sibling
  that reaches into its privates; if `source_gate` were being written today, a
  shared `RunnerGate` base with a typed runner Protocol might be the better
  design, and adopting it now would set that direction rather than entrench the
  free-function-on-privates pattern.
- **Reversal trigger:** when the `observe_default` follow-up runs, or when a
  third gate-like sibling appears, revisit whether the free-function-on-runner
  pattern should be promoted to a typed `SupportsGate` Protocol (removing the
  cross-module private access) and whether `loop_factory`'s bottom-import should
  be retired by repointing call sites.
