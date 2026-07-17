---
id: dec-018
title: Golden set — synthetic-from-pages + human review-freeze, held-out split from day one
status: accepted
category: architectural
date: 2026-07-15
summary: Bootstrap each topic's eval-scalar set by generating synthetic QA pairs from entity pages, then human review-and-freeze into a held-out golden.jsonl (~20-30 pairs) with a sha256 MANIFEST, kept disjoint from the flywheel qa.jsonl (future DSPy trainset) from the start. Frozen records use source: curate_example (no enum change).
tags: [evals, phase-2, golden-set, data-governance, contamination, held-out, dspy]
made_by: agent
agent_type: systems-architect
branch: worktree-pipeline-eval-harness
pipeline_tier: standard
affected_files: [src/knotica/evals/golden.py, src/knotica/cli/eval.py]
affected_reqs: [REQ-GOLDEN-01, REQ-GOLDEN-02, REQ-GOLDEN-03, REQ-GOLDEN-04]
dissent: Deferring the trainset/held-out split until Phase 3a (when DSPy actually consumes qa.jsonl) would keep Phase 2 tighter to its scope, at the real risk of retrofitting a split after examples accumulate — which risks leakage that silently contaminates the eval scalar the whole loop trusts.
re_affirms: dec-006
---

## Context

The golden-set cold start is not hypothetical: the only non-empty `qa.jsonl` in the project (`agentic-systems`) holds exactly one record, and it is an *ingest* logged via `curate_example`, not a QA pair (`citations: []`). Phase 2 needs ~20-30 QA pairs/topic for a scalar stable enough to gate keep/discard (research Q4; Anthropic's 20-50 starting floor). Separately, Phase 3a's DSPy optimizer consumes `qa.jsonl` as its **trainset**; if the eval scalar is measured on the same examples DSPy optimized against, the scalar is contaminated. The frozen `QARecord.source` enum is `{curate_example, distillation}`; `distillation` was reserved for the deferred transcript-distillation flywheel.

## Decision

**Bootstrap = synthetic-from-pages + human review-and-freeze.** `knotica eval --bootstrap --topic <t>` has a strong model read each entity page (Summary → Key claims-with-citations → Relations) and emit candidate `(question, reference_answer, supporting_pages/citations)` triples to a **review staging file** (not the golden set). A human reviews/edits/accepts; accepted pairs are frozen into `<t>/.knotica/datasets/golden.jsonl` as `QARecord`s with **`source: curate_example`** — the human review *is* a curation act, so the frozen enum is reused with **no schema change**; synthetic provenance is recorded in the MANIFEST, not the record. A sibling `MANIFEST.json` carries `sha256`, `version` (date/semver), `source`, `split: held_out`, `size`. Floor: `EVAL_MIN_GOLDEN = 20` (a *distinct* constant from `COMPILE_READY_MIN_EXAMPLES`; see the objection in SYSTEMS_PLAN — they count disjoint sets).

**Split held-out NOW (Phase 2):** `golden.jsonl` = the frozen, held-out, eval-scalar set; `qa.jsonl` = the flywheel / future DSPy trainset. They are kept disjoint from the first frozen set. This is data governance (a directory + file convention), **not** a DSPy program — no Phase-3a scope creep. `held_out_delta` (held-out minus any public score) is the contamination signal logged in the per-run manifest.

## Considered Options

### Option A — synthetic-from-pages + human review, held-out split now, source: curate_example (chosen)
- **Pros:** far cheaper than hand-authoring, higher quality than pure synthetic (human gate); split-now prevents retroactive leakage; reuses the frozen enum (no template migration); MANIFEST makes the set content-addressed and reproducible; maps onto SIA's `data/private/` held-out convention.
- **Cons:** human review is a real bottleneck per topic; reusing `curate_example` slightly overloads its meaning (mitigated by MANIFEST provenance).

### Option B — add a `synthetic` value to `QARecord.source`
- **Pros:** honest provenance in the record itself.
- **Cons:** an additive enum change touches the frozen record + root `SCHEMA.md` → a template migration, the exact friction `dec-006` exists to avoid; provenance already lives in the MANIFEST.

### Option C — defer the trainset/held-out split to Phase 3a
- **Pros:** keeps Phase 2 tighter to scope; the split meets its DSPy consumer.
- **Cons:** examples accumulate in the interim; retrofitting a split later risks leakage that silently contaminates the scalar — a correctness failure of the objective function, not a cosmetic one.

## Consequences

- **Positive:** every evaluated topic gets a reproducible, content-addressed, human-vetted held-out set; the eval scalar is contamination-safe by construction before DSPy ever runs; no schema/template churn; `golden.jsonl` is generated per-topic during Phase 2 (not shipped in the template), so the template stays as `dec-006` froze it (empty `qa.jsonl`, no `metrics.jsonl`).
- **Negative:** human review gates every topic's onboarding to eval; `curate_example` now labels both live-curation and reviewed-synthetic records (disambiguated only via the MANIFEST).

## Disconfirmation

- **Falsifier:** if reviewed-synthetic golden pairs turn out to be systematically easier/narrower than real user questions (so the scalar rewards a baseline that does well on synthetic but poorly in practice), the synthetic-from-pages seed was a biased instrument.
- **Steelmanned runner-up (Option C):** DSPy is the only consumer that makes the split *matter*, and it arrives in Phase 3a; splitting now commits to a partition (sizes, which examples are held out) before the trainset's real shape is known, and a Phase-3a split could be done with full knowledge of the accumulated `qa.jsonl`. If leakage can be avoided by a disciplined one-time split at 3a, deferring keeps Phase 2 leaner.
- **Reversal trigger:** revisit if (a) synthetic pairs prove biased vs real questions (add a real-question review lane), (b) the `curate_example` overload causes confusion downstream (then Option B's `synthetic` enum + a `migrate` step), or (c) human review throughput blocks topic onboarding (add a lighter accept-all-with-spot-check mode).

## Prior Decision

Re-affirms `dec-006`: the `QARecord` shape and `QA_SOURCES` enum are unchanged; the golden set reuses the frozen `curate_example` source and introduces no field. `golden.jsonl` + `MANIFEST.json` are new *per-topic generated data* under loop-owned `.knotica/datasets/`, not template files, so the Phase-0 freeze (empty `qa.jsonl`, no shipped `metrics.jsonl`) still holds.
