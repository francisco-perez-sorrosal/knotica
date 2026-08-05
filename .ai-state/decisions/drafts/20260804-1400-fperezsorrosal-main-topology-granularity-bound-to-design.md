---
id: dec-draft-4b91f4f7
title: Test-topology group granularity is bound to DESIGN.md §3 component granularity
status: proposed
category: architectural
date: 2026-08-04
summary: Topology groups bind to DESIGN.md §3 Built components; coarse rows stay coarse and un-modeled packages stay un-grouped rather than getting synthetic subsystem names.
tags: [testing, topology, architecture-docs, test-segregation]
made_by: agent
agent_type: systems-architect
branch: main
pipeline_tier: standard
affected_files:
  - .ai-state/TEST_TOPOLOGY.md
  - .ai-state/DESIGN.md
re_affirmed_by:
  - dec-draft-c20759d6
dissent: A topology that silently under-covers ~24 modules is arguably worse than one with four honest synthetic subsystem names, because the gap is invisible at the point of use (a scoped run just quietly skips them).
---

## Context

`/refresh-topology --init` scaffolds `.ai-state/TEST_TOPOLOGY.md` from the `Status: Built`
components in `.ai-state/DESIGN.md` §3. The trunk schema requires every `subsystems` entry of a
test group to resolve to such a component, and sentinel TT01 enforces the binding.

Knotica's §3 table has 18 Built rows, but its granularity does not match the codebase in two
directions at once:

- **Too coarse in one place.** `src/knotica/core/` is a single row standing for ~50 modules
  spanning vault semantics, the loop runtime, the compile chain, the gap-fill spine, and the notes
  overlay.
- **Missing entirely in another.** Four top-level packages — `src/knotica/okf/` (11 modules),
  `src/knotica/guillotine/` (9), `src/knotica/service/` (3), `src/knotica/dashboard/` (1) — have
  no §3 row at all, yet carry 11 test files / 128 tests.

Both mismatches surface at exactly the same moment: when the topology tries to name what a group
covers. The topology is the first artifact that reads §3 as a *contract* rather than as prose, so
it is the first to feel the drift.

## Decision

Group granularity is bound to §3 granularity. Concretely:

1. A group's `subsystems` list names only verbatim §3 Built components. No synthetic names.
2. Where §3 is coarse, the group is coarse. `vault-semantics` covers the whole `core/` row and is
   accepted as the largest group; the fix for its size is splitting the §3 row, not the group.
3. Where §3 is silent, no group is created. `okf/`, `guillotine/`, `service/`, and `dashboard/`
   tests fall through to pipeline-tier (full-suite) execution.
4. The gap is recorded *inside* the topology (`## Subsystems` notes 1–3) naming the affected
   packages and test files, so it is visible at the point of use rather than inferred from absence.
5. Group membership under the coarse `core/` row is assigned **by concern, not by directory**: the
   loop-extraction siblings (`arena*.py`, `branch_*.py`, `candidate_gate.py`, `best_effort.py`,
   `loop_*.py`) go to `loop-runtime`, the compile chain to `query-compile`, and so on, with the
   subtractions tabulated explicitly. `vault-semantics` is defined as the residual.

## Considered Options

### Bind to §3, document the gaps (chosen)

- **Pro:** TT01 stays green. The table never claims coverage it does not have. The unblock is a
  single well-understood action (a §3 refinement pass) owned by the agent that already owns §3.
- **Pro:** Keeps `DESIGN.md` the single authority for what a component *is*; the topology stays a
  pure cross-reference and does not fork a second component vocabulary.
- **Con:** 11 test files / 128 tests get no scoped-run benefit until §3 is refined.

### Invent synthetic subsystem names for the un-modeled packages

- **Pro:** Full test coverage under the topology immediately; every test file has an owning group.
- **Con:** Every synthetic name is an unresolvable `subsystems` entry — a TT01 FAIL on the first
  sentinel run, which is a worse failure than the gap it closes.
- **Con:** Forks the component vocabulary. Two files would then disagree about what components
  exist, and the disagreement would be invisible until someone read both.

### Silently widen an adjacent group's `file_dependencies` to cover them

- **Pro:** TT01 passes (the `subsystems` list stays legal) and the tests do run in a scoped step.
- **Con:** The Subsystems table becomes a lie — it says `vault-semantics` covers `core/` while the
  group actually also runs `okf/` and `guillotine/`. That is the failure mode the ownership model
  exists to prevent, and it is undetectable by any check.

### Fix `DESIGN.md` §3 first, then build the topology

- **Pro:** Strictly the correct ordering; produces the best topology.
- **Con:** Out of scope for this pass, and the §3 refinement is not a mechanical edit — deciding
  whether `okf/` is one component or four is a real architectural judgment that deserves its own
  pass rather than being smuggled into a topology scaffold.

## Consequences

**Positive**

- The topology is honest at the point of use: notes 1–3 name the uncovered packages and files.
- TT01 passes on the first sentinel run.
- `DESIGN.md` §3 remains the sole component authority.
- The residual definition of `vault-semantics` (note 2) prevents the most likely downstream error —
  a `src/knotica/core/**` glob that swallows four other groups.

**Negative**

- 11 test files / 128 tests (the five `test_okf_*.py` **plus `test_log_fmt.py`**,
  `test_guillotine.py`, the three `service` tests, `test_http_dashboard.py`) get no scoped-run
  speedup until §3 is refined. The count was first written as "~10" from a `test_okf_*.py`
  filename glob, which silently missed `test_log_fmt.py` — it imports `knotica.okf.log_fmt` but
  carries no `okf` marker in its name. Enumerating an un-modeled package's tests by filename
  pattern under-reports whenever a test is named for the module it covers rather than the package;
  enumerate by import instead. The whole-file un-grouped total is 12 files / 149 tests, the twelfth
  being `test_spine.py` — un-grouped as test infrastructure, not as a §3 gap, so it is outside this
  decision's scope.
- `vault-semantics` will be a large, slow group; steps touching the mutation core see the least
  benefit from the topology, which is unfortunate since that is a frequently-touched area.
- The concern-based subtraction table (note 2) is hand-maintained and will drift if §3 rows change
  without a `/refresh-topology` pass. It has no mechanical check.

## Disconfirmation

**Falsifier.** If a `/refresh-topology` drift pass finds that the un-grouped packages' tests were
in fact being run in scoped steps all along (because the test-engineer widened a glob), the
"documented gap" is fiction and the honest-coverage argument collapses — the decision would then
have bought nothing over option 3 while still costing the speedup.

**Steelmanned runner-up.** Refining `DESIGN.md` §3 first is genuinely the better engineering order.
The four missing packages are not obscure: `okf/` and `guillotine/` together are 20 modules, larger
than several rows §3 *does* carry. Building a topology on a component model known to be incomplete
means the very first `/refresh-topology` pass will re-do work, and the "record the gap in a note"
mitigation depends on a human reading a note — the weakest enforcement mechanism available.

**Reversal trigger.** Revisit when either (a) `DESIGN.md` §3 gains rows for any of the four
un-modeled packages — at which point the topology should immediately gain the corresponding groups;
or (b) measured `vault-semantics` runtime exceeds roughly half the full-suite wall-clock, which
would mean the coarse `core/` row has made the topology's central promise unachievable for the
project's most-edited area.
