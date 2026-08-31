---
id: dec-122
title: The observe leg leaves loop.py — fourth extraction pass, sibling modules not a package
status: accepted
category: architectural
date: 2026-08-31
summary: observe_default and the regression→gap redirect move to core/loop_observe.py and core/loop_gap_redirect.py, taking loop.py from 1189 to 681 and retiring its ratchet exemption.
tags: [loop, refactoring, file-size, tech-debt, core]
made_by: agent
agent_type: implementer
branch: main
pipeline_tier: standard
dissent: A package (core/loop/) would have expressed the cluster's real shape better than a fourth flat sibling, and this pass entrenches a naming convention (loop_*) that now spans eleven modules with no index.
affected_files:
  - src/knotica/core/loop.py
  - src/knotica/core/loop_observe.py
  - src/knotica/core/loop_gap_redirect.py
  - tests/test_loop_observe_characterization.py
  - tests/test_file_size_ratchet.py
---

## Context

`core/loop.py` has carried a file-size ratchet exemption since the ceiling was introduced —
the last measurement put it at 1189 lines against an 800-line hard ceiling, 389 over. Three
prior extraction passes had already taken the cheap parts out: `build_loop_runner` to
`loop_factory.py`, `_run_arena_and_resolve` to `arena_resolve.py`, and the candidate-gate
cluster to `candidate_gate.py` (each characterization-tests-first). What remained was the
module's centre of mass: `observe_default`, the longest method in the project, plus the
guards and the post-regression heal that only it reaches, plus the gap-classification
sub-cluster that only the heal path reaches.

The tech-debt row deferred the fourth pass on one condition — "once that code has proven
stable in production". v0.3.0 has shipped and the daemon runs this path unattended, so the
condition is met. Re-measurement also corrected the row's own estimate in both directions:
`observe_default` is 256 lines (not 264), and the gap cluster is 147 (not ~93 — the row had
counted two of its three methods).

## Decision

Extract two cohesive sibling modules under `core/`:

- **`core/loop_observe.py`** — `observe_default` and the three helper clusters only it
  reaches: the pacing guards (`observation_hold`, `cadence_hold`, `_within_window`) and the
  post-regression arena heal (`heal_prompts_after_regression`).
- **`core/loop_gap_redirect.py`** — the regression → knowledge-gap redirect:
  `maybe_redirect_to_gaps`, `classify_and_persist_gaps`, `maybe_discover_for_gaps`.

Both are free functions taking the driving `LoopRunner` as an explicit first parameter — the
shape the three prior passes and `source_gate`/`loop_holds` already established. `loop.py`
lands at 681 lines, so its ratchet baseline is **deleted with no successor** (the ratchet's
rule 3), the third exemption paid off by splitting rather than by shrinking in place.

Two seams are deliberately preserved rather than simplified:

- `observe_default` and `_cadence_hold` remain thin `LoopRunner` methods. The first is the
  public API and the billing boundary every caller patches; the second is consulted through
  the *method* by the extracted observe leg, because the candidate-gate path's
  never-consults-cadence contract is proven by a spy on that attribute.
- the observation debounce (`_pending_head` / `_pending_since`) stays runner state, mutated
  through `runner`. The CLI watcher keeps one runner alive across ticks precisely so a burst
  of commits coalesces into one eval; a per-call debounce settles forever.

## Considered Options

### A fourth flat sibling pair (chosen)

- **Pro**: matches three established precedents exactly; every importer of `LoopRunner` is
  untouched; the diff is a move, reviewable as one.
- **Pro**: the two clusters have genuinely different reasons to change — scheduling/pacing
  versus regression diagnosis — so they are two modules, not one.
- **Con**: `core/` now holds eleven `loop_*` modules with no index; the flat namespace is
  reaching the point where a reader cannot see the cluster's shape from the directory.

### Convert to a `core/loop/` package

- **Pro**: expresses the cluster's real shape; a re-exporting `__init__` would keep the ~40
  importers untouched, exactly as `core/records/` and `core/gapfill/` did.
- **Con**: `loop.py` has a genuine import cycle with `loop_factory` resolved by a
  bottom-of-file import — a package conversion has to redesign that, which is not a
  behavior-preserving move. Out of scope for a debt-paydown pass; recorded as the natural
  next step if the sibling count keeps growing.

### Accept the exemption and close the row

- **Con**: the ratchet bounds growth, it does not pay debt. The deferral's stated condition
  had been met, so continuing to defer would have been re-deferral without new evidence.

## Consequences

**Positive.** `loop.py` is under the ceiling for the first time and holds only the runner's
construction, baseline vocabulary, candidate-gate delegators and the harness adapter. The
observe leg and the gap redirect each read top-down in a file that is about one thing. One
of the two remaining `src/` exemptions is retired.

**Negative.** Reading the observe path now costs one hop through a delegator, and two of the
extracted functions mutate runner state (`_pending_head`) from outside the class — a wart
this pass preserves deliberately rather than redesigning under a behavior-preserving
mandate. The `loop_*` flat namespace grows by two.

## Disconfirmation

**Falsifier.** If a subsequent change has to reach across the new boundary in both
directions — `loop_observe` needing more `LoopRunner` internals, or `LoopRunner` needing
`loop_observe` state — the split was made along the wrong seam and the package conversion
was the right call from the start.

**Steelmanned runner-up.** The package conversion is genuinely better-shaped, and
`core/records/` proved the re-exporting `__init__` makes such a move importer-invisible.
The only thing standing against it is the `loop_factory` import cycle, and that cycle is
itself a recorded wart — a conversion that dissolved it would pay two debts at once. This
pass chose the smaller move because the mandate was behavior preservation, not because the
package is wrong.

**Reversal trigger.** A twelfth `loop_*` sibling, or any change that forces the
bottom-of-file `loop_factory` import to move: at that point convert `core/loop/` to a
package and fold the eleven siblings in behind a re-exporting `__init__`.
