---
id: dec-draft-01a7689b
title: Per-task [models] config with fingerprint-folding worker/judge and non-folding query
status: proposed
category: architectural
date: 2026-07-22
summary: Introduce a [models] table (worker=Haiku 4.5, judge=Sonnet 5, query=Sonnet 5); worker/judge fold into harness_version (baseline refreeze on change), query does not; MIPRO proposal LM is inert and gets no key.
tags: [models, eval, harness-version, config, cost, temperature]
made_by: agent
agent_type: systems-architect
branch: worktree-eval-cadence-model-config
pipeline_tier: standard
affected_files:
  - src/knotica/core/models_config.py
  - src/knotica/evals/config.py
  - src/knotica/evals/llm.py
  - src/knotica/cli/eval.py
  - src/knotica/core/query_engine.py
  - src/knotica/mcp_server/tools_query.py
dissent: Downgrading the judge from Opus 4.6 to Sonnet 5 trades a maximally-stable reference grader and temperature-0 determinism for cost; if judge variance inflates, the cost saving will not have been worth the measurement instability.
---

## Context

Every LLM call site today hardcodes one of two packaged constants
(`WORKER_SNAPSHOT="claude-sonnet-4-6"`, `JUDGE_SNAPSHOT="claude-opus-4-6"`); both are now
legacy generations. Operators need per-task model choice with a quality/cost rationale, and a
CLI-flags > config > defaults precedence. The load-bearing invariant: any model identity that
feeds a scored eval must flow through `harness_version` so a swap auto-refreezes the baseline
rather than silently shifting recorded scalars.

Research (2026-07-22, verified live) inventoried the call sites:

- **Fingerprinted eval instrument**: eval worker (`MessagesApiRunner`, `worker_snapshot`) and
  judge (`grade`, `judge_snapshot`) — both already fold into `harness_version` via `HarnessConfig`.
- **Worker-task siblings** sharing `WORKER_SNAPSHOT`: bootstrap/dataset-seeding, compile-student.
- **User-facing, non-instrument**: MCP `query` tool (`answer_question`, currently reuses
  `WORKER_SNAPSHOT`) — never writes `metrics.jsonl`.
- **Inert**: MIPRO teleprompter proposal LM — no `dspy.settings.configure(lm=…)` anywhere and
  `MIPROv2(...)` built with no `prompt_model`/`task_model`; model identity is not traceable.
- **No LLM calls**: arena (delegates to judge), guillotine, gap-classifier, discovery.

Verified ids/pricing: Haiku 4.5 `claude-haiku-4-5-20251001` ($1/$5, accepts `temperature`);
Sonnet 5 `claude-sonnet-5` ($3/$15, introductory $2/$10 through 2026-08-31, **rejects the
`temperature` argument**).

## Decision

Add a **`[models]` config table** resolved by a new `models_config.py` (same idiom as
`gapfill_config.py`) with three optional keys, each falling through to a packaged default:

| Key | Default | Rationale | In `harness_version`? |
|-----|---------|-----------|-----------------------|
| `worker` | `claude-haiku-4-5-20251001` (Haiku 4.5) | High-volume grounded QA — one call per golden question per run at 4 threads. Cheapest capable model ($1/$5); grounded extraction from provided pages needs speed/throughput over deep reasoning. | **Yes** |
| `judge` | `claude-sonnet-5` (Sonnet 5) | Judgment is load-bearing; the reference grader. Sonnet 5 ($3/$15) is a deliberate cost reduction from Opus 4.6 ($5/$25) while keeping frontier judgment. | **Yes** |
| `query` | `claude-sonnet-5` (Sonnet 5) | User-facing single-shot answers; judgment quality matters, but never measured against a baseline. | **No** |

**Precedence**: `config_from_toml.with_overrides(**cli_overrides)` — `resolve_models_config()
.to_harness_base()` builds the `HarnessConfig` base carrying config-sourced worker/judge
snapshots, then `cli/eval.py._resolve_config(base, args)` applies any `--worker-snapshot` /
`--judge-snapshot` on top. Because both funnel through the same `HarnessConfig` fields, a
config-sourced model id folds into `harness_version` exactly as a CLI-sourced one does.

