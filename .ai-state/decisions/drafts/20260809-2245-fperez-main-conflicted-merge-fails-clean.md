---
id: dec-draft-06dd7710
title: A conflicted gate merge aborts and fails clean instead of stranding the vault
status: proposed
category: behavioral
date: 2026-08-09
summary: The loop's keep-path merge now catches a conflict, aborts it, records the failed cycle, and raises a typed error naming the colliding paths — rather than unwinding and leaving the live vault mid-merge with conflict markers in tracked files.
tags:
  - loop
  - gate
  - git
  - vault-integrity
  - unattended-behaviour
  - failure-recovery
made_by: agent
agent_type: orchestrator
branch: main
pipeline_tier: standard
affected_files:
  - src/knotica/core/candidate_gate.py
---

## Context

Found by running a real ingest, not by reading code.

`candidate_gate.keep` fetches the eval clone's tip and fast-forward-merges it onto the default branch with a bare `runner._vcs.merge_branch(...)`. On a conflict, `git merge` exits non-zero **and leaves the working tree mid-merge** — `MERGE_HEAD` set, conflict markers written into tracked files. Nothing caught it. The exception unwound out of the cycle and the live vault stayed conflicted.

Observed on `decision-making`:

```
CONFLICT (add/add):  decision-making/.knotica/eval-runs/gen-3/manifest.json
CONFLICT (content):  decision-making/.knotica/metrics.jsonl
```

The candidate's *content* — four pages and six source chunks — merged cleanly. Only the loop's own bookkeeping collided, because the candidate had been branched before the default branch gained generation 3: its eval clone therefore numbered its own record gen-3 as well, and two different gen-3s cannot merge.

The conflict is a nuisance. The aftermath is the defect. The vault sat with conflict markers in tracked files and `MERGE_HEAD` set until something happened to call `heal_git_mutation_state` — which only runs at the *start* of some later mutation span. Until then, every reader sees a broken tree: Obsidian, an MCP tool, a human running `git status`, the next `VaultTransaction`. For a watcher designed to run unattended overnight, "leave the knowledge base wedged and stop" is the wrong shape of failure — and `keep`'s merge is one of the few git mutations that runs outside a `VaultTransaction`, so nothing rolled it back.

## Decision

`keep`'s merge goes through `_merge_or_leave_clean`, which on failure:

1. Collects the conflicted paths (for the message) and **aborts the merge**, restoring a clean tree.
2. Records the cycle as `failed` in loop-state with the colliding paths, rather than leaving `stage` stranded at `merging`.
3. Raises a typed `GIT_ERROR` naming what collided, stating that the candidate branch is untouched and still pending, and giving the way out: refresh the candidate against the default branch and re-submit.

Both recovery steps are `best_effort` — an abort that itself fails must not replace the merge error with its own.

**The generation collision itself is left unfixed.** The immediate defect is the stranded vault; the collision is a separate design question (should the gate evaluate the candidate *merged with* current default, rather than in isolation?) that deserves its own decision. The error message now tells an operator exactly what to do about it in the meantime — refreshing the candidate against the default branch is a clean git merge and was verified end to end on the live vault.

## Considered Options

### Abort and fail clean (chosen)

- Bounded, unambiguous, and makes the unattended failure mode safe.
- Does not make the merge succeed — a stale candidate still needs refreshing.

### Rely on `heal_git_mutation_state`

- Already exists and already clears a dangling `MERGE_HEAD` at span entry, so arguably the machinery is there.
- It is a *crash* remnant handler, and it runs at the start of the next span — which may be seconds away or never. A handled failure should not leave wreckage for an unrelated later caller to discover.

### Give `metrics.jsonl` a union merge driver, like `log.md`

- The loop already self-heals `log.md merge=union` into `.gitattributes`; metrics is also an append-only JSONL journal, so the shape fits.
- It would resolve the conflict by keeping *both* gen-3 records — two different measurements sharing a generation number. That silently corrupts the metrics history rather than failing, which is worse than the conflict.

### Evaluate the candidate merged with current default

- Addresses the actual cause, and is arguably what the gate should measure anyway: a scalar for the candidate *as it would land*.
- A design change to what the gate means, not a failure-handling fix. Out of scope here; recorded as the reversal trigger below.

## Consequences

**Positive.** The loop can no longer strand the vault in a conflicted merge. A stale candidate now produces a diagnosis instead of wreckage, and the diagnosis names the remedy. Loop-state ends at `failed` with the colliding paths rather than stuck at `merging`.

**Negative.** A conflicted candidate still cannot merge without operator action — the failure is clean but it is still a failure, and the underlying staleness is unaddressed. The failure path now writes a loop-state commit, so a conflicted cycle advances the default branch by one bookkeeping commit even though it merged nothing.

## Disconfirmation

**Falsifier.** A conflicted merge that still leaves `MERGE_HEAD` set or conflict markers in a tracked file — meaning the abort did not fire, or fired and failed silently through `best_effort`.

**Steelmanned runner-up.** Evaluating the candidate merged with current default. It removes the collision rather than reporting it, and produces a more honest scalar. If stale candidates turn out to be common rather than an artifact of one long-running ingest, that is the fix to build.

**Reversal trigger.** Operators hitting this conflict routinely. One occurrence is a stale candidate; a pattern means the gate is measuring the wrong tree and the refresh should be automatic rather than advised.
