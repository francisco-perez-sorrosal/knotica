---
id: dec-draft-fc4cf526
title: A truncated judge sample is mitigated three ways rather than diagnosed
status: proposed
category: behavioral
date: 2026-08-09
summary: The judge's budget rises 512 to 2048 with a 4x retry, an unusable sample is dropped instead of failing the run, and max_tokens truncation is retryable when the snapshot cannot pin its sampling — the cause of the overrun itself is measured but unexplained.
tags:
  - eval
  - judge
  - harness
  - reliability
  - retry-backoff
  - unexplained-cause
made_by: agent
agent_type: orchestrator
branch: main
pipeline_tier: standard
affected_files:
  - src/knotica/evals/judge.py
  - src/knotica/evals/llm.py
  - src/knotica/evals/config.py
---

## Context

Four of seven identical-corpus eval runs on `decision-making` aborted with `EvalRunError`, each on a *different* golden example. Not the golden set, not the corpus — a judge response truncated at `JUDGE_MAX_TOKENS = 512`, carrying no parseable score. One unscored example fails the run.

**The obvious cause was tested and refuted.** The judge snapshot is `claude-sonnet-5`, `complete()` sends no `thinking` field, and Sonnet 5 documents adaptive thinking as the default when that field is absent — with `max_tokens` capping thinking and answer together. That is a clean story and it is wrong. Two direct probes:

| request | output tokens |
|---|---|
| exact-match grade, no thinking field, 512 budget | 44 |
| exact-match grade, thinking disabled, 2048 budget | 46 |
| partial match vs 502-char reference, no thinking field, ×3 | 95 / 98 / 97 |
| partial match vs 502-char reference, thinking disabled, ×3 | 112 / 98 / 105 |

Typical spend is ~100 tokens — the 512 ceiling was already 5× that. Disabling thinking changes nothing measurable and is marginally *more* verbose. Whatever the default does on this workload, it is not spending the budget: grading against a fixed rubric is shallow enough that adaptive thinking barely engages.

So the overruns remain **unexplained**. The manifest does not retain candidate answers, so the pathological inputs from those runs are not recoverable after the fact.

## Decision

Mitigate in layers rather than assert a cause:

1. **`JUDGE_MAX_TOKENS` 512 → 2048** — ~20× measured spend, and headroom for a case that does engage thinking.
2. **`JUDGE_RETRY_MAX_TOKENS` at 4×** — one retry per sample, for truncation *or* an unparseable score.
3. **A sample that fails twice is dropped, not fatal.** With three samples, two survivors still bracket the score. The odd-`n` guarantee that the median is a real drawn sample degrades; losing the run is strictly worse.
4. **`max_tokens` truncation is retryable when the snapshot could not pin its sampling.** Sonnet 5 rejects `temperature`, so the client omits it and identical calls draw different lengths (95/98/97 measured). The old blanket `retryable=False` was justified by "the calls are `temperature=0`" — a premise that stopped being true when the judge moved to a temperature-rejecting snapshot, and which fed the loop's hour-long non-retryable backoff floor.

**Only truncation and unparseable scores are absorbed.** Auth rejections, rate limits, and transport errors propagate — a typed `LLMIncompleteResponseError` makes that distinction structural rather than message-matched. When *every* sample fails it is still an instrument failure.

**Nothing rotates `harness_version`.** A budget ceiling bounds how long a response may be; it does not change what the judge writes within the bound. An earlier revision of this change disabled judge thinking and folded the sampling mode into `JUDGE_PROMPT_HASH` — correct *if* the change altered scores, but the measurements above show it does not, and it would have retired every baseline in every topic for nothing.

`config.py`'s standing claim that "a request that omits `thinking` runs without thinking on all currently pinned generations" is corrected in the same change: it is false for Sonnet 5, and the correction records what was measured alongside what is documented.

## Considered Options

### Layered mitigation without a root cause (chosen)

- Prevents the failure three independent ways; no baseline churn; honest about what is unknown.
- Leaves a real cause unfound. If it is something that scales — a pathological candidate class — 2048 buys time, not immunity.

### Disable judge thinking (the first attempt)

- Principled on its face: a bounded classification does not need reasoning tokens.
- Measured to change nothing, and rotates `harness_version`, retiring every baseline. Rejected on evidence after being implemented.

### Raise the budget only

- Smallest possible change.
- Leaves one bad sample fatal, and leaves the retry floor mis-paced. The budget is the least certain of the three fixes precisely because the cause is unknown.

### Give the judge a structured-output schema

- Would guarantee a parseable score field; the client already supports `output_config.format`.
- Does not stop a truncation, only changes what the truncated bytes look like. Worth doing separately, and it *would* rotate the instrument.

## Consequences

**Positive.** A recurrence costs one sample rather than 21 questions of measurement. Truncation paces correctly through the retry backoff. Baselines are untouched — no re-eval forced on any topic. The refuted hypothesis is recorded so nobody re-runs it.

**Negative.** The cause is still open, so this is defense in depth over a fault that could recur in a form the layers do not cover. Dropping a sample can leave an even survivor count, so the median may average two samples instead of being a drawn one. And a genuinely pathological candidate would now be silently down-weighted rather than loudly failing — the log line is the only signal.

## Disconfirmation

**Falsifier.** Judge responses truncate at 2048 with typical spend still ~100 tokens — that would mean the overrun is not a gradual length problem at all but something discontinuous (a runaway generation, a malformed candidate), and headroom is the wrong shape of fix.

**Steelmanned runner-up.** Structured outputs. If a schema-constrained judge cannot emit an unparseable score at all, most of this machinery is unnecessary, and the instrument rotation it costs is a one-time price for a stronger guarantee.

**Reversal trigger.** A second unexplained truncation cluster after this ships, or evidence that dropped samples are correlated with a particular question or candidate shape rather than randomly distributed.
