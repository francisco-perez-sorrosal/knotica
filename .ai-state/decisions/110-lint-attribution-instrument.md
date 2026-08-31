---
id: dec-110
title: One lint-attribution rule for both counters, bumping the scalar formula to v2
status: accepted
category: behavioral
date: 2026-08-30
summary: core.lint.topic_of_violation is THE per-topic counting rule shared by the eval harness and wiki_status; the harness change is an instrument change, so SCALAR_FORMULA_VERSION bumps 1→2 and the existing cross-instrument gate machinery absorbs it
tags: [lint, eval, scalar, instrument, status, honesty]
made_by: agent
agent_type: orchestrator
branch: main
pipeline_tier: lightweight
affected_files:
  - src/knotica/core/lint.py
  - src/knotica/core/status.py
  - src/knotica/evals/harness/
  - src/knotica/evals/scalar.py
---

# One lint-attribution rule for both counters, bumping the scalar formula to v2

## Context

A field report showed `wiki_status` reporting `lint_violations: 0` for a topic
while the same generation's eval recorded `12.0` as a scalar component — and
until resolved, the scalar's correctness was itself in doubt. The disagreement
was structural, not corpus drift: both surfaces call the same `lint_vault`, but
the harness counted *everything its topic-scoped run returned* (vault-level
findings on `log.md`, `index.md`, the root schema included), while the status
walk bucketed a whole-vault run by first path segment — dropping vault-level
findings entirely, filing `sources/<topic>/…` under a non-topic, and (scoped)
attributing root findings to the scope topic.

## Decision

- `core.lint.topic_of_violation(path)` is the single attribution rule: a topic
  owns findings under its directory and under `sources/<topic>/`; everything
  else is vault-level and belongs to no topic's count.
- The harness counts only topic-attributable findings into the scalar's
  `lint_violations` input; `wiki_status` buckets with the same rule and reports
  the vault-level remainder as `totals.lint_violations_vault_level` instead of
  letting it vanish.
- The harness change alters what one scalar input measures — an **instrument
  change** — so `SCALAR_FORMULA_VERSION` bumps 1→2 (the formula expression is
  unchanged; the version comment records exactly that). The bump rotates
  `harness_version`, so pre-v2 scalars read as cross-instrument (gate
  "unknown", instrument-change re-freeze) rather than being compared silently.

## Considered Options

### Shared attribution + formula bump (chosen)

- Pro: one rule, two consumers, disagreement becomes impossible by
  construction; the instrument discipline that already exists absorbs the
  semantic change honestly.
- Con: every topic's next eval re-freezes its baseline (expected, one-time).

### Make the harness count everything and status match it

- Rejected: a root-schema glitch would shade *every* topic's cleanliness score
  for defects no page of the topic carries, and a baseline frozen under
  different vault-level state makes gate comparisons noisy with out-of-topic
  signal.

### Leave the counters divergent and document the difference

- Rejected: the scalar is a gate input; a number whose meaning depends on which
  surface you read is exactly the doubt the field report raised.

## Consequences

- The two surfaces can no longer disagree structurally; live-vs-frozen-clone
  remains the only legitimate difference and is visible via `corpus_ref`.
- Vault-level findings are now reported (new totals key) rather than silently
  dropped between buckets.
