---
id: dec-draft-ef07d3ff
title: Eval-manifest diagnostic substrate — manifest schema v2 (per-question id, retrieval trace, held_out_delta)
status: proposed
category: architectural
date: 2026-07-18
summary: The per-run eval manifest self-versions (manifest_schema_version) and gains stable per-question ids, an ordered retrieval-trace, and a wired cross-generation held_out_delta — a diagnostic substrate consumed by the P1 four-way fault classifier, additive over dec-006-frozen records.
tags: [evals, phase-3a, manifest, schema, diagnostics, retrieval-trace, held-out-delta, gap-fill, forward-compat]
made_by: agent
agent_type: systems-architect
branch: worktree-hackathon-loop-ideas
pipeline_tier: standard
affected_files:
  - src/knotica/evals/runner.py
  - src/knotica/evals/program.py
  - src/knotica/evals/golden.py
  - src/knotica/evals/harness.py
  - src/knotica/core/page.py
affected_reqs: [REQ-01, REQ-02, REQ-03, REQ-04, REQ-05]
re_affirms: dec-006
dissent: A per-run manifest that self-versions independently of metrics.jsonl introduces a second schema-version namespace; a purist reading of dec-006 would fold the manifest under METRICS_SCHEMA_VERSION instead of minting manifest_schema_version.
---

## Context

The autoresearch gap-fill spine (P1–P4) turns failed golden questions into source-discovery queries.
Its first consumer, the four-way fault classifier at `core/loop.py:339-345`
(dilution / retrieval-fault / generation-fault / genuine-gap), cannot function on today's eval data:
the war-story regression (0.96 → 0.9493, baseline forgotten) is neither detectable nor localizable.
Three concrete data gaps block it (research: `RESEARCH_FINDINGS_internal.md` §Data Gaps 1–3):

1. The manifest `per_example` list keys on question **text**, not the stable `QARecord.id`
   (`golden.py:860`) that already exists — cross-generation diffs are fragile to any reword.
2. `MessagesApiRunner._retrieve` (`runner.py:240-244`) computes the pages retrieved per question then
   **discards** them — the single highest-leverage datum for attributing dilution is thrown away.
3. `held_out_delta` (`harness.py:932`) is a live placeholder hard-coded to `None`; nothing diffs a
   generation against its predecessor.

The manifest is the `artifact_ref` reproducibility blob written once per generation inside the eval's
single `VaultTransaction` commit on the clone (dec-015). It is **not** one of dec-006's five frozen
record kinds and carries no `schema_version` today. This decision freezes the substrate shape the
P1–P4 consumers will read, without building any consumer.

## Decision

Introduce **manifest schema v2** — additive over the current manifest, all existing keys retained:

