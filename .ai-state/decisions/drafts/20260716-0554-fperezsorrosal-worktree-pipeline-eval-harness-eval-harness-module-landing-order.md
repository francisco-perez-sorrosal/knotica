---
id: dec-draft-75eb597e
title: Eval harness module landing order corrects SYSTEMS_PLAN Group B/C/D hints to match the import dependency graph
status: proposed
category: implementation
date: 2026-07-16
summary: Step decomposition reorders scorer.py/cache.py/judge.py and splits golden.py into two landing steps so every module's import-time dependencies already exist when it lands.
tags: [eval-harness, step-sequencing, module-structure, dspy]
made_by: agent
agent_type: implementation-planner
branch: worktree-pipeline-eval-harness
pipeline_tier: standard
affected_files:
  - src/knotica/evals/scorer.py
  - src/knotica/evals/cache.py
  - src/knotica/evals/judge.py
  - src/knotica/evals/golden.py
  - src/knotica/evals/harness.py
affected_reqs:
  - REQ-SCALAR-01
  - REQ-SCALAR-03
  - REQ-JUDGE-03
  - REQ-GOLDEN-04
  - REQ-RUN-04
supersedes: null
re_affirms: null
---

## Context

`SYSTEMS_PLAN.md § Phase / step grouping hints` groups the new `evals/` modules into
five prose buckets (A foundations, B pure scalar core, C LLM access, D dspy adapter +
orchestration, E CLI + golden tooling) with an *internal* ordering inside Group C
("`evals/llm.py` → `evals/runner.py` → `evals/judge.py` → `evals/cache.py`"). Cross-checking
these hints against the architecture's own `## Components` dependency column
(`SYSTEMS_PLAN.md` lines 103-116) during Phase 3 decomposition surfaced three places
where the prose grouping, read literally as a landing order, would make a module's
first commit fail to import:

1. **`evals/scorer.py`** is listed in Group B ("pure scalar core... no LLM") but its
   `Depends on` column reads `records, judge, evals/citations` — it imports
   `evals.judge`, a Group C module. Landing it in Group B would mean importing a
   module that does not exist yet.
2. **`evals/judge.py`**'s `Depends on` column reads `LLMClient, evals/cache` — but
   Group C's internal prose order lists `judge.py` *before* `cache.py`. Landing judge.py
   first would mean importing `evals.cache` before it exists.
3. **`evals/harness.py`**'s data-flow trace (`SYSTEMS_PLAN.md` lines 130-153) calls
   `golden.to_example(qa) for qa in golden.load(clone, t)` — but `golden.py`'s
   *bootstrap* workflow (synthetic generation + human review-freeze, REQ-GOLDEN-02/03)
   is Group E work, scheduled well after Group D's `harness.py`. `golden.py`'s
   deterministic `load`/`to_example` functions (REQ-GOLDEN-04 read path) are needed by
   `harness.py` at import time regardless of whether the interactive bootstrap workflow
   has landed yet.

## Decision

The implementation plan's step order follows the **import dependency graph** (derived
from the architecture's own `Depends on` column and data-flow trace), using the prose
Group A-E labels only as a narrative backdrop, not a literal landing sequence:

- `evals/cache.py` lands before `evals/judge.py` (both still inside what the plan calls
  "Group C").
- `evals/scorer.py` lands *after* `evals/judge.py`, as the bridge step between the plan's
  Group C and Group D — not inside Group B alongside `citations.py`/`scalar.py`, which
  remain genuinely LLM-free and independent of each other.
- `evals/golden.py` is split into two landing steps touching the same file
  sequentially: a **devset-builder step** (`load`, `MANIFEST.json` verify, `to_example`
  — deterministic, fixture-testable, REQ-GOLDEN-04) lands in Group D immediately before
  `harness.py`; a **bootstrap step** (synthetic-from-pages generation, review staging,
  freeze — REQ-GOLDEN-02/03) lands later in Group E alongside `cli/eval.py`, matching
  the architecture's original placement for the interactive/LLM-generation half of the
  module.

No architectural interface changes. `scorer.py`'s public signature
(`score(gold, prediction, trace=None) -> float | bool`) and `golden.py`'s public surface
are unchanged from `SYSTEMS_PLAN.md § Interfaces` — only the *step landing order* moves.

## Considered Options

### A. Follow the prose Group B/C/D/E order literally

Land `scorer.py` in Group B and `judge.py` before `cache.py`, exactly as the hint
prose lists them.

- Pro: matches the architect's hint text verbatim, zero planner judgment call.
- Con: produces a step whose first commit fails `import evals.judge` /
  `import evals.cache` — violates the known-good-increment principle (every step must
  leave the system in a working, importable state). Not viable as written.

### B. Reorder per the dependency graph, keep Group labels as narrative only (chosen)

Reorder `cache.py`/`judge.py`/`scorer.py` and split `golden.py`, using the `Depends on`
column and data-flow trace as ground truth over the prose grouping hint.

- Pro: every step's production code imports cleanly at commit time; no step ever
  references an unbuilt module; preserves the architecture's actual interfaces
  unchanged.
- Con: the plan's step numbers no longer map 1:1 to the SYSTEMS_PLAN's Group A-E prose
  buckets, requiring this ADR + a LEARNINGS.md note so a future reader isn't confused
  by the mismatch.

### C. Escalate back to systems-architect for a corrected grouping hint

Flag the Group hint inconsistency and wait for a revised `SYSTEMS_PLAN.md`.

- Pro: keeps the plan and the architecture doc in lockstep.
- Con: the grouping hints are explicitly non-binding decomposition *hints* for the
  planner ("Phase / step grouping hints for the implementation-planner"), not frozen
  architecture; the actual binding contract is the `Depends on` column and the
  Interfaces section, both of which this ordering respects unchanged. Round-tripping
  to the architect for a hint-only correction is disproportionate process for a
  same-conclusion reordering within the planner's own remit (decompose steps, don't
  redesign).

## Consequences

- **Positive**: every implementation step is independently importable and testable in
  isolation, matching the known-good-increment discipline; the dependency graph is now
  the single source of truth for landing order (traceable to `SYSTEMS_PLAN.md`'s own
  `Depends on` column, not a second, conflicting ordering).
- **Positive**: `golden.py`'s split mirrors a natural incremental-development seam
  (consume path before produce path) rather than being an arbitrary planner cut.
  Both landing steps touch the same file sequentially (never concurrently), so no
  parallel-group file-disjointness rule is at risk.
- **Negative**: a reader comparing `IMPLEMENTATION_PLAN.md`'s step numbers against
  `SYSTEMS_PLAN.md`'s Group A-E prose will find the two orderings diverge in these three
  places; this ADR plus the `IMPLEMENTATION_PLAN.md` intro note are the record of why.
- **Negative**: none of `scorer.py`, `judge.py`, `cache.py` gets a fully "parallel, no
  LLM" implementation slot the way the architecture's Group B framing implied for
  `scorer.py` specifically — `scorer.py` is sequential-after-`judge.py`. This is a
  strictly more conservative (safer) schedule, not a capability loss.