**`query` is resolved separately** and passed to `answer_question`; it is deliberately excluded
from `harness_version` because it never writes to the frozen instrument — folding it would
refreeze baselines for a change that cannot affect any recorded scalar.

**MIPRO proposal LM gets no key** — it is inert (documented as tech debt for a future
verifier/sentinel ledger row, per the brief's "if inert, document, don't invent a key").
bootstrap and compile-student stay on the `worker` key (they are the worker/grounded-QA task).

**`temperature` per-snapshot conditionalization** in `evals/llm.py`: a new predicate
`_snapshot_accepts_temperature(snapshot)` (denylist of temperature-incompatible generations:
Sonnet 5, Opus 4.7+, and later) gates whether `temperature` is added to the Messages request.
Haiku 4.5 and the 4.6 generation keep sending `temperature=0`; Sonnet 5 (the new judge) omits it.

## Considered Options

### Option A — [models] with worker/judge folded, query non-folded, temperature conditionalized (chosen)

- **Pros**: Honors the fingerprint invariant with no structural change to `harness_version`
  (snapshot fields already fold); minimal key set matching real live call sites; CLI precedence
  reuses `with_overrides`; query cost/quality without instrument churn.
- **Cons**: Sonnet 5 judge loses temperature-0 determinism; a denylist must track future model ids.

### Option B — Fold every task model (incl. query, bootstrap) into harness_version

- **Pros**: One uniform rule.
- **Cons**: Refreezes baselines for changes that cannot affect any scored scalar (query/bootstrap
  do not write `metrics.jsonl`) — spurious "instrument changed" churn; wrong by construction.

### Option C — Keep temperature=0 unconditional, pin judge to a temperature-capable model

- **Pros**: Preserves judge determinism.
- **Cons**: Contradicts the settled direction (Sonnet 5 judge); either 400s on Sonnet 5 or forbids
  the chosen judge. Recorded as the reversal path if variance proves unacceptable.

## Consequences

**Positive**: Operator-tunable per-task models with verified cost rationale; the fingerprint
invariant holds automatically (worker/judge changes refreeze; query changes do not); net cost
reduction on both eval legs; installation unchanged (all keys optional).

**Negative**: The Sonnet 5 judge cannot pin `temperature=0`, so judge draws carry model-default
sampling variance — absorbed by the `N_JUDGE_SAMPLES=3` median, and the snapshot change already
rotates `harness_version` so no false regression is reported. The temperature denylist is a
maintenance surface (errs toward sending temperature, so older models stay safe; a 400 on a new
model is loud and a one-line denylist extension). MIPRO proposal remains inert tech debt.

## Disconfirmation

- **Falsifier**: If a `[models]`-sourced worker/judge change does *not* rotate `harness_version`
  (baseline fails to refreeze), or if the Sonnet 5 judge 400s on a `temperature` argument, the
  design is wrong. If setting `[models].query` rotates `harness_version`, the exclusion is broken.
- **Steelmanned runner-up (DI)**: Option C (keep temperature-0, pin a temperature-capable judge).
  The judge is the *reference grader* — the one place measurement stability matters most. Trading
  deterministic temperature-0 draws for a $2/MTok output saving is a poor bargain if it widens the
  scalar's confidence interval enough to flip gate decisions near the baseline. A maximally-stable
  Opus-class judge with pinned temperature is the conservative instrument choice; cost on the judge
  leg is small (one median per question, not the high-volume leg).
- **Reversal trigger**: If judge-sample variance under Sonnet 5 measurably inflates gate flip-rate
  near baselines, re-pin the judge to a temperature-capable model (Option C) and bump the formula
  only if needed.

## Prior Decision

None superseded. The pinned-snapshot constraint prose in `evals/config.py` is updated from the
4.6-generation note (now historical) to the Haiku 4.5 / Sonnet 5 temperature facts.
