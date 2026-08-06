---
id: dec-075
title: Split § 3 into structural components and capabilities, and widen TT01 to resolve against both
status: accepted
category: architectural
date: 2026-08-05
summary: Both architecture documents adopt the Praxion two-tier § 3 — `3a. Structural components` (one row per LikeC4 `component`, i.e. one package) and `3b. Capabilities` (cross-cutting features owning no directory) — and `TEST_TOPOLOGY.md`'s TT01 rule widens from "resolve to a §3 Built component" to "resolve to a §3a component or a §3b capability", because a rule scoped to §3a alone would orphan six topology entries the moment the tier split landed.
tags: [architecture-docs, granularity, components, capabilities, test-topology, tt01, praxion-contract]
made_by: agent
agent_type: orchestrator
branch: main
pipeline_tier: full
affected_files:
  - .ai-state/DESIGN.md
  - docs/architecture.md
  - .ai-state/TEST_TOPOLOGY.md
  - docs/diagrams/architecture/src/architecture.c4
re_affirms: dec-069
dissent: Praxion's own `architecture-documentation.md` says checks that read *components* resolve against 3a only. Widening TT01 to admit §3b makes this project's rule diverge from the upstream contract, and a future upstream sync will have to reconcile the two — a cost paid by whoever does that sync, not by this pass.
---

## Context

`.ai-state/DESIGN.md` § 3 was a single flat table that had grown one row per shipped feature until row
granularity was inconsistent by construction: package rows (`store/`, `okf/`) sat beside single-module
rows (`core/vault_layout.py`, `evals/error_capture.py`) and multi-module cluster strings (a sixteen-module
loop cell, an eight-module compile cell, three gap-fill cells). `docs/architecture.md` had drifted
further — its sections ran § 3, § 3b, § 3a, § 3d, with `3a` naming the *loop lifecycle* and `3b` the
*tool surface*, exactly inverting the tier names the contract reserves, and §§ 5–8 were absent entirely.

Praxion's `architecture-documentation.md` specifies a two-tier § 3 precisely to stop this: a merged table
grows without bound until it silently contradicts the model above it and can no longer be reconciled.

The blocking coupling is `.ai-state/TEST_TOPOLOGY.md`. Its 23 `subsystems` entries were bound 1:1 to the
flat § 3 table's Built rows, and its own Note 1 states the sequencing rule: *"The unblock is always a § 3
refinement pass first, a topology edit second."* Six of those entries named cluster strings that the tier
split converts into capabilities, so the split could not land without deciding what TT01 resolves against.

## Decision

1. **Both documents adopt `### 3a. Structural components` and `### 3b. Capabilities`.** A § 3a row is one
   `component` element in `docs/diagrams/architecture/src/architecture.c4`. Thirteen are packages under
   `src/knotica/`; two are not — the dashboard (repo-root Preact client plus its packaging loader, one
   component per `dec-070`) and the plugin layer. Fifteen Built rows, plus `agent/` as Planned in DESIGN
   only.
2. **Six capabilities occupy § 3b**: the single-mutation vault write path, the autonomous loop lifecycle,
   the MCP tool surface, query compile & promote, the gap-fill spine, and the notes overlay. Each is
   composed from § 3a components and owns no directory.
3. **`okf/` and `guillotine/` stay in § 3a, not § 3b.** Both pass the structural falsifier — name the
   directory — and both are `component` elements in the model. Modelling them as capabilities would
   double-count them against their own packages and drop them out of AC06's reach.
4. **TT01 widens.** A `subsystems` entry resolves to a Built § 3a component **or** a § 3b capability.
   Module-level entries that carve a single module out of a § 3a row (`core/vault_layout.py`,
   `core/notes_config.py`, `core/operations/capture_note.py`, `evals/error_capture.py`) are labelled as
   carve-outs, reusing the residual idiom Note 2 already established rather than inventing a third kind.
5. **The package-inventory table stays separate from § 3a.** It is a superset: `src/knotica/` itself is a
   package needing a row for the gate but is not a component and owns no responsibility. Merging the two
   was tried and rejected — it would have forced the package root into § 3a and thereby into the
   topology's totality rule, obliging a test group for an `__init__.py`.
6. **The LikeC4 model is updated in the same pass** from five components (one named `mcp/`, a package
   `dec-009` renamed away on 2026-07-03) to the sixteen that exist, with four scoped views. Those sixteen
   are the fifteen Built § 3a rows of #1 plus `agent/`, which is Planned and modelled so the diagram
   shows where the outer loop will attach.

## Considered Options

### A. Two-tier § 3, TT01 widened to §3a ∪ §3b (chosen)

- Restores the contract's structure; the § 3a row count matches the model 1:1 and can be reconciled.
- Costs one divergence from the upstream "3a only" wording, recorded in the dissent above.

### B. Two-tier § 3, TT01 restricted to § 3a

- Matches upstream exactly, and immediately FAILs six topology entries with nowhere to move them: the
  loop runtime, the compile chain, and the three gap-fill phases have no § 3a home by construction —
  they are precisely the things that own no directory.
- Would force either synthetic subsystem names (which Note 1 forbids by name) or folding six groups into
  `vault-semantics`, which is already the largest group and the one the topology most wants to split.

### C. Keep the flat § 3 table

- Zero churn, and the defect the contract exists to prevent keeps compounding: the table had already
  reached a point where a single row named sixteen modules and the developer guide's § 3a and § 3b meant
  the opposite of the contract's § 3a and § 3b.

## Consequences

**Positive.** § 3a is reconcilable against the model, so a new package is a three-place edit (model, § 3a,
inventory) with the count gated. The two documents now use one section vocabulary, so a cross-document
reference to "§ 3b" resolves to the same subject in both. `docs/architecture.md` regains §§ 5–8 as
pointers, which removes the duplicated loop/notes/dispatcher prose that was drifting in two directions.

**Negative.** The TT01 divergence from upstream (see dissent). Six topology rows now resolve against a
capability rather than a component, which is a slightly weaker check: a capability names no single
directory, so AC06-style "does this name a real module" verification does not apply to it — the same
exemption the contract already grants § 3b generally.

## Prior Decision

`dec-069` bound topology-group granularity to § 3 granularity and named "a § 3 refinement pass" as the
unblock for anything § 3 does not model. `dec-070` realized that trigger once, adding rows for four
un-modelled packages. This decision realizes it a second time and is a **re-affirmation, not a
supersession**: the binding rule is right and stays: the topology still inherits its granularity from
§ 3 rather than choosing it. What changes is only which § 3 tiers count as a resolution target, and that
change is forced by § 3 gaining tiers at all.
