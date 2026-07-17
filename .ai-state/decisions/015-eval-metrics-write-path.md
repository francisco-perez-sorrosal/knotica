---
id: dec-015
title: metrics.jsonl write path — VaultTransaction on the clone; reproducibility via artifact_ref + harness_version
status: accepted
category: architectural
date: 2026-07-15
summary: Write metrics.jsonl through core.transaction.VaultTransaction on the eval clone (one knotica(eval) commit + log entry), never the live vault. Resolve the MetricsRecord reproducibility gap by encoding a harness fingerprint into harness_version and pointing artifact_ref at a per-run manifest — no schema bump. Extend the import-boundary fitness test to cover evals/.
tags: [evals, phase-2, metrics, vault-transaction, single-writer, reproducibility, fitness-test]
made_by: agent
agent_type: systems-architect
branch: worktree-pipeline-eval-harness
pipeline_tier: standard
affected_files: [src/knotica/evals/harness.py, src/knotica/evals/config.py, tests/test_architecture_boundaries.py]
affected_reqs: [REQ-METRICS-01, REQ-METRICS-02, REQ-METRICS-03, REQ-CORPUS-03]
dissent: Bumping metrics.jsonl schema_version to add first-class dataset_sha / judge_prompt_hash / model_snapshot / cost_usd columns would make every reproducibility field queryable in one flat record, at the cost of a template/constitution migration and breaking the record-schema-freeze promise that Phase 2 is a pure addition.
re_affirms: dec-006
---

## Context

