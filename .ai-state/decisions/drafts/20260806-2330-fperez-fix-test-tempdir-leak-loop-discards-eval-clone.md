---
id: dec-draft-fcae60b7
title: The loop discards its eval clone; standalone eval keeps it
status: proposed
category: behavioral
date: 2026-08-06
summary: Loop cycles delete the throwaway eval clone when the cycle exits, while standalone run_eval callers retain it, because clone_root is a documented human-review contract only on that path.
tags:
  - loop
  - eval
  - resource-cleanup
  - unattended-behaviour
  - retention
  - temp-dir
made_by: agent
agent_type: orchestrator
branch: fix-test-tempdir-leak
pipeline_tier: direct
affected_files:
  - src/knotica/core/vcs.py
  - src/knotica/core/loop.py
  - src/knotica/core/candidate_gate.py
  - tests/test_file_size_ratchet.py
---

## Context

`evals.harness.run_eval` clones the vault into `tempfile.mkdtemp(prefix="knotica-eval-")` and never removes it. 25,663 stranded knotica temp directories had accumulated since 21 July, 8,027 of them eval clones. Its sibling `core.compile_run` already deletes its own clone in a `finally` (`compile_run.py:349-351`), so the eval path was the outlier, not the norm.

The obvious reading — "a missing `finally: rmtree` in the harness" — is wrong. `EvalRunResult.clone_root` is documented as surfacing the clone so a caller can resolve the manifest at `clone_root / record.artifact_ref` *and point a human at the clone to review the eval commit*. Deleting inside the harness would break that contract for every caller.

The two callers want opposite things:

- The **loop** (unattended, the bulk producer) consumes the clone during the cycle — `fetch_ref_from(clone_root, "HEAD", result_branch)` at `loop.py:337` and `candidate_gate.py:162`, plus regression-manifest reads at `loop.py:686`, `source_gate.py:350`, `gap_classifier.py:196`. Once the cycle returns, the git state that mattered is already in the vault as a result branch, so the clone is redundant.
- **Standalone `knotica eval`** is human-paced, and the clone is the only place its manifest lives.

## Decision

Retention is decided by the **caller**, not the harness. `core.vcs.discarded_clone` — a context manager wrapping a whole loop cycle — deletes the clone directory on exit, applied at both eval sites (`LoopRunner.observe_default` and `candidate_gate.process_candidate`). `run_eval` itself is unchanged, so standalone callers still get a surviving clone and the documented contract holds.

A context manager rather than a delete after the last read: a cycle has several exits (regression redirect, arena heal, pass, fail) and every one must release.

It lives in `core/vcs.py`, beside `VaultVcs.clone_to`, as that method's disposal counterpart — and takes the clone `Path` rather than the `EvalOutcome` carrying it, so `vcs` gains no dependency on the loop's protocol. Placing it there rather than in `loop.py` was forced by the file-size ratchet and is the better home regardless.

## Considered Options

### Delete in the loop, retain standalone (chosen)

- Targets the unattended producer, which is where accumulation actually comes from.
- Makes the eval path consistent with `compile_run`'s existing `finally`.
- Costs a ~130-line re-indent in `observe_default` — noisy in review, but `try`/`finally` is the honest construct for a multi-exit cycle.

### Relocate clones to a state dir with keep-last-N

- Would make retention observable (`<topic>/<ts>-<sha>` instead of random `mkdtemp` names) and bound the standalone path too.
- Rejected as premature: a state dir, a naming scheme and a pruner are real complexity, and the combination of this decision plus the test-fixture fix removes nearly all of the observed volume. Revisit only if standalone clones are measured accumulating.

### TTL sweep of `knotica-eval-*` at run start

- Rejected: strictly worse than the state dir — keeps opaque random names, and reaps by wall-clock rather than by relevance.

### A `knotica eval gc` subcommand

- Rejected as the sole mechanism: the loop daemon runs unattended, so a manual command is exactly the thing that never runs. Still viable later as a companion to the state-dir option.

## Consequences

**Positive.** Unattended loop cycles no longer strand a directory each. The eval and compile paths now dispose of clones the same way. The documented `clone_root` review contract is untouched for standalone callers.

**Negative.** A failed *unattended* eval no longer leaves a clone to inspect post-hoc; diagnosis relies on the result branch merged into the vault and on loop state. A crash *inside* `run_eval` still strands its clone — the outcome never returns, so no caller can release it. That residue is unaddressed here.

`core/loop.py`'s ratchet baseline rises 1133 → 1136: one line for the guard, and two because re-indenting the cycle body pushed a `message = f"..."` line past the line limit so the formatter wrapped it. The avoidable growth — the 28-line helper — was extracted rather than absorbed.

## Disconfirmation

**Falsifier.** Someone needing to debug an unattended eval failure from the clone itself, and finding the vault's result branch plus loop state insufficient. That would show the loop path also carries a review contract, not just the standalone one.

**Steelmanned runner-up.** The state-dir + keep-last-N option is the only one that bounds *both* paths and makes clones discoverable by topic and corpus sha rather than by random name; if standalone eval usage grows, its extra machinery starts paying for itself and this decision becomes a subset of it.

**Reversal trigger.** Standalone eval clones measured accumulating beyond a few dozen, or a second consumer of `clone_root` appearing on the loop path that outlives the cycle.
