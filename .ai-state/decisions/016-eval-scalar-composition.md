---
id: dec-016
title: Eval scalar — hinged budget-relative multiplicative cost penalty; citation validity deterministic-only
status: accepted
category: architectural
date: 2026-07-15
summary: Compose the per-topic eval scalar as quality · (1 − λ·max(0,(T−T_target)/T_target)), a hinged, budget-relative, multiplicative cost discount over a dimensionless [0,1] quality composite (QA accuracy + citation validity + lint cleanliness). Citation validity is deterministic integrity only for v1; judge-based faithfulness is deferred. Revises the illustrative additive formula noted in the record-schema-freeze ADR without changing the frozen record shape.
tags: [evals, phase-2, scalar, objective-function, cost-penalty, citation-validity, metrics]
made_by: agent
agent_type: systems-architect
branch: worktree-pipeline-eval-harness
pipeline_tier: standard
affected_files: [src/knotica/evals/scalar.py, src/knotica/evals/scorer.py, src/knotica/evals/citations.py]
affected_reqs: [REQ-SCALAR-01, REQ-SCALAR-02, REQ-SCALAR-04]
dissent: Additive quality − λ·cost is simpler to read and is the shape the PRE_PLAN §Model policy and dec-006 sketched; it is defensible if every term is carefully unit-matched, but it is the formulation most prone to the cost penalty swamping the quality signal when token scale and quality are not commensurable.
re_affirms: dec-006
---

## Context

The success criterion is "a stable scalar on the frozen corpus," and the scalar will later be *optimized against* (DSPy) and *gated on* (SIA keep/discard). PRE_PLAN §Model policy describes the scalar as "QA accuracy + citation validity + lint violations − token-cost penalty," and `dec-006` (record-schema-freeze) restated an illustrative additive form `scalar = qa_accuracy + citation_validity − lint_violation_penalty − token_cost_penalty`. The research (Q6) surveyed cost-penalty precedents (Lagrangian `R−λC`; CATP-LLM compute-adjusted fitness; overthinking penalties) and found additive absolute penalties can swamp quality when `C` and `R` are not commensurable. The `MetricsRecord` shape is **frozen** (`components{qa_accuracy, citation_validity, lint_violations, token_cost}`) — but *how the scalar is computed from those components* is not part of the frozen field contract. Separately, citation validity splits into cheap deterministic integrity (a `CITATION_UNRESOLVED` lint check already exists, `dec-011`) and subjective judge-based faithfulness.

## Decision

**Scalar = hinged, budget-relative, multiplicative** (`scalar_formula_version = 1`):

Per-example quality (this is `score(example, prediction, None)`, the DSPy metric):
`q_i = w_qa·qa_accuracy_i + w_cite·citation_validity_i`  (`w_qa + w_cite = 1`; v1 `w_qa=0.7, w_cite=0.3`).

Topic scalar:
`lint_cleanliness = max(0, 1 − lint_violations/L_ref)` (`L_ref = max(1, n_content_pages)`);
`Q = (1−w_lint)·mean_i(q_i) + w_lint·lint_cleanliness` (v1 `w_lint=0.15`);
`cost_factor = clamp(1 − λ·max(0,(T − T_target)/T_target), 0, 1)` (v1 `λ=0.3`);
`scalar = Q · cost_factor ∈ [0,1]`.

`T` = per-item median total tokens for the scored generation (size-independent, outlier-resistant). `T_target = τ·median_i(T_i^gen0)` (v1 `τ=1.3`), frozen at generation 0 and stored in the topic manifest. Every constant is a packaged default, CLI-overridable, recorded per-run in the manifest, and a change to the *shape* bumps `scalar_formula_version`. Store `cost_usd` separately in the manifest for reporting; keep **tokens** (not USD) inside the scalar for cross-generation stability.

**Citation validity = deterministic integrity only** for the Phase-2 scalar: reuse the `CITATION_UNRESOLVED` logic (fraction of a baseline answer's citations that resolve to stored sources). **Judge-based faithfulness is deferred** (an explicit in/out decision — out for v1) to avoid the added judge cost + κ-calibration burden.

Three properties defeat swamping: **hinge** (`max(0,…)` — no penalty at/under budget, no bonus for degenerate terse answers), **budget-relative** (`÷T_target` — dimensionless, side-steps the cross-model tokenizer-count mismatch), **multiplicative** (`×Q` — a cheap low-quality answer cannot buy score). `λ∈[0,1]` bounds the maximum discount so cost can shade but never dominate.

## Considered Options

### Option A — hinged budget-relative multiplicative; citation deterministic-only (chosen)
- **Pros:** dimensionless and bounded → stable/comparable across schema/prompt changes that alter output length; robust under optimization (no swamping); citation integrity is cheap, deterministic, and already implemented; maps onto the frozen components with no schema change.
- **Cons:** more moving parts than a plain sum; requires a per-topic `T_target` seeded at generation 0; v1 weights/λ are principled but empirically untuned; deterministic-only citation misses faithfulness (a claim can cite an existing-but-unsupporting source).

### Option B — additive `quality − λ·cost` (the PRE_PLAN/e5cf9cf1 sketch)
- **Pros:** simplest to read; matches the illustrative formula already written; one weighted sum.
- **Cons:** most prone to swamping when token scale ≫ quality scale; demands careful unit-matching; a degenerate terse answer can score well on the cost term.

### Option C — include judge faithfulness in citation validity now
- **Pros:** catches "cites an existing source that doesn't support the claim" — a real failure the deterministic check misses.
- **Cons:** adds judge cost + a κ-calibration burden before the scalar is even stable; premature for v1 whose bar is *stable*, not *maximally discriminating*.

## Consequences

- **Positive:** the scalar is a robust objective function ready to be optimized/gated; no `MetricsRecord` schema bump; the additive form's swamping risk is designed out; deterministic citation integrity gives DSPy/SIA a concrete, cheap reward signal immediately.
- **Negative:** the frozen `token_cost` component now holds a *factor* (the applied discount multiplier ∈[0,1]), a semantic clarification of the field (name/type unchanged); v1 weights/λ need empirical calibration before they gate keep/discard confidently; faithfulness gaps are a known, accepted v1 limitation.

## Disconfirmation

- **Falsifier:** if, on a real golden set, the multiplicative form produces a scalar whose ranking of generations disagrees with human judgement of "better wiki" more often than a tuned additive form would — or if `cost_factor` dominates because `T_target` is systematically mis-seeded — the multiplicative choice was wrong for this domain.
- **Steelmanned runner-up (Option B):** knotica's quality terms are already normalized to `[0,1]`, so the additive form's swamping risk is largely defused before it starts; `quality − λ·cost` with a small `λ` is transparent, trivially explainable to a human reviewing keep/discard, and one fewer nonlinearity to reason about when debugging a surprising scalar. If the components stay bounded, additive may be the more legible objective.
- **Reversal trigger:** revisit (bump `scalar_formula_version`) if (a) calibration shows the multiplicative form mis-ranks generations vs human judgement, (b) `T_target` seeding proves unstable across topics, or (c) deterministic-only citation validity lets unsupported-but-resolvable citations pass often enough to warrant adding the faithfulness judge.

## Prior Decision

Re-affirms `dec-006` for the **record shape**: `MetricsRecord` / `MetricsComponents` fields are unchanged and remain frozen. This ADR revises only the *illustrative scalar formula* that e5cf9cf1 restated from PRE_PLAN §Model policy — that formula was a description of components, not part of the frozen field contract. The authoritative scalar composition is defined here; e5cf9cf1's freeze of the record fields stands.