`metrics.jsonl` lives under `<topic>/.knotica/` — a loop-owned vault write. Two questions: (1) *how* it is written under the single-mutation-path invariant and the loops-always-on-a-clone invariant, and (2) *where* the reproducibility columns go. Research verified: `VaultTransaction(store, vault_root, op, topic, title)` takes an already-resolved root with no assumption it is the live vault (a clone is a legitimate target as-is, `transaction.py:124-350`), the commit grammar accepts an `eval` op cleanly (`COMMIT_SUBJECT_RE`), and the append precedent is `curate_example.py:37-89`. But the frozen `MetricsRecord` (`records.py:177-263`) has `{schema_version, topic, timestamp, generation, harness_version, scalar, components{...}, n_examples, corpus_ref("git:…"), artifact_ref|null}` — it **lacks** first-class fields for dataset hash, judge-prompt hash, model snapshots, and cost_usd that a full reproducibility ledger wants (research cross-lens tension #1). Only `corpus_ref` carries a format constraint (`git:` prefix); `artifact_ref` is an unconstrained `str | None`. The fitness test `ADAPTER_PACKAGES = ("cli", "mcp_server")` does not cover `evals/`, so the single-writer invariant would be unenforced on the newest surface.

## Decision

**Write path — VaultTransaction on the clone.** The eval run appends one `MetricsRecord` line to the clone's `<t>/.knotica/metrics.jsonl` via `VaultTransaction(clone_store, clone_root, "eval", topic, "generation N")`, producing exactly one `knotica(eval): <topic> — generation N` commit on the clone and one `log.md` entry (frozen H2 grammar) — mirroring every mutating op. The source vault is never touched (`REQ-CORPUS-03`); the clone's eval commit is returned as a branch per loops-return-branches. A **characterization test is the mandated first implementation step** to validate this reuse (it is inferred from code, not yet observed).

**Reproducibility — fingerprint + manifest, no schema bump.** Resolve the tension without changing the frozen record:
- `harness_version` = a fingerprint hash of `{scalar_formula_version, judge_snapshot, worker_snapshot, judge_prompt_hash, runner_config_hash}` — this is `harness_version`'s intended meaning (the harness identity that must match for two scalars to be comparable).
- `artifact_ref` = the vault-relative path of a **per-run manifest** `<t>/.knotica/eval-runs/gen-<N>/manifest.json`, carrying the full ledger columns the frozen record cannot hold: `dataset_sha` (golden.jsonl hash), `judge_prompt_hash`, `judge_snapshot`, `worker_snapshot`, `T`, `T_target`, `λ`, weights, `token_usage`, `cost_usd`, `held_out_delta`, and per-example scores.

**Fitness test.** Extend `ADAPTER_PACKAGES` to `("cli", "mcp_server", "evals")`. This applies the existing invariant verbatim: `evals/` must not import `subprocess`, not call `write_text_atomic`/`delete`, not call `commit_paths`/`rollback_paths` — all clone mutation flows through `VaultTransaction`. The new `VaultVcs.clone_to` is a read/checkout method (not in `MUTATING_VCS_METHODS`), so `evals/` may call it; no parallel clause is needed.

## Considered Options

### Option A — VaultTransaction-on-clone + harness_version fingerprint + artifact_ref manifest, no schema bump (chosen)
- **Pros:** honors the single-mutation-path and loops-on-a-clone invariants with zero new mutation code; `artifact_ref` was literally designed as the "pointer to a richer per-run artifact" escape hatch; keeps the record-schema-freeze promise (Phase 2 = pure addition); fitness test extends by one tuple element.
- **Cons:** reproducibility columns are one indirection away (in the manifest, not queryable from the flat record); `harness_version` becomes an opaque hash whose inputs live in the manifest.

### Option B — bump `metrics.jsonl` schema_version, add first-class reproducibility columns
- **Pros:** every reproducibility field is directly on the record and queryable without opening a manifest.
- **Cons:** a template/constitution migration + a `migrate` step — the exact friction `dec-006` froze the schema to avoid; commits the record to a wide column set before the ledger's real shape is proven.

### Option C — write metrics.jsonl directly (bypass VaultTransaction), since it is only a clone
- **Pros:** simpler; no transaction ceremony for a disposable clone.
- **Cons:** violates the single-writer invariant and the health guard ("all vault writes through VaultTransaction"); reopens exactly the drift the fitness test exists to prevent; the clone is still a git work tree that deserves the same one-commit-per-op audit trail.

## Consequences

- **Positive:** the newest surface inherits the single-writer discipline for free (one tuple edit); no schema/template churn; full reproducibility is preserved in the manifest and the comparability key (`harness_version`) is a single field; the `eval` commit + log make an eval run a first-class, auditable, reversible op on the clone.
- **Negative:** consumers wanting dataset_sha / cost_usd must read the manifest via `artifact_ref` (a documented indirection); the VaultTransaction-on-clone reuse carries hypothesis risk until the characterization test lands (mitigated by ordering it first).

## Disconfirmation

- **Falsifier:** if `VaultTransaction` on a clone does not, in fact, produce a single clean `knotica(eval)` commit (e.g. the flock path, log append, or rollback assumes live-vault specifics), the reuse hypothesis was wrong and the write path needs a dedicated (non-VaultTransaction) appender — surfaced by the first-step characterization test before any dependent code exists.
- **Steelmanned runner-up (Option B):** an objective-function ledger is queried far more than it is written; making `dataset_sha`, `judge_prompt_hash`, `model_snapshot`, and `cost_usd` first-class columns means a future analysis (or SIA feedback prompt) can select and compare runs without dereferencing a manifest per row, and a one-time `migrate` step is cheap relative to the years of ledger queries it simplifies. The freeze was meant to avoid *accidental* migration, not to forbid a *deliberate, planned* one when the consumer finally exists.
- **Reversal trigger:** bump the schema (Option B) if (a) manifest dereferencing becomes a real friction for analysis/SIA, or (b) `harness_version` as an opaque hash proves too coarse to debug comparability failures, or (c) the per-run manifest and the flat record drift out of sync often enough to warrant one source of truth.

## Prior Decision

Formally re-affirms `dec-006` (record shape frozen — `artifact_ref`/`harness_version` absorb the reproducibility columns, no field added). It also **extends** `dec-008` (single vault-mutation path) by applying it to `evals/` via the fitness test, keeping `core.transaction` the sole writer — an application of that invariant to a new surface, not a re-opening of it. Neither prior decision is superseded.