- **`manifest_schema_version: 2`** at the manifest root (today's unversioned shape is implicit v1).
  This is the read-time capability probe: `>= 2` guarantees the fields below; absent/`< 2` signals a
  pre-substrate manifest and triggers graceful degradation in the reader.
- **`per_example[].id`** = the golden `QARecord.id` (edit-stable hash of query+answer). Requires
  mapping `id` onto the `dspy.Example` in `golden.to_example` as a non-input field so the harness
  breakdown loop can read `gold.id`.
- **`per_example[].pages`** = the ordered, deduplicated topic-relative page **names** the runner
  actually retrieved (rank = index; `DEFAULT_MAX_PAGES = 5`), normalized to `QARecord.pages_used`
  form via a **shared** normalizer so the P1 join matches by construction. Carried through a new
  `Prediction.pages` field, forwarded in `BaselineProgram.forward`, onto `_ExampleBreakdown`.
- **`held_out_delta`** populated as an object (scalar delta + per-id vector of score deltas and
  retrieval-trace diffs + `ids_added`/`ids_removed` set membership), keyed on stable id, discovered
  via the prior `MetricsRecord.artifact_ref`. `null` — never a fabricated `0` — when no comparable
  prior manifest exists (generation 1, prior absent/unreadable, or prior below v2).

Retrieval **scores** are deliberately excluded (rank-order only) to keep the contract stable across
the Phase-5 vector-backend swap; they can be added additively in a v3 if proven load-bearing. No
scalar, metric, harness fingerprint, or `metrics.jsonl` (dec-006) record shape changes — the
fingerprint (`config.py:312-354`) folds none of the touched surface, so no baseline re-freeze occurs.

This **re-affirms dec-006**: machine-readable records self-version. We extend that discipline to the
manifest (which lacked it) rather than modifying dec-006's frozen `metrics.jsonl` shape.

## Considered Options

### Option A — manifest_schema_version on the manifest only (chosen)

- Pro: dec-006-frozen records stay byte-stable; the version stamp lands exactly where a new consumer
  contract begins; consumers get a clean capability probe.
- Con: a second version namespace (manifest vs record) to reason about.

### Option B — bump METRICS_SCHEMA_VERSION 1 → 2

- Pro: one versioning namespace.
- Con: `metrics.jsonl`'s aggregate components are untouched, so bumping it falsely signals the frozen
  record changed; every metrics parser would need a migration for a change it does not see.

### Option C — add manifest fields with no version stamp

- Pro: least code.
- Con: P1–P4 have no deterministic way to tell an old manifest (no id/pages/delta) from a new one; the
  graceful-degradation contract has nothing to probe. Rejected.

### Option D — persist retrieval scores alongside rank

- Pro: richer signal for threshold tuning.
- Con: not required for any of the four fault distinctions; couples the frozen contract to the
  ripgrep backend's scoring, which the vector-backend swap will change. Deferred to a possible v3.

## Consequences

**Positive:**
- P1's classifier reads one artifact; the load-bearing per-question trace diff is precomputed where
  both manifests are cheapest to access (already on the clone).
- Cross-generation comparison survives question edits (stable id join).
- Zero cold-start / cache / fingerprint impact; dec-006-frozen records untouched.
- Forward-compatible: v3 additions (scores, LLM hypothesis, lint attribution) are non-breaking.

**Negative / costs:**
- A second schema-version namespace (manifest_schema_version) coexists with record schema_versions.
- The `held_out_delta` object shape is now a consumer contract; changing it later needs a v3 bump.
- One small refactor: the page-name normalizer moves to a shared home.

## Disconfirmation

- **Falsifier:** If, when P1 is built, the four-way classifier turns out to need a datum not derivable
  from `{per_id score deltas, per_id trace diffs, QARecord.pages_used, a live clone page-existence
  check}` — e.g. it provably cannot separate generation-fault from genuine-gap without retrieval
  scores or an LLM hypothesis field — then v2's "necessary and sufficient" claim is wrong and a v3
  substrate item was required at P0.
- **Steelmanned runner-up:** Option C (no version stamp) is strongest if we accept that no manifest
  will ever predate the substrate in a running system — then the probe is dead weight and
  "tolerate-unknown-fields" parsing suffices. It fails only because historical gen-N manifests already
  exist on disk without these fields, and P1 must read them without crashing or fabricating data; the
  probe is what makes that safe and explicit rather than implicit.
- **Reversal trigger:** If a future backend makes retrieval scores stable and cheap AND a tuning need
  for score thresholds materializes, revisit Option D — add `per_example[].page_scores` and
  `per_id[].score_deltas` under manifest_schema_version 3 (additive, non-breaking).

## Prior Decision

Re-affirms **dec-006** (freeze machine-record schemas with `schema_version` at Phase 0). dec-006 froze
`qa.jsonl`, `metrics.jsonl`, log-entry, commit-message, and provenance — deliberately not the ancillary
per-run manifest. This decision does not reopen or supersede dec-006; it applies the same
version-your-machine-records principle to the manifest, an artifact dec-006 left unversioned, and
leaves every dec-006-frozen shape byte-stable. A future supersession would require evidence that the
manifest and `metrics.jsonl` should share one version namespace after all.

## Amendment (2026-07-18, implementation-planner, user-directed)

The Disconfirmation § Steelmanned-runner-up above named the precondition for Option C (no version
stamp needed): "no manifest will ever predate the substrate in a running system." The user has since
directed that this precondition be made true by construction rather than tolerated at read time —
there is exactly one vault, owned by the project, and its existing gen-2/gen-3 manifests are
reconciled to v2 shape (see the implementation plan's Step 15) rather than left as legacy v1 shapes a
reader must degrade around.

This **narrows, but does not reverse**, the original decision:

- `manifest_schema_version: 2` and the full field set (`id`, `pages`, `held_out_delta`) are unchanged
  — the version stamp remains useful (documents intent, guards future drift) even though the "tolerate
  an unknown-shape prior manifest" branch it was partly justified by is now out of scope.
- `_compute_held_out_delta`'s graceful-degradation contract narrows to exactly one null case: no prior
  generation exists (`generation == 1`, cold start). "Prior manifest unreadable" or "prior manifest
  predates v2" are no longer tolerated read-time branches — a corrupt prior manifest at
  `generation > 1` now raises (typed error), consistent with the project's typed-errors-over-silent-
  fallback convention, since the compat-tolerance rationale for swallowing it no longer applies.
- This does not change `status` (still `proposed`) or any cross-reference field; it is recorded here
  because the decision is still in-flight (unfinalized draft) rather than as a new `dec-draft-*`
  supersession.
