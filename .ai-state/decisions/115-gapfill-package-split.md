---
id: dec-115
title: core/gapfill.py becomes the core/gapfill/ package, split on its own five seams
status: accepted
category: architectural
date: 2026-08-31
summary: The 1536-line gapfill module splits into six modules behind a re-exporting __init__ — queue_io, synthetic, gap_review, review, drain, discovery_bridge — retiring the largest file-size-ratchet exemption with zero consumer import churn
tags: [refactoring, gapfill, module-boundaries, file-size, tech-debt]
made_by: agent
agent_type: implementer
branch: main
pipeline_tier: standard
dissent: "Six modules replace one module's in-file section banners with six import statements; a reader who could previously grep one file for the whole gap-fill lifecycle must now know which module owns which half."
affected_files:
  - src/knotica/core/gapfill/__init__.py
  - src/knotica/core/gapfill/queue_io.py
  - src/knotica/core/gapfill/synthetic.py
  - src/knotica/core/gapfill/gap_review.py
  - src/knotica/core/gapfill/review.py
  - src/knotica/core/gapfill/drain.py
  - src/knotica/core/gapfill/discovery_bridge.py
  - tests/test_file_size_ratchet.py
  - .ai-state/DESIGN.md
  - docs/architecture.md
  - .ai-state/TEST_TOPOLOGY.md
---

# `core/gapfill.py` becomes the `core/gapfill/` package, split on its own five seams

## Context

`core/gapfill.py` was the largest exemption the file-size ratchet ever carried: 1536 lines
against an 800-line ceiling, 92% over. Its baseline was raised seven times, and each raise was
argued in place — every addition was genuinely unextractable *given the module's shape*, because
the one-commit-per-operation invariant pins a `VaultTransaction` and every write it declares into
one function body. The ledger row (td-042) recorded that the answer was never "move one function
out"; a measured alternative (extracting `apply_gap_decision` alone) shed 99–132 lines and would
still have left the module 183–216 over while cutting on a *fourth* axis.

What made a split safe now is the suite. v0.3.0 shipped this module with ~314 gap-fill-spine
tests covering the cascade rollback, span-lock concurrency, re-drain-after-reopen, and the
healing pass — the strongest characterization net this code has ever had.

## Decision

`core/gapfill.py` becomes the `core/gapfill/` package. Six modules, each with one responsibility
stated in its own docstring; `__init__.py` re-exports the public surface verbatim so
`from knotica.core.gapfill import X` keeps working for every consumer.

| Module | Lines | Responsibility |
|---|---|---|
| `queue_io.py` | 261 | The two committed JSONL queues as files, plus candidate identity, the published-branch protection both writers consult, and the shared refusal wording |
| `synthetic.py` | 272 | Filing a gap no eval produced — the `reported` and `retracted` origins |
| `gap_review.py` | 303 | The gap lifecycle: human dismiss/reopen, its cascade, the machine `open -> resolved` close |
| `review.py` | 334 | Every status transition of a *suggestion* record — the human lifecycle and the machine gate verdict that mirrors its legality |
| `drain.py` | 421 | One drain: select, formulate, join, heal, write once |
| `discovery_bridge.py` | 92 | Config + env keys to a real `DiscoveryService`, or `None` |
| `__init__.py` | 86 | The import surface + the package's own map |

The dependency graph is acyclic and one-directional: `queue_io` is the leaf; `synthetic`,
`gap_review` and `drain` sit on it; `review` sits on `gap_review` for the gap body a merged
verdict closes; `discovery_bridge` depends on nothing local.

Boundaries were derived from the module's own cohesion seams, not from line-count arithmetic —
they follow which constants each function reads and which queue each function writes.

## Considered Options

### One package, six modules, re-exporting `__init__` (chosen)

- Zero import churn: no consumer, in `src/` or `tests/`, needed an edit.
- Every module lands between 92 and 421 lines; the ratchet needs no successor exemption, so the
  entry is *deleted* rather than lowered — the rule-3 outcome the ratchet was built to force.
- The one-commit invariant survives intact: no `VaultTransaction` crosses a module boundary, and
  the two operations that declare a second file write still declare it to their own transaction.

### The ledger's earlier three-way cut (file / drain / decide-gate)

The row's original prose proposed exactly three modules. Rejected as measured: `report_gap` /
`file_retracted_gap` are not the only "file" writers (the classifier is), and lumping the gate
with the human decision *and* the gap lifecycle would have produced a ~700-line "decide" module —
inside the ceiling but reproducing the god-module problem one size down.

### Two modules — split only `apply_gap_decision` out

The alternative td-042 already measured and rejected: it sheds 99–132 lines, leaves the module
183–216 over the ceiling, raises the baseline anyway, and cuts on an axis that strands the
queue's other writers.

## Consequences

**Positive.** The largest ratchet exemption is retired outright. Each module is navigable in one
read. The lifecycle tables now sit beside — and only beside — the transitions they govern, so a
future addition lands in a 300-line module rather than a 1536-line one. `review.py` provably
imports no `discovery`, which is the cold-start property a fitness test already pins.

**Negative.** Six import statements replace five in-file section banners; a reader must know
which module owns which half of the lifecycle (the package docstring is the map). One helper,
`_legal_exits_hint`, lost its default argument: both lifecycle tables now pass theirs explicitly,
because the default silently privileged one of two callers and could not follow both.

**Neutral.** `__init__.py` re-exports `_source_key` under a redundant alias — a test imports it by
name to pin that the queue's dedup key cannot drift from `discovery.normalize`'s.

## Disconfirmation

**Falsifier.** A gap-fill change that has to touch four or more of the six modules at once. That
would mean the seams cut across a change axis rather than along one, and the split should be
re-cut (most likely by merging `review` and `gap_review` into one lifecycle module).

**Steelmanned runner-up.** Keeping one module and simply raising the baseline again is the option
with the best track record here: seven raises, each individually correct, each preserving a
one-file grep for the whole lifecycle. Cohesion arguments lose to that when the module has a
single reason to change — and "the gap-fill queue" is arguably one reason. The counter is
measurement, not taste: at 1536 lines the module was 92% over a ceiling every other module in the
tree respects, and its own baseline comments had started citing td-042 as the fix in four
separate places. A rule that everything eventually excuses itself from is not a rule.

**Reversal trigger.** If the ratchet's `tests/` half or a later field report shows the split made
a class of bug *harder* to see — a lifecycle invariant now enforced in two modules where it was
previously enforced in one — collapse `review` and `gap_review` back together and accept a
~630-line module rather than re-introduce a second declaration of a legality table.
