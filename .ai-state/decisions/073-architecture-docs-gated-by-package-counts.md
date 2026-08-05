---
id: dec-073
title: The architecture documents are gated by per-package module counts, not by per-module naming
status: accepted
category: architectural
date: 2026-08-05
summary: A new `scripts/check_architecture_coverage.py` fails `make verify` when a package's module count drifts from DESIGN.md §3's inventory table or a cited path stops resolving; per-module naming was measured and rejected because it reverses the residual decision.
tags: [architecture-docs, gate, drift, make-verify, residual, gate-liveness]
made_by: agent
agent_type: orchestrator
branch: feat-test-topology
pipeline_tier: standard
affected_files:
  - scripts/check_architecture_coverage.py
  - tests/test_architecture_coverage.py
  - Makefile
  - .ai-state/DESIGN.md
  - docs/architecture.md
dissent: Counting is a weaker invariant than naming. A module can be counted, pass the gate forever, and never be described anywhere -- so the architecture record can stay accurate about how many components exist while saying nothing useful about what they do, which is the failure the documents exist to prevent.
---

## Context

Two gates landed this week: `scripts/test_group.py --check` for the test topology and
`scripts/check_adr_health.py` for the ADR corpus, both wired into `make verify`. Nothing gated the
architecture documents — and within hours of a pass that corrected them, `.ai-state/DESIGN.md` and
`docs/architecture.md` drifted again. Three `core/` modules (`topics.py`, `jsonl.py`,
`loop_attempt.py`) landed in commits `fe10b7a` and `05d1c89`, and appeared **zero** times in either
document; `1427fd4` then reconciled `TEST_TOPOLOGY.md` to them and left the architecture record
behind (td-038).

The generalizable finding is not the three modules. It is that **the one artifact pair with no gate
is the one that lagged**, and that the *gated* artifact absorbing the content made the lag harder to
see rather than easier — a reader of `TEST_TOPOLOGY.md` saw a current tree, a reader of `DESIGN.md`
saw a three-module-old one, and nothing reconciled them.

## Decision

Add `scripts/check_architecture_coverage.py` as the third step of `make verify`, enforcing two exact,
fail-closed invariants:

1. **Inventory.** Every package under `src/knotica/` publishes its module count in a new
   package-inventory table in `DESIGN.md` §3, and the count equals what is on disk. `__init__.py` is
   counted.
2. **Citations resolve.** Every `src/knotica/...` path cited in either document exists on disk, with
   globs resolved as globs and designed-but-unbuilt paths listed by name in a `PLANNED_PATHS`
   constant rather than tolerated by a pattern.

The gate does **not** require every module to be named in a document. Its docstring says so
explicitly, so the guarantee is not over-read.

The count convention is not invented: the pre-existing `okf/` ("11 modules"), `guillotine/` ("9
modules") and `service/` ("3 modules") prose claims all matched the tree exactly when `__init__.py`
is included. Those three prose counts are removed and replaced by table rows, because a count
published twice is a count that drifts once — and leaving three ungated numeric claims in the file
whose entire lesson is "the ungated claim lags" would be self-refuting.

## Considered Options

### Require every module to be named in one of the two documents

The rule the finding's wording implies, and the strongest-sounding invariant. **Measured and
rejected.** Requiring the literal `<name>.py` or a full path flags **60** modules today. Closing that
means naming 60 modules — which is precisely the five-way `core/` decomposition considered and
rejected on 2026-08-04 (the steelmanned runner-up in `dec-070`), arrived at by the back
door. `DESIGN.md`'s `core/` row is deliberately a residual: "Read it as a subtraction, never as
`core/**`." A gate that cannot be satisfied without reversing a recorded decision is not a gate, it
is a re-litigation.

### Match a module by its bare stem rather than its filename

Relaxing the match to a backticked bare stem (`` `lint` `` covering `lint.py`) drops the failure set
from 60 to 25, which looks tractable. **Rejected as unsafe, and this is the decisive measurement.**
Bare stems are ambiguous for exactly the common words that appear as JSON keys, tool names, and
field names: the rule reports `core/topics.py` as *covered* because the word `topics` appears
backticked elsewhere in the document — while td-038's whole finding is that `core/topics.py` appears
zero times. A permissive variant that splits dotted tokens is worse still: `metrics.jsonl` covers
`core/jsonl.py`. A gate that reports success over something it never inspected is worse than no
gate, because it also removes the suspicion that would have prompted a manual read.

### Counts only, per package (chosen)

Counts are exact integers with no false-positive surface. Critically, they catch the *mechanism* that
actually produced td-038 — a module arriving or leaving without the record being told — including for
modules that live inside a residual row and are therefore never named by design. This is the honest
answer to "how do you gate a residual?": you gate its arithmetic.

### A per-package `<!-- arch-inventory: ... -->` marker instead of a table row

Rejected as an invented convention with a second textual site. The table is human-readable, renders,
and is the single source of truth the gate parses.

## Consequences

**Positive.** The drift class is closed, not just the instance: adding a module to any package now
fails `make verify` at the developer's own gate rather than at the next audit. Both documents become
citation-safe — a rename cannot leave a dangling path behind, which is what `docs/architecture.md`'s
header has been promising ("Every path in the table below was re-checked on disk") with nothing
holding it to it. The residual rows become *safe to read as residuals* for the first time, because
what falls into them is now bounded by an arithmetic claim.

**Negative.** Every new module now requires a one-integer edit to `DESIGN.md`, which is friction on
the fast path — deliberate, and the smallest friction that makes the record self-correcting. The gate
proves accounting, not description: a counted-but-undescribed module passes forever. And the
inventory table is a new maintenance surface that a package rename touches.

## Disconfirmation

**Falsifier.** If a future audit finds a module that is *counted* by the table and *described*
nowhere, and the absence of a description causes a real misunderstanding (an agent's brownfield
baseline missing a component, a wrong dependency assumption), then counting was the wrong invariant
and naming should have been enforced despite its cost.

**Steelmanned runner-up.** Per-module naming is the invariant the documents actually want. The
architecture record exists so a reader learns what a component *does*, and a count teaches nothing
about that; "60 modules are unnamed" is arguably a finding rather than an argument against the rule
that surfaced it. The honest counter is that the 60 include leaves nobody would describe
individually, and that the decomposition needed to describe them was already weighed and declined on
proportionality grounds — but if `core/` is ever decomposed for other reasons, the naming rule
becomes cheap and should be revisited.

**Reversal trigger.** Either (a) the falsifier above fires, or (b) `DESIGN.md` §3 gains per-cluster
rows for `core/` such that the residual disappears — at which point per-module naming costs little
and the stronger invariant should replace the count.
