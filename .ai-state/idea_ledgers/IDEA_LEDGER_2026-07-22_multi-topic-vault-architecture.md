# Idea Ledger — Multi-Topic Vault Architecture (2026-07-22)

**Status:** open — flagged for a future dedicated deep-dive task (not scoped into the in-flight
`eval-cadence-model-config` pipeline). **Origin:** user question during that pipeline's execution,
answered from the current codebase; the answer surfaced a real gap worth its own Standard/Full-tier
task with `researcher` + `systems-architect` involvement.

## The question

Can a single vault hold multiple topics beyond the seed `agentic-systems/`? If topics interrelate,
is that handled? When a user wants to run a `knotica` command across every topic rather than one,
is that considered?

## Findings as of this date (grounded in code, not aspiration)

1. **Multiple topics: yes, by design.** The vault layout is topic-rooted (`docs/PRE_PLAN.md` line
   16 area) — each topic is a sibling directory with its own `SCHEMA.md` overlay and `.knotica/`
   state. `knotica status` (`src/knotica/cli/status.py`) and `knotica service`
   (`src/knotica/cli/service.py`) already treat "topics" as a list and aggregate/iterate over all
   configured topics.

2. **Cross-topic relation: schema-level only, no tooling awareness.** `docs/PRE_PLAN.md` line 43
   names "cross-topic linking rules" as one of the root `SCHEMA.md` invariants — wikilinks between
   pages in different topics are permitted and resolve via the root schema. But nothing beyond
   linking exists: no shared embedding space, no cross-topic query fan-out, no merged eval/compile.
   Each topic's DSPy compile artifacts, `qa.jsonl`, and `loop-state.json` are independent,
   topic-scoped files. Relatedness today is a *content* fact (a wikilink), never something a tool
   reasons about across topic boundaries.

3. **"Run on every topic": inconsistent today.** Two patterns coexist, unreconciled:
   - **Aggregate-view commands** (`status`, `service`) already iterate/aggregate all topics.
   - **Per-topic-required commands** (`eval`, `loop`, `gapfill discover`, `datasets bootstrap-train`
     — `--topic` is `required=True`; `compile`, `migrate` — `--topic` optional but single-valued)
     have no `--all-topics` / "no topic means all" convention. A user wanting "eval every topic" or
     "compile every topic" must shell-loop it themselves today.

## Why this deserves a dedicated task, not a quick fix

The gap touches architecture, not just argument parsing:
- Does "all topics" mean sequential or parallel execution? Cadence/rate-limit interaction with the
  eval-cadence work landing in the sibling pipeline matters here (running N topics' evals
  back-to-back could reproduce the same subscription-rate-window collision this pipeline fixes for
  a single topic).
- Does cross-topic linking ever need to become more than passive wikilinks — e.g. should
  `search`/`query` optionally fan out across topics when a query's answer plausibly spans two
  topics? That's a query-semantics decision, not a CLI-ergonomics one.
- Config/state implications: `.ai-state/`, `loop-state.json`, and eval baselines are all
  topic-scoped singularly today; an "all-topics" mode needs a clear story for aggregated reporting
  vs. per-topic isolation, especially once per-task model config (this pipeline) and per-topic
  cadence policy compound.
- Precedent check needed: does `knotica doctor`/`init`/`migrate` already assume single- vs
  multi-topic vaults consistently? Worth an explicit audit before designing the fix.

## Recommended next step

Scope as its own task (`researcher` → `systems-architect`, Standard tier likely) once the current
`eval-cadence-model-config` pipeline merges — that pipeline's cadence/model-config additions are
exactly the kind of per-topic state this task needs to design "all-topics" semantics around, so
sequencing after it avoids redesigning twice.

## Cross-references

- `docs/PRE_PLAN.md` — vault layout, cross-topic linking invariant, topic inference policy.
- `.ai-work/eval-cadence-model-config/` — sibling in-flight pipeline whose per-topic cadence/model
  config this future task should build on, not duplicate.
