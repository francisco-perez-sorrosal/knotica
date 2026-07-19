---
id: dec-029
title: Discovery trigger placement — on-demand primary + opt-in loop-side batch, network off the mandatory heal path
status: accepted
category: architectural
date: 2026-07-19
summary: Gap-fill discovery is triggered on-demand (knotica gapfill discover CLI / MCP) as the always-available primary path, with a config-gated opt-in loop-side batch trigger (default off) that shares the same drain function; the committed gap queue is the durable buffer, so deferring discovery loses no data, and the offline-deterministic loop watcher is not forced to depend on outbound network, a credential, or a cost budget on its mandatory heal path — the loop-side path, when enabled, is failure-isolated and fixed-budget-capped (one drain per regression event, max_gaps queries, dedup gate).
tags: [gapfill, phase-p3, discovery-trigger, loop, client-as-brain, fixed-budget, failure-isolation, best-effort, dec-013, dec-014]
made_by: agent
agent_type: systems-architect
branch: worktree-hackathon-loop-ideas
pipeline_tier: standard
affected_files:
  - src/knotica/core/gapfill.py
  - src/knotica/cli/gapfill.py
  - src/knotica/core/loop.py
affected_reqs: [REQ-01, REQ-02, REQ-08]
dissent: Loop-side automatic default-on is the purest "wiki that researches itself" — a regression IS the trigger, DiscoveryService is deterministic-REST/no-LLM so it violates no invariant, and the same try/except isolation the classifier uses already contains a discovery failure; the opt-in flag hides the flagship capability behind a config toggle.
---

## Context

P3 must decide who fires discovery and when. `DiscoveryService` is deterministic REST with no LLM, so a
headless or loop-side invocation does **not** violate client-as-brain (unlike ingest, dec-014) — the brief and
SYNTHESIS both confirm this. But three facts pull against inline-mandatory loop-side discovery: (1) the loop
watcher's load-bearing invariant is offline determinism (git clone + eval, no network); (2) discovery requires
a credential (`KNOTICA_YOUCOM_API_KEY`) present in the headless env and consumes a rate/cost budget; (3) the
autoresearch literature the brief cites shows low accept rates (~6.6 rounds/accept), so per-question firing is
wasteful. Crucially, P1 already commits `GapRecord`s durably *before* any discovery runs — so the committed gap
queue is a buffer and deferring discovery loses no data.

## Decision

**On-demand is the primary, always-available trigger:** `knotica gapfill discover --topic <t>` (CLI) drains the
committed gap queue, formulates deterministic queries, runs discovery, and stages suggestions. It keeps
outbound network entirely off the loop's mandatory path.

**A loop-side batch trigger is offered opt-in, default off** (`[gapfill] discover_on_regression = false`): when
enabled, the loop calls the *same* `refresh_suggestions_for_gaps` drain in the regression tick, immediately
after `write_gap_records`, wrapped in the classifier's failure-isolation `try/except` (a discovery failure is a
logged no-op; the heal proceeds) and writing a **separate** `VaultTransaction` (never piggybacked on the
gap-record commit — dec-008).

**Fixed-budget defense (concrete):**
- Batch granularity = one drain per regression *event* (per `_maybe_redirect_to_gaps` call), never per-question.
- Call cap = `max_gaps` (default 5): the top-`max_gaps` open `genuine_gap`s by `|quality_delta|` get queries;
  one gap ⇒ one `discover` call ⇒ 1 provider search + ≤1 batched OpenAlex enrich (≤50 DOIs/request, P2).
- Dedup gate: a gap already carrying a suggestion for a candidate source is skipped — a persistent regression
  never re-spends budget.

`dilution` gaps are never drained (P1 contract #3 — P4-quarantine input). Both triggers filter
`fault_class == genuine_gap AND status == open`.

## Considered Options

### Option 1 — Loop-side automatic, default on
Every regression tick fires discovery inline.
- Pros: the strongest autonomous-demo beat (candidates staged before the human wakes); no invariant violated
  (deterministic REST); one obvious trigger.
- Cons: makes a headless watcher's *normal* path depend on outbound network + a credential + a cost budget; a
  key-less host logs a discovery failure every regression; couples the offline heal loop to an external API.

### Option 2 — On-demand only
The loop only writes gaps; discovery is a wholly separate CLI/MCP action.
- Pros: maximal loop robustness; zero network coupling.
- Cons: the "researches itself" story requires a manual step; weaker demo.

### Option 3 — On-demand primary + opt-in loop-side batch, default off (chosen)
One shared drain; CLI is the floor; the loop calls it only when explicitly enabled.
- Pros: offline-robust + credential-free by default; discovery cost opt-in and capped; one code path, one test
  surface; the autonomous story is a documented one-line flag flip.
- Cons: two trigger entry points to test; the flagship demo needs the flag on.

## Consequences

- Positive: the loop stays offline-deterministic by default; discovery coupling is a deliberate operator choice;
  the committed gap queue is the durable buffer so nothing is lost by deferral; budget is bounded when enabled.
- Negative: the fully-hands-off demo requires flipping `discover_on_regression` (documented in the CLI help +
  demo path); a small extra config surface.
- Neutral: both triggers converge on one function, so behavior is identical regardless of who fires it.

## Disconfirmation

- **Falsifier:** If gap records were not durably committed before discovery (so deferring could drop a gap),
  on-demand-primary would be wrong and inline-mandatory necessary. P1 commits gaps first — the premise holds.
- **Steelmanned runner-up (Dialectical Inquiry):** Loop-side automatic default-on is the purest expression of
  the thesis — a regression *is* the proposer's trigger, and a human finding reputable candidates already staged
  is the demo's flagship moment. DiscoveryService's deterministic-REST/no-LLM nature means it breaks no
  client-as-brain invariant, the classifier's existing try/except already contains any failure to a logged
  no-op, and the fixed-budget cap already bounds cost — so the network-coupling objection is largely
  neutralized, and the opt-in flag arguably just hides the capability. Genuinely close.
- **Reversal trigger:** Once the loop reliably runs with a provisioned key and the isolation + budget cap prove
  quiet in practice, flip the default to on — a one-line config-default change, no schema or code change.
