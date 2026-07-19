---
id: dec-draft-79dc148d
title: Guillotine becomes verdict + report + gap-filing only; content rewriting moves to the client-approved re-grounding path
status: proposed
category: architectural
date: 2026-07-19
summary: Strip the guillotine's hardcoded, claim-specific replacement text and its in-code regex demotion voice; the guillotine keeps classification, risk scoring, report/diff rendering, and retracted-gap filing, and content rewriting flows through the existing gap→suggestion→approved-ingest path where the client-as-brain writes grounded prose.
tags: [guillotine, client-as-brain, gapfill, demo-content, refactor]
made_by: agent
agent_type: systems-architect
branch: worktree-hackathon-loop-ideas
pipeline_tier: standard
affected_files:
  - src/knotica/guillotine/patch.py
  - src/knotica/core/operations/guillotine.py
dissent: The mechanical demotion produces a polished one-shot rewrite that a human sees and applies immediately; async re-grounding through the queue is slower and may leave a marked-but-unfixed claim visible in the interim.
---

## Context

A whole-system coherence pass over the self-improving autoresearch llm-wiki found demo-era
hardcoding in `guillotine/patch.py`: `_REACT_STYLE_REPLACEMENT` is a claim-specific replacement
paragraph about ReAct, selected by `_looks_react_synergy_claim` (substring match on
`"reasoning-only"` + `"acting-only"`) — fitted to this vault's actual claims (the golden set cites
`reports/guillotine/…react-s-reasoning-acting-synergy…` and `…reasoning-only-systems-hallucinate…`).
This is exactly the demo content CLAUDE.md states was purged elsewhere ("no hardcoded questions /
prompt appendices"). Alongside it, `_demote_text` is a regex table (`are→can be`, `is→may be`,
`hallucinate→can be more vulnerable…`, `proves→suggests`) — a crude in-code editorial voice.

Deterministic server code performing editorial claim-rewriting violates the project's client-as-brain
invariant (the MCP server exposes deterministic tools only; the client LLM does the cognitive work).
The system already owns the correct path: `apply_guillotine` files a `retracted` gap on weakening
verdicts, which flows through discovery → human approval → client-authored grounded re-ingest.
The in-place regex demotion is redundant with, and qualitatively inferior to, that path — and a
mechanical demotion can itself degrade prose enough to trigger a loop regression the system then heals.

Impact on the frozen eval: 3 of 25 golden questions reference guillotine reports, and all three depend
only on the verdict + triage / risk score (e.g. "0/100, KEEP"; "DEMOTE … 55/100"), never on rewritten
page content. The rewriting machinery is severable without touching what the eval measures.

## Decision

Refactor the guillotine to **verdict + report + triage-scoring + gap-filing only**:

1. Remove `_REACT_STYLE_REPLACEMENT`, `_looks_react_synergy_claim`, `_demote_text`, the two
   `_*_REPLACEMENT_TEMPLATE`s, and replacement-prose synthesis in `_replacement_text` /
   `propose_patches`.
2. Keep classification, risk scoring, passage search/localization, report + diff rendering (mechanical
   mark-for-removal / strikethrough of the contested span is retained — mechanical, not editorial),
   the JSON sidecar, and the index bullet.
3. `--apply` files the `retracted` gap (already wired) and may apply a mechanical removal/strikethrough
   of the contested span, but stops synthesizing replacement wording. Re-grounding flows through the
   existing gap→suggestion→approved-ingest path, where the client writes grounded text.

## Considered Options

### Adapt — purge only the ReAct-specific strings, keep mechanical patching
Removes the smoking-gun hardcoding but leaves `_demote_text`'s in-code editorial voice — deterministic
server code still authors prose. The client-as-brain violation persists. Rejected.

### Refactor — verdict + report + gap-filing only (chosen)
Removes ~90 LOC of hardcoding, restores client-as-brain, preserves the frozen eval (report/score
untouched), and folds content-rewriting into the spine that already owns it. Chosen.

### Delete — remove the guillotine entirely
Over-broad: the verdict/triage/report/gap-filing is genuinely valuable, is what the 3 golden questions
measure, and has standalone audit value. Deleting it would break the frozen eval and discard a working
contested-claim adjudicator. Rejected.

## Consequences

Positive: client-as-brain restored; demo-era hardcoding gone; the self-inflicted-regression path from
mechanical demotion disappears; content rewriting consolidates onto the gap-fill spine; the frozen eval
stays green (verdict + score rendering untouched).

Negative: `--apply` no longer emits a polished rewritten paragraph in one shot — the claim is
marked/removed and re-grounded asynchronously through the approval queue, leaving a possible
marked-but-unfixed interim. Tests asserting specific `after` text must be updated or removed.

## Disconfirmation

- **Falsifier:** if a golden question (now or after a future golden refresh) asserts specific rewritten
  *page* content that only the regex demotion produces, or if operators rely on the one-shot polished
  rewrite as a primary workflow, the refactor removes load-bearing behavior and is wrong.
- **Steelmanned runner-up (Adapt):** deleting just the ReAct strings is the smallest diff, keeps the
  familiar one-shot rewrite UX, and a generic (claim-agnostic) demotion table is arguably "mechanical
  enough" to sit in a deterministic tool — reserving the client only for net-new grounded prose.
- **Reversal trigger:** if the client-approved re-grounding path proves too slow or low-throughput in
  practice (weakened claims sit marked-but-unfixed for long stretches), revisit adding a bounded,
  claim-agnostic mechanical demotion back into `--apply`.
