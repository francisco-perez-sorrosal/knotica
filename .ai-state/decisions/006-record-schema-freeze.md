---
id: dec-006
title: Freeze machine-record schemas at Phase 0
status: accepted
category: architectural
date: 2026-07-03
summary: Freeze qa.jsonl, metrics.jsonl, log-entry, commit-message, and source-provenance record shapes now (each with schema_version), documented in root SCHEMA.md, so Phase 2/3 consumers need no template migration.
tags: [schema, records, flywheel, evals, dspy, sia, phase-0, forward-compat]
made_by: agent
agent_type: systems-architect
branch: pipeline-wiki-mvp-core
pipeline_tier: standard
affected_files: [vault-template/SCHEMA.md, vault-template/agentic-systems/.knotica/datasets/qa.jsonl, src/knotica/core/records.py]
affected_reqs: [REQ-REC-01, REQ-REC-02, REQ-REC-03, REQ-TOOL-04]
dissent: Deferring metrics.jsonl and qa.jsonl shapes until their Phase-2/3 consumers exist would let the schemas be designed against real usage, at the cost of a template migration and a rewrite of already-curated examples.
re_affirmed_by: [dec-016, dec-015, dec-018, dec-023]
---

## Context

Phase 0 authors the vault template deliberately software-free, but the flywheel (`qa.jsonl`) starts
filling in **Phase 1** (`curate_example`), and Phase 2 (evals → `metrics.jsonl`) and Phase 3 (DSPy
trainsets from `qa.jsonl`, SIA reading `metrics.jsonl`) consume these files. If the record shapes are not
fixed now, a later phase forces a **template migration** and a rewrite of already-curated real examples —
exactly the friction the schema-versioned constitution exists to avoid.

## Decision

Freeze the following record shapes now, document them in **root `SCHEMA.md`** (the versioned
constitution `migrate` governs), and give each record its own `schema_version` for additive forward
evolution. Ship an **empty** `datasets/qa.jsonl` in the topic template (Phase-1 `curate_example` appends
immediately); do **not** ship `metrics.jsonl` (its producer is Phase 2 — absence = "not yet evaluated").

**`qa.jsonl`** (per-topic `.knotica/datasets/qa.jsonl`, one JSON object per line):
`id, schema_version, topic, created, query, pages_used[], answer, citations[], verdict
(good|bad|corrected), corrected_answer|null, source (curate_example|distillation), model`.
Phase-3a DSPy trainset = records with `verdict ∈ {good, corrected}` (gold = `corrected_answer` when
present else `answer`); `bad` retained for analysis.

**`metrics.jsonl`** (per-topic `.knotica/metrics.jsonl`, Phase-2 producer):
`schema_version, topic, timestamp, generation (0=baseline), harness_version, scalar,
components{qa_accuracy, citation_validity, lint_violations, token_cost}, n_examples,
corpus_ref (git:<sha>), artifact_ref|null`. Scalar formula (locked, PRE_PLAN §Model policy):
`scalar = qa_accuracy + citation_validity − lint_violation_penalty − token_cost_penalty`.

**`log.md` entry:** H2 `## [YYYY-MM-DD] <op> | <topic> | <title>` + optional touched-pages bullets.
**Commit message:** `knotica(<op>): <topic> — <title>` (one commit per op; op→commit index is `git log`).
**Source provenance frontmatter** (`sources/<topic>/<citation-key>.*`):
`type: source, topic, citation_key, retrieved (ISO), origin_url, sha256, ingested_by`.

## Considered Options

### Option A — freeze all record shapes at Phase 0 (chosen)
- **Pros:** no future template migration; already-curated examples stay valid; `migrate` is the single
  evolution lever; consumers (evals/DSPy/SIA) code against a stable contract.
- **Cons:** commits to field sets before some consumers exist — risk of a wrong/missing field.

### Option B — freeze only qa.jsonl + log/commit now; defer metrics.jsonl to Phase 2
- **Pros:** metrics designed against real eval usage.
- **Cons:** Phase 2 still touches the template/constitution; a half-frozen constitution is a confusing
  single source. Chosen middle ground: document metrics **shape** now but ship no file.

### Option C — defer all record schemas to their consuming phase
- **Pros:** every schema meets its consumer first.
- **Cons:** guarantees a template migration and a rewrite of Phase-1-curated examples; contradicts the
  schema-versioned-constitution design.

## Consequences

- **Positive:** Phase 2/3 are pure additions, not template migrations; the flywheel substrate and the UX
  surface are one versioned artifact; per-record `schema_version` makes evolution auditable.
- **Negative:** a mis-designed field surfaces only when its consumer lands — mitigated by additive-only
  evolution (new optional fields, never breaking renames) under `migrate`.

## Disconfirmation

- **Falsifier:** if Phase 2/3 need a **breaking** change to a frozen field (a rename or type change, not an
  additive field), the freeze was premature for that field.
- **Steelmanned runner-up (Option C):** schemas are cheapest to get right against a real consumer; deferring
  metrics.jsonl until the evaluator exists would let its component breakdown match the actual scalar
  computation instead of a guessed one.
- **Reversal trigger:** if a Phase-2/3 consumer needs a breaking field change, bump the record
  `schema_version` and add a `migrate` step — the versioning is precisely the escape hatch.
