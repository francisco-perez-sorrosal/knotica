---
id: dec-036
title: candidate_kind is a branch-name convention, and a source candidate that fails the gate is quarantined, never raced through the arena
status: accepted
category: architectural
date: 2026-07-19
summary: The loop distinguishes a source candidate from a prompt candidate by branch name alone (loop/c/<topic>/source-<suggestion_id[:8]>), not a persisted loop-state field or marker file — git-derived, stateless (dec-004), and the linked suggestion_id recovers for free; _process_candidate gains a thin kind-branch so a source candidate that regresses the scalar routes straight to quarantine (dec-038) and is never raced through the arena, because the arena heals prompt regressions and a content-dilution regression is not prompt-fixable — racing it risks a prompt variant that masks the dilution (a reward-hacking hazard the autoresearch defenses guard against).
tags: [gapfill, phase-p4, source-gate, candidate-kind, loop, arena, stateless-server, dec-004, branch-topology]
made_by: agent
agent_type: systems-architect
branch: worktree-hackathon-loop-ideas
pipeline_tier: standard
affected_files:
  - src/knotica/core/loop.py
  - src/knotica/core/source_gate.py
affected_reqs: [REQ-04, REQ-05, REQ-06]
re_affirms: dec-004
dissent: A branch-name convention is a stringly-typed soft contract — a typed candidate_kind field on loop-state would be self-documenting and refactor-safe, at the cost of a new persisted field, a schema migration, and a second source of truth the loop must keep in sync with the branch tips it already scans.
---

## Context

SYNTHESIS §Layer 4 generalizes the shipped `loop/c/*` gate to a new candidate kind —
`source` (vs today's implicit prompt candidates) — and §Decisions #1 deferred the *modeling*
of `candidate_kind` to P4 as "a small ADR." Two questions: (1) **where** does the gate read a
candidate's kind, and (2) **how** does the gate behave for a source candidate, given the arena
(`_race_then_resolve`) is a prompt-regression healer that races query-prompt variants.

Today `_next_candidate` takes any unprocessed `loop/c/*` tip and `_process_candidate` gates it
uniformly: pass → `_keep` (merge), fail → arena-or-`_discard`. No kind distinction exists.
`DEFAULT_BRANCH_PREFIX = "loop/c/"`. The loop is stateless per dec-004 — its only persisted
state is `loop_state` (via `VaultTransaction`) and the git branch tips it scans.

## Decision

**Kind is a branch-name convention, not persisted state.** A source candidate is published (by
the U1 ingest, dec-037) at `loop/c/<topic>/source-<id8>` where `id8 =
suggestion_id[:8]`; any other `loop/c/*` tip (e.g. `loop/c/<sha>` from a prompt/arena flow) is
a prompt candidate — today's behavior. Detection is a pure function of the branch name
(`classify_candidate(branch) -> "source" | "prompt"` keyed on a named `/source-` infix
constant; `suggestion_id_from_branch(branch)` recovers the id by prefix-scan of
`suggestions.jsonl`, failing closed on an ambiguous prefix). No new loop-state field, no
migration, no second source of truth.

**`_process_candidate` gains a thin kind-branch**, delegating the source path to a new cohesive
`core/source_gate.py` module (keeps logic out of the already-over-ceiling `loop.py`, td-008):

- **prompt candidate** — unchanged: pass → `_keep`; fail → `_race_then_resolve` (arena) or
  `_discard`.
- **source candidate, pass** (scalar ≥ baseline) — `_keep` (FF/merge onto default), then auto
  `mark_ingested` the linked suggestion + set `gate_outcome` + trigger the page-subset trainset
  grower.
- **source candidate, fail** (scalar < baseline) — **quarantine** (dec-038): never
  the arena.

**Source-fail never races the arena.** The arena mutates the query-prompt substrate to heal a
*prompt*-caused regression. A source candidate's regression is content dilution (a newly
ingested page displaced a relevant page in the retrieval trace — the dec-024 classifier's
`dilution` fault). Prompt variance cannot fix dilution; worse, racing could find a prompt that
*happens* to paper over the dilution, masking a genuine quality loss — exactly the
reward-hacking-via-prose failure the autoresearch defenses (SYNTHESIS §"Autoresearch-brief
defenses") exclude. A no-baseline topic gates nothing (existing `poll_once` no-op), so a source
candidate simply waits until a baseline is frozen (observe auto-freezes the first observation).

## Considered Options

### Option A — branch-name convention (chosen)
- Pro: zero persisted state (dec-004); no schema/migration; recovers `suggestion_id` for free;
  the loop already keys everything off branch tips; a single parse function + constant contains
  the stringly-typed risk.
- Con: a naming convention is a soft contract enforced by tests, not types.

### Option B — a persisted `candidate_kind` field on loop-state
- Pro: typed, self-documenting.
- Con: a new persisted field + migration + a second source of truth to sync with branch tips;
  the id-recovery still needs the branch name anyway. Rejected (dec-004 / Simplicity First).

### Option C — a sidecar marker file per candidate
- Pro: explicit.
- Con: another cross-call artifact to write/read/prune; strictly more state than the branch
  name already carries. Rejected.

### Sub-decision — source-fail routing: arena vs quarantine
- **Quarantine (chosen):** honest — the dilutive source is caught and explained.
- **Arena race (rejected):** category error (heals prompts, not content) and a reward-hacking
  hazard.

## Consequences

**Positive:** the gate generalizes to sources with no new persisted state and a minimal
`loop.py` delta; the arena stays prompt-only (a clean, stable contract future kinds inherit);
kind + linked id are both git-derived. Branch topology gains a documented `loop/c/*/source-*`
family.

**Negative / costs:** kind detection is a naming convention — a mis-named branch is
mis-classified (mitigated by one parse function, a constant infix, and the U1 publish step that
mints the name). `loop.py` grows slightly (bounded by delegating to `source_gate.py`, honoring
the deferred td-008 ceiling rather than worsening it).

## Disconfirmation

- **Falsifier:** if a future candidate flow legitimately needs `loop/c/<topic>/source-…` naming
  for a *non-source* purpose, or if source candidates must carry structured kind metadata the
  branch name cannot hold, the convention breaks and a typed field (Option B) is required.
- **Steelmanned runner-up (Option B):** a typed `candidate_kind` field is self-documenting,
  survives a branch-naming refactor, and extends cleanly to F2 (contradiction mode) and further
  kinds without stringly-typed parsing — the right shape if the number of candidate kinds grows
  beyond two or if kind ever needs attributes.
- **Reversal trigger:** when a third candidate kind lands (e.g. F2 contradiction candidates) or
  a kind needs structured attributes, promote the convention to a typed field — an additive
  loop-state evolution at that point, not now.

## Prior Decision

Re-affirms **dec-004** (stateless server): candidate kind and the linked suggestion id are both
derived from git branch names, adding no persisted cross-call state. Depends on
dec-037 (which mints the `loop/c/<topic>/source-<id8>` name) and pairs with
dec-038 (the quarantine route this decision selects for source-fail).
