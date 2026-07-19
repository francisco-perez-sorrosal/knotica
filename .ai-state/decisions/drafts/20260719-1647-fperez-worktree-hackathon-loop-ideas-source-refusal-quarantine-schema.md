---
id: dec-draft-97c5122a
title: Source-gate outcome — quarantine (loop/x/*) with a bounded per-question diff, one additive gate_outcome field, auto-mark_ingested on merge, and a git-derived contamination-guarded dataset upgrade
status: proposed
category: architectural
date: 2026-07-19
summary: A refused source candidate is renamed (not deleted) to a quarantine branch loop/x/<topic>/source-<id8> carrying a bounded top-N per-question dilution diff (from the dec-023 v2 manifest retrieval-trace deltas); the suggestion records one additive nullable gate_outcome field (merge → status advances to ingested via auto mark_ingested + gate_outcome{merged,loop/r ptr}; refuse → status stays approved + gate_outcome{refused,loop/x ptr,reason}, gate-terminal, ingest-queue consumers filter status==approved AND gate_outcome is null); on merge the trainset grows only for the git-derived newly-merged pages via a page-subset filter on bootstrap_trainset/golden.bootstrap; held-out golden candidates are client-synthesized from the source text BEFORE ingest, kept disjoint from qa.jsonl, and frozen only through the existing human-gated read-merge-freeze.
tags: [gapfill, phase-p4, source-gate, quarantine, suggestion-record, schema-evolution, contamination-guard, dataset-upgrade, dec-030, dec-018, dec-008]
made_by: agent
agent_type: systems-architect
branch: worktree-hackathon-loop-ideas
pipeline_tier: standard
affected_files:
  - src/knotica/core/records.py
  - src/knotica/core/source_gate.py
  - src/knotica/core/gapfill.py
  - src/knotica/evals/train_bootstrap.py
  - src/knotica/evals/golden.py
affected_reqs: [REQ-05, REQ-06, REQ-07, REQ-08]
re_affirms: dec-030
dissent: A single gate_outcome field leaves a refused source at status=approved, so every ingest-queue consumer must remember the compound filter status==approved AND gate_outcome is null; a distinct terminal refused status would make the negative state self-evident to a status-only filter, at the cost of a new status-enum value and the migration/surfacing it drags through suggestions_read, wiki_status, and the dashboard.
---

## Context

Once the U1 ingest (dec-draft-0a5dd23b) publishes a `loop/c/<topic>/source-<id8>` candidate and
the gate classifies it source (dec-draft-3b1145b5), P4 must wire the two outcomes and the
post-merge dataset evolution — the halves SYNTHESIS §Layer 5 names.

Today `_keep` merges and deletes the candidate; `_discard` **deletes** a failed candidate,
losing all explanation. dec-030 froze `SuggestionRecord` v1 (five-state lifecycle
`pending/approved/rejected/deferred/ingested`; `ingested_at`; `mark_ingested` an explicit
`approved→ingested` action) and its Addendum sanctioned the gate automating `mark_ingested` and
adding an **additive** branch-reference field. The interface-designer shadow (dec-draft-64b4196f)
independently proposed surfacing the gate outcome through a single additive nullable
`gate_outcome` field and quarantining under a `loop/x/…` namespace with a bounded,
pointer-not-payload diff. `bootstrap_trainset` and `golden.bootstrap` both iterate **ALL**
entity pages today (internal gap #5). The contamination guard (SYNTHESIS §Layer 5, load-bearing):
golden candidates derived from a source must be generated **before** ingest and stay disjoint
from the flywheel `qa.jsonl`, or the wiki answers them from the page it just ate and the score
inflates. `golden.freeze` **replaces the entire** frozen set (dec-018) — any grower must
read-merge-freeze.

## Decision

**Refuse = quarantine, not delete.** On source-fail the gate renames
`loop/c/<topic>/source-<id8>` → `loop/x/<topic>/source-<id8>` (kept; invisible to
`_next_candidate`'s `loop/c/` scan) and commits a **bounded top-N** per-question dilution diff
(which golden ids regressed, which page displaced which — read from the dec-023 v2 manifest
`held_out_delta` retrieval-trace deltas) as an artifact on that branch. `loop/x/*` is pruned to
the newest 5 per topic, mirroring `_prune_result_branches`.

**One additive nullable `gate_outcome` field** on `SuggestionRecord` (adopting
dec-draft-64b4196f's surface-schema shape over this plan's first sketch of a separate `refused`
status + `gate_ref`/`gate_reason`):
- **merge** — `_keep` merges onto default; the gate auto `mark_ingested` (status
  `approved → ingested`, `ingested_at` set — dec-030 Addendum) and sets `gate_outcome =
  {result: merged, ref: <loop/r/* pointer>}`.
- **refuse** — status **stays `approved`**; `gate_outcome = {result: refused, ref: <loop/x/*
  pointer>, reason: <one line>}` is the **gate-terminal** marker. Ingest-queue consumers filter
  `status == approved AND gate_outcome is null`. The record survives branch prune as the durable
  audit trail (pointer-not-payload).

The field is additive-only: `SuggestionRecord.from_json_line` already tolerates absent/extra
fields, so pre-P4 records parse unchanged; no new status-enum value, no migration.

**Page-subset dataset upgrade (git-derived).** `bootstrap_trainset` and `golden.bootstrap` gain
a `pages: Sequence[str] | None = None` filter (`None` = ALL pages, byte-identical to today). On
merge, the headless loop grows the **trainset** for exactly the newly-merged entity pages,
computed **deterministically from git** (`changed_paths(merge_base, merged_head)` filtered to
the topic entity-page glob) — never from a client-reported list. This LLM synthesis runs in the
**headless loop** (dec-014 sanctions server-side LLM for headless loops), not the interactive
ingest.

**Contamination guard (ordering + disjointness).** Held-out golden candidates for a source are
**client-synthesized from the source text, before ingest** (client-as-brain — no server LLM on
the ingest path), staged disjoint from `qa.jsonl` (reusing `bootstrap_trainset`'s existing
golden-collision exclusion precedent), and reach the frozen gate set **only** through the
existing **human-gated** `golden.freeze` (read-merge-freeze — never freeze a subset, dec-018).
The automated post-merge path grows the trainset, **not** the frozen golden gate, so the gate
eval (against the frozen golden only) cannot inflate on questions the source trivially answers.

## Considered Options

### Refusal attach point
- **Quarantine `loop/x/*` + bounded diff artifact + `gate_outcome` (chosen):** keeps the
  explanation, honest audit, bounded cost, pointer-not-payload.
- **Delete like `_discard` (rejected):** loses the per-question dilution attribution the whole
  refusal story depends on.
- **Attach to the gap record / a sidecar file (rejected):** the gap is about the knowledge need,
  not this source's refusal; a sidecar is extra state the branch + record already carry.

### Schema shape
- **One additive `gate_outcome` field (chosen):** lowest surface, no status-enum value, no
  migration; interface-designer owns the surface (dec-030 Amendment).
- **Distinct `refused` status + `gate_ref` + `gate_reason` (rejected):** a status-only filter
  would read cleaner, but it adds a status-enum value and drags migration/surfacing through
  `suggestions_read`, `wiki_status`, and the dashboard — more surface for the same information.

### Newly-merged page identification
- **Git-derived `changed_paths` (chosen):** deterministic; independent of a client honestly
  reporting its page list.
- **Ingest-returned page list (rejected as primary):** trusts client self-report; kept only as
  an advisory cross-check.

## Consequences

**Positive:** every refusal is explained and auditable (durable on the record even after branch
prune); merges compound via a page-scoped, curated-record-safe dataset upgrade; the gate golden
stays human-gated and contamination-safe; schema stays additive; the trainset grower reuses the
loop's sanctioned headless LLM. Re-affirms dec-030 (additive, self-versioned) and dec-018
(human-gated whole-set freeze).

**Negative / costs:** a refused source sits at `status=approved` with a non-null `gate_outcome`
— consumers must apply the compound ingest filter (documented as the P4 As-Built Contract; a
`source_ingest_open` may also short-circuit when a refuse `gate_outcome` already exists). The
`pages` filter and the client-side golden generator are new (bounded, additive) surface.

## Disconfirmation

- **Falsifier:** if a golden question that a merged source now answers is generated *after*
  ingest and frozen into the gate golden, the contamination guard is breached and the gate score
  inflates on trivially-answerable questions — the whole refusal signal degrades. If a consumer
  treats `status=approved` as "still queued" without checking `gate_outcome`, a refused source is
  re-ingested in a loop.
- **Steelmanned runner-up (distinct `refused` status):** a terminal `refused` status makes the
  negative state self-evident to a plain status filter, surfaces natively in `suggestions_read`'s
  status filter and the dashboard badge without a compound predicate, and removes the "approved
  but actually terminal" ambiguity — worth the extra enum value if the queue's consumers multiply
  or the compound filter proves error-prone.
- **Reversal trigger:** if the compound ingest filter causes a real re-ingest bug, or the
  dashboard/queue needs a first-class refused view, promote `gate_outcome=refused` to a distinct
  `refused` status (additive at that point). If the git-derived page subset ever misidentifies
  merged pages (e.g. a merge strategy that rewrites unrelated paths), revisit the ingest-returned
  list as primary.

## Prior Decision

Re-affirms **dec-030** (self-versioned, additive `SuggestionRecord`; the Addendum's sanction of
gate-automated `mark_ingested` and an additive branch-reference field is exactly the evolution
`gate_outcome` performs) and **dec-018** (human-gated whole-set golden freeze; the contamination
guard preserves read-merge-freeze). Depends on dec-draft-3b1145b5 (which selects the quarantine
route for source-fail) and cross-references the interface-designer's dec-draft-64b4196f (the
`gate_outcome` surface shape and `loop/x/*` namespace this decision implements gate-side).
