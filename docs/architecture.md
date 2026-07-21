# Architecture Guide

<!-- Developer navigation guide. Every component name and file path here is verified against the codebase;
     only components that exist on disk are listed as Built. Design rationale + planned components live in
     .ai-state/DESIGN.md; the converged design lives in docs/PRE_PLAN.md.
     Created by systems-architect; updated by implementer; verified by doc-engineer at checkpoints. -->

> **Status: MVP + Phase-2 `evals/` + Phase-3a `programs/`/compile/loop Built (2026-07-18).** The `store/`,
> `search/`, `core/`, `mcp_server/`, `cli/`, `evals/`, and `programs/` packages plus the autonomous
> `knotica loop` watcher, the cold-start dataset bootstrap, the plugin layer, and the dashboard MCP App
> are on disk. Outer-loop `agent/` (SIA / Phase 3b) remains Planned. For design rationale, read
> [`.ai-state/DESIGN.md`](../.ai-state/DESIGN.md); for the full design, [`docs/PRE_PLAN.md`](./PRE_PLAN.md).
> End-user Desktop install: [`docs/CLAUDE_DESKTOP.md`](./CLAUDE_DESKTOP.md) (headless `query`/compile/eval
> credentials: [Headless LLM credentials](./CLAUDE_DESKTOP.md#headless-llm-credentials-query--compile--eval)).

## 1. Overview

| Attribute | Value |
|-----------|-------|
| **System** | Knotica — LLM-Wiki MVP |
| **Type** | Stateless MCP server + CLI over a versioned Obsidian vault; Claude plugin marketplace |
| **Language / Framework** | Python 3.12+ (uv) / official `mcp` SDK (`FastMCP`) |
| **Architecture pattern** | Hexagonal, single-mutation-core |
| **Last verified against code** | 2026-07-18 — MVP tree + Phase-2 `evals/` + Phase-3a `programs/`/compile/loop Built (`store/`, `search/`, `core/` incl. `core.loop`/`core.loop_state`/`core.loop_heartbeat`/`core.loop_progress`, `mcp_server/`, `cli/` incl. `cli.loop`, `evals/` incl. `evals.train_bootstrap`, `programs/` + the plugin layer); `agent/` (SIA outer loop) Planned (Phase 3b) |

Knotica is an AI-maintained markdown wiki in an Obsidian vault. The **Claude client's LLM is the brain**;
the server exposes deterministic tools and holds no session state. Every vault mutation flows through a
single `VaultTransaction` (flock + atomic write + log append + secret-scrub + one git commit).

## 2. System Context

<!-- TODO(diagram): render docs/diagrams/architecture/rendered/context.svg from the LikeC4 source
     at docs/diagrams/architecture/src/architecture.c4 (render command in the .c4 header). -->
Diagram source: `docs/diagrams/architecture/src/architecture.c4` (rendered SVG pending). Actors:
User → Claude client (Code/Desktop) and Obsidian; the Claude client → the knotica MCP server / CLI;
knotica → the vault git repo. Deployment is out of scope (local-only Phases 0–3).

## 3. Components

**Built components (MVP tree):**

| Component | Responsibility | Path (verified on disk) |
|---|---|---|
| `store/` | `VaultStore` protocol + `LocalFSStore` — atomic (temp+rename) storage primitives; no git/log/schema knowledge | `src/knotica/store/` |
| `search/` | `SearchBackend` protocol + `RipgrepBackend` — read-only full-text search with cursor paging | `src/knotica/search/` |
| `core/` | Vault semantics: `config`, `schema`, `page`/`links`, `lint`, `vcs`, `lock`, `scrub`, `records`, `template` (read-only packaged-template locator), `transaction.VaultTransaction`, the four `operations.*` writes, and the loop spine (`loop.LoopRunner` — observe/gate/heal; `loop_state` — persisted `LoopState`/`LoopStage`/`LoopDecision`; `loop_heartbeat` — runner-liveness file under `.knotica/locks/`; `loop_progress` — in-flight per-question eval progress, same locks dir). Operations are config-agnostic — `(store, vault_root, *semantic_args)`, resolving config only at the adapter boundary | `src/knotica/core/` |
| `mcp_server/` | FastMCP adapter: read tools, mutating tools, resources, and prompts. Resolves config per call; delegates every mutation to `core.operations.*`; never writes the vault directly | `src/knotica/mcp_server/` |
| `cli/` | `knotica` console entry point — self-registering subcommand registry (`init`/`mcp`/`doctor`/`status`/`migrate`/`prompt`/`guillotine`/`okf`/`eval`/`compile`/`datasets`/`loop`). Reads via `core` read functions; mutations only through `core.operations.*`; never writes the vault directly. `loop` (`cli/loop.py`) owns the watch/once/set-baseline entry to `core.loop.LoopRunner`, plus the heartbeat thread | `src/knotica/cli/` |
| `evals/` | Frozen-corpus evaluator (Phase 2, headless `knotica eval`): clones the vault at a pinned SHA, scores a topic's held-out golden set through `dspy.Evaluate` over a baseline runner + cached LLM-as-judge, composes one stable scalar, and appends a `MetricsRecord` to the *clone's* `metrics.jsonl` through `core.transaction` — never the live vault. `--bootstrap` stages synthetic golden candidates for human review (never auto-frozen). `run_eval(..., on_example=, on_substage=)` progress seams feed `core.loop_progress`. `train_bootstrap.bootstrap_trainset` cold-starts a fresh topic's `qa.jsonl` from its own entity pages (LLM-grounded, `source: seed_train`; displaced by curated records over time). `anthropic`+`dspy` are isolated in the `evals` dependency group, off the MCP launch path | `src/knotica/evals/` |
| `programs/` | Phase 3a DSPy query compile (MIPROv2 with bootstrap fallback) → JSON compiled artifact (`optimizer`/`fallback_reason` recorded on fallback; never fabricates a compile score without LLM credentials) + `CompiledRunner`; selected by `query_engine` behind the single MCP `query` tool | `src/knotica/programs/` |
| Dashboard | Single-file Preact MCP client: MCP App (`ui://knotica/dashboard` + `open_dashboard`) and HTTP mount (`knotica mcp --http`) | `dashboard/`, `src/knotica/dashboard/`, `src/knotica/mcp_server/app_ui.py` |
| Plugin layer | Claude plugin marketplace surface: manifests, ten `/knotica:*` command aliases (`commands/*.md`, incl. `/knotica:loop`), SessionStart pre-warm hook, the maintenance skill, and the MCP server registration | `.claude-plugin/`, `commands/`, `hooks/`, `skills/wiki-maintenance/`, `.mcp.json` |

The single-writer boundary (adapters never mutate the vault; the sole writer is `core.transaction`) is
enforced statically by `tests/test_architecture_boundaries.py`; `evals/` and compile route writes through
`core.transaction` on a clone. The full module map lives in
[`.ai-state/DESIGN.md` § 3](../.ai-state/DESIGN.md#3-components).

Navigation:
- Vault mutation logic → `src/knotica/core/` (`transaction.py`, `operations/` — one module per op) — the single writer.
- Storage backend → `src/knotica/store/` (`VaultStore` protocol + `LocalFSStore`).
- Full-text search → `src/knotica/search/`.
- MCP server (tools/resources/prompts) → `src/knotica/mcp_server/` (named to avoid shadowing the `mcp` SDK package; see `dec-009`).
- CLI → `src/knotica/cli/` (`init`/`mcp`/`doctor`/`status`/`compile`/`datasets`/`eval`/`loop`/…).
- Eval harness → `src/knotica/evals/`; compile programs → `src/knotica/programs/`; cold-start bootstrap →
  `src/knotica/evals/train_bootstrap.py`.
- Autonomous loop → `src/knotica/core/loop.py` (spine), `loop_state.py`, `loop_heartbeat.py`,
  `loop_progress.py`; CLI entry `src/knotica/cli/loop.py`.
- Plugin layer → repo root (`.claude-plugin/`, `.mcp.json`, `commands/`, `hooks/`, `skills/wiki-maintenance/`).

**Built (Phase P2, gap-fill discovery):** `src/knotica/discovery/` provides a pluggable
source-discovery layer — a `SearchProvider` protocol with an `httpx`-REST adapter (`YouComProvider` with bearer auth; Exa was cut by user directive but the protocol stays pluggable for future adapters), a separate
provider-agnostic OpenAlex enrichment pass stamping citation/venue/open-access metadata, and a deterministic
metadata-only reputability scorer — producing ranked, frozen `SourceCandidate` records for the loop's gap-fill
suggestion queue. It is a pure outbound-network boundary (no vault access, no LLM) and stays off the MCP
cold-start path. **Note:** the you.com API wire shape is documented from the public REST spec but not yet live-verified
(Step 31 deferred); the fixtures are synthetic. Config stays provider-aware for future extension.
Contract and rationale: [`.ai-state/DESIGN.md` § 3](../.ai-state/DESIGN.md#3-components) and ADRs `dec-027` /
`dec-026` (finalize to `dec-NNN` at merge).

**Built (Phase P3, gap-fill suggestion queue):** the human-approval surface that joins diagnosed
`genuine_gap`s (P1) to ranked sources (P2) and lets a person approve, reject, defer, or mark them
ingested.

- `src/knotica/core/records.py` (`SuggestionRecord`) — the `schema_version 1` join record; the
  candidate rides as an opaque dict so `core/records.py` keeps no import edge into `discovery/`.
- `src/knotica/core/gapfill.py` — the only `discovery`-touching module (all `discovery` imports
  lazy): `formulate_query` (deterministic, no LLM), `build_default_discovery_service` (config→service
  factory, `None` on a missing key), `refresh_suggestions_for_gaps` (the drain, own
  `VaultTransaction`, `(gap_id, source_key)`-dedup), and `apply_decision` (approve/reject/defer/
  mark_ingested, discovery-free).
- `src/knotica/mcp_server/tools_suggestions.py` — the deterministic, discovery-free MCP surface:
  `suggestions_read` (cursor-paged, filterable by status) and `suggestions_review` (dry-run|apply).
- `src/knotica/cli/gapfill.py` — `knotica gapfill discover --topic <t>`, the on-demand drain trigger.
- The additive `wiki_status.suggestions` per-topic count block (in `src/knotica/core/status.py`) —
  the passive ingest-handoff surface, including `approved_awaiting_ingest`.

Suggestions land in a committed, observe-safe `<topic>/.knotica/suggestions/suggestions.jsonl`. No LLM
anywhere; approval queues an ingest instruction but never ingests (dec-014 untouched).

**Built (Phase P4, gap-fill source-candidate gate):** the interactive client ingests an approved source onto a server-managed git worktree (isolated from the live default branch), and the loop's existing gate merges gap-closing sources (auto-marking suggestions ingested with page-subset dataset upgrade) or quarantines dilutive ones (never arena).

| Component | Responsibility | Path (verified on disk) |
|---|---|---|
| `source_ingest.py` | Session lifecycle for source ingests (open WIP branch on worktree, publish to `loop/c/*`, abandon); stateless via explicit suggestion-id argument per call | `src/knotica/core/source_ingest.py` |
| `source_gate.py` | Candidate-kind classifier (source vs prompt by branch name); gate dispatch (merge with auto-`mark_ingested` + trainset grower on pass; quarantine to `loop/x/*` with per-question diff on refuse) | `src/knotica/core/source_gate.py` |
| `tools_source_ingest.py` | MCP tools `source_ingest_open` (start WIP ingest, refuse non-approved suggestions) and `source_ingest_submit` (dry-run lint/gate-eligibility check, apply publishes candidate branch and synchronously gates) | `src/knotica/mcp_server/tools_source_ingest.py` |
| `candidate_scope.py` | Helper: resolve worktree/branch from suggestion-id handle; used by `store_source`/`write_page` to route writes onto candidate worktree when `candidate=<handle>` argument is present | `src/knotica/core/operations/candidate_scope.py` |

## 3a. Loop Lifecycle (`knotica loop --topic <t>`)

`LoopRunner` (`core/loop.py`) drives one topic's self-improvement watch loop. Each tick:

1. **Observe** (`observe_default`) — if the default branch's HEAD moved since the last observation
   *and* the diff is content (not `.knotica/` bookkeeping or `log.md`; a `.knotica/prompts/` edit does
   count), and no observation hold applies (below), eval it on a clone (`evals.harness.run_eval`), fetch
   the metrics commit home, and merge. The **first** observation for a topic auto-freezes its scalar as
   the gate baseline (`auto_baseline=True`) — a fresh topic is fully gated with zero manual setup.
2. **Gate** (`poll_once` / `_process_candidate`) — process at most one pending `loop/c/*` candidate branch
   per tick: evaluate, compare to the frozen baseline, then keep (fast-forward merge) or discard.
3. **Heal** (`_heal_prompts_after_regression`) — an observation that regresses below baseline races prompt
   variants via the arena (`core.arena`) on the prompt substrate only; default-branch **content** is never
   reverted.

A daemon heartbeat thread (`core.loop_heartbeat.write_heartbeat`) writes
`.knotica/locks/loop-runner-<topic>.json` every tick so `wiki_status` / the dashboard can report the
runner alive; `core.loop_progress` overwrites a small JSON file in the same `.knotica/locks/` directory
once per eval example so an in-flight observation shows live "question 7/25" progress instead of a frozen
stage card. Both files are gitignored runtime state, never committed. `knotica loop --topic <t>` watches
forever (`--once` runs a single tick; `--set-baseline` freezes explicitly and is rarely needed now that
the first observation self-freezes). `scripts/loop_runner.py` is a forwarding shim to `cli/loop.py` —
prefer the CLI subcommand.

#### Baseline policy state machine

`LoopState.baseline_policy` (`"latest"` default, or `"best"`) governs what an observation does to the
frozen baseline when it beats it, evaluated in `observe_default` (`core/loop.py`):

| Condition | Action |
|---|---|
| No baseline yet, `auto_baseline=True` | Freeze the observed scalar as baseline (first-observation auto-freeze) |
| Baseline exists, observation's `harness_version` differs from the baseline's | **Instrument re-freeze** (below) — never counted as a regression |
| Baseline exists, `scalar > baseline`, `policy == "best"` | Ratchet the baseline up to the new scalar (high-water mark) |
| Baseline exists, `scalar >= baseline` (either policy) | Hold — baseline unchanged, decision passes |
| Baseline exists, `scalar < baseline` | Regression — triggers **Heal** |

`policy == "latest"` never ratchets on a win; only auto-freeze and instrument re-freeze move the
baseline. `policy == "best"` additionally ratchets upward on every win, so the bar only rises. Switch
policy with `LoopRunner.set_baseline_policy("latest"|"best")` (CLI `--baseline-policy`, MCP
`loop_baseline_policy`); readable via `wiki_status.loop.baseline_policy`.

**Rebaseline from history** — `LoopRunner.rebaseline(mode)` (CLI `--rebaseline {best,latest}`, MCP
`loop_rebaseline`) freezes a new baseline directly from `metrics.jsonl` with no eval: it restricts to
records whose `harness_version` matches the newest record (the current instrument), then picks either the
high-water scalar (`best`) or the most recent one (`latest`).

**Instrument re-freeze** — a baseline is only meaningful under the harness fingerprint that produced it.
When an observation's `harness_version` differs from the baseline's (a judge-prompt edit, model rotation,
dspy upgrade, or fingerprint-schema change), `observe_default` re-freezes the baseline at that observation
rather than comparing across instruments; the loop-state commit message records the old and new scalars
for audit. This re-freeze is unconditional on any policy and is never flagged as a regression.

**Recovery** — `LoopRunner.mark_observed()` (CLI `--mark-observed`) adopts the current default-branch HEAD
as observed (cursor advanced, stage `idle`, no eval) after a human has manually reconciled an interrupted
observation (crashed run, killed merge).

#### Observation debounce (watch mode)

`_observation_hold` gates every watch-mode observation behind two independent guards, checked before the
eval runs:

- **Ingest hold** — `core.ingest_activity.has_active_ingest` reports true while an ingest run is in
  progress; bounded by `ingest_hold_stale_seconds` (default 600s) so a crashed ingest can never block the
  loop forever. A multi-commit ingest is measured once, at its natural boundary.
- **Quiet window** — `observe_quiet_seconds` (CLI `--observe-quiet`, default 20; watch mode only) requires
  HEAD to be stable for that many seconds before observing, so a burst of commits coalesces into one eval
  instead of one per commit.

`--once` / `loop_run_once` skip the quiet window (an explicit one-shot invocation observes immediately)
but still respect the ingest hold.

#### Branch topology

Three `loop/`-prefixed branch families, with distinct lifetimes:

| Prefix | Meaning | Lifetime |
|---|---|---|
| `loop/c/*` | Pending candidates awaiting the gate (prompt candidates `loop/c/<sha>`; **source** candidates `loop/c/<topic>/source-<id8>`, gap-fill P4) | Deleted on keep (fast-forward) or discard; a refused source is renamed to `loop/x/*` |
| `loop/wip/*` | **(P4, Built)** In-flight source ingest on a server-managed worktree (`loop/wip/<topic>/source-<id8>`) — invisible to the gate until `source_ingest_submit` publishes it to `loop/c/*` | Published (→ `loop/c/*`) or abandoned |
| `loop/x/*` | **(P4, Built)** Quarantined refused source candidates (`loop/x/<topic>/source-<id8>`) carrying a bounded per-question dilution diff — kept, not deleted | Pruned to newest 5 per topic (mirrors `loop/r/*`) |
| `compile/*` | Pending compile proposals awaiting promotion | Deleted on promote or discard |
| `loop/r/*` | Merged observation-eval audit pointers | Already ancestors of the default branch post-merge; **not** divergent branches — the history lives in `main`, the pointer is convenience only |

`_prune_result_branches` deletes merged `loop/r/*` pointers beyond the newest 5 after every merge;
unmerged ones are left in place as evidence of an interrupted run. Pruning is best-effort and never fails
the observation that triggered it. **Gap-fill P4 (Built):** the `loop/c/*` gate
distinguishes a **source** candidate from a prompt candidate by branch name alone (no persisted
`candidate_kind`); a source candidate is ingested onto its branch by the interactive client through a
server-managed git **worktree keyed by suggestion_id** (default working tree untouched); on pass it merges
and auto-`mark_ingested`s the driving suggestion (page-subset trainset upgrade over the git-derived
newly-merged pages); on regression it is **quarantined** (`loop/x/*`, never raced through the arena — the
arena heals prompt regressions, not content dilution) and the suggestion records an additive `gate_outcome`.
See ADRs `dec-037` (ingest-onto-branch), `dec-036` (candidate_kind + arena
exclusion), `dec-038` (quarantine + `gate_outcome` + contamination-guarded dataset upgrade)
— finalize to `dec-NNN` at merge.

#### Source-candidate detection and dispatch (P4)

The gate's `poll_once` call on each `loop/c/*` candidate begins by classifying the branch:
`classify_candidate(branch)` parses the branch name to distinguish **source** candidates (`loop/c/<topic>/source-<id8>`) from **prompt** candidates (`loop/c/<sha>`). Source candidates are never raced through the arena; instead, `source_gate.py::handle_source_pass` and `handle_source_refuse` route them according to the eval scalar:

- **Pass** (scalar ≥ baseline): fast-forward merge onto default, auto-call `mark_ingested` to transition the suggestion from `approved → ingested`, record `gate_outcome={verdict: merged, ref: loop/r/<sha>}`, and trigger `bootstrap_trainset` with only the git-derived newly-merged entity pages (not all pages — contamination guard via page subset).
- **Refuse** (scalar < baseline): rename the candidate branch from `loop/c/...` to `loop/x/...` (kept as a quarantine record, not deleted), write a bounded (≤10) per-question dilution diff artifact onto the quarantine branch, record `gate_outcome={verdict: refused, ref: loop/x/..., regressed_questions: [...]}` on the suggestion (status stays `approved`), and **never invoke the arena** — content dilution is caught here, not papered over by prompt variants.

Prompt candidates continue through the existing keep/discard/arena flow unchanged.

#### `log.md` union merge

`log.md` is an append-only journal, so concurrent branches legitimately append different lines at the
same location — without a merge strategy this conflicts. `_ensure_union_log_merge` self-heals a
`log.md merge=union` rule into the vault's `.gitattributes` before every merge (idempotent; the
`vault-template/.gitattributes` ships it by default for new vaults). The eval clone is pinned **after**
the loop's own state commit, so the live side only has to reconcile concurrent human activity — which the
union attribute absorbs cleanly.

#### Parallel eval

`evals.harness.run_eval` scores the golden devset through `dspy.Evaluate(num_threads=config.num_threads)`
(default `NUM_THREADS=4`, capped at `MAX_NUM_THREADS=8`; CLI `--eval-threads`). Thread-safety for the
shared instrument: `evals.cache.ResponseCache` uses one compute lock per cache key so concurrent workers
racing the same judge call block on each other instead of double-computing; usage accounting and the
progress-callback counter in `evals.harness` are each guarded by their own lock. `num_threads` is
deliberately **excluded** from `harness_version` — parallelism changes wall-time, not the measurement, and
results are proven identical to a sequential run by test.

#### Diagnostic manifest schema v2

> Status: **Built** (dec-023, gap-fill P0) — landed with the gapfill-substrate pipeline;
> verified end-to-end against the live vault (gen-4 run: 25/25 id join, populated `held_out_delta`).

The per-run manifest (`<topic>/.knotica/eval-runs/gen-<N>/manifest.json`) is the diagnostic substrate the
gap-fill loop's fault classifier will read. Schema v2 is additive over today's manifest and self-versions
via a top-level `manifest_schema_version` (the read-time capability probe; today's unversioned manifest is
implicit v1). It adds, per golden example, a stable `id` (the `QARecord.id` join key, edit-stable) and
`pages` (the ordered top-K retrieval trace as `pages_used`-form page names — the runner already computes
these in `_retrieve` and currently discards them). It also populates `held_out_delta` (a live `None`
placeholder today) with a scalar delta plus a per-`id` vector of score deltas and retrieval-trace diffs,
diffed against the prior generation's manifest and `null`-never-`0` when no comparable prior exists.
The change touches no eval scalar and no `harness_version` fingerprint input, so it triggers no baseline
re-freeze; it leaves every dec-006-frozen record (`metrics.jsonl`) byte-stable.

#### Four-way fault classifier (Phase P1)

> Status: **Built** (gap-fill P1, `gapfill-classifier` pipeline) — `src/knotica/core/gap_classifier.py`
> and `records.GapRecord`, wired into `LoopRunner.observe_default` via the lazily-imported
> `_maybe_redirect_to_gaps` hook. Contract and rationale:
> [`.ai-state/DESIGN.md` § 3](../.ai-state/DESIGN.md#3-components) and ADRs `dec-024` /
> `dec-025` (finalize to `dec-NNN` at merge).

At the **Heal** step, before racing prompt variants, `core/gap_classifier.py` diagnoses
*why* an observation regressed rather than blindly healing. Reading the v2 manifest above on the eval
clone (`held_out_delta` per-id score + retrieval-trace diffs), the golden set (`QARecord.pages_used`), and
a clone page-existence check, it classifies each regressed golden question into one of four faults
via an ordered first-match cascade. Gap records have three origins: `measured` (loop regression classifier),
`reported` (client-as-brain via `gap_report` MCP tool), and `retracted` (guillotine verdicts on weakened claims).

| Fault class | Signal | Route |
|---|---|---|
| `genuine_gap` | reference page(s) do not exist on the clone | persist gap record → P3 discovery |
| `generation_fault` | reference page is in the retrieval trace, answer still degraded | existing arena heal |
| `dilution` | reference page was in the prior trace, absent now, a new page displaced it | persist gap record → P4 quarantine |
| `retrieval_fault` | reference exists, absent from trace, no fresh displacement | existing arena heal (conservative) |

The arena heal is **skipped only** when every regressed question is knowledge-cause (`genuine_gap` /
`dilution`); any prompt/neutral/ambiguous fault, a null delta, or a classifier exception falls through to
the current heal path unchanged (self-healing is never lost). Every knowledge-cause verdict is persisted
regardless of route — a mixed regression logs its knowledge gaps *and* still races the arena for the
prompt-recoverable ones. Knowledge-cause verdicts persist as
`schema_version 1` `GapRecord`s to a committed append-only `<topic>/.knotica/gaps/gaps.jsonl` — its own
`VaultTransaction` under an observe-safe `.knotica/` path — the committed P1→P3 hand-forward queue. The
classifier is deterministic (no LLM), lives in `core/` with core-only deps, is imported lazily by the
loop, and is not part of the eval harness, so it rotates no fingerprint.

**`wiki_status` loop/LLM fields** (`core/status.py::gather_wiki_status`, single-topic scope only):

| Field | Meaning |
|---|---|
| `llm.available` / `llm.mode` | Whether `CLAUDE_CODE_OAUTH_TOKEN`/`ANTHROPIC_API_KEY` is present, and which |
| `loop.runner` | Heartbeat liveness: `alive`, `pid`, `beat_at`, `interval_seconds` |
| `loop.progress` | In-flight eval: `phase`, `current`/`total`, `detail`, `substage`, `sub_current`/`sub_total` |
| `loop.stage` / `loop.last_decision` | Persisted `LoopState.stage` / last gate decision |
| `loop.baseline_frozen` / `loop.baseline_scalar` | Whether/at-what the gate baseline is frozen |
| `loop.baseline_policy` | `"latest"` or `"best"` — the current gate policy |
| `topics[].compiled.optimizer` / `.fallback_reason` | Which optimizer produced the compiled artifact (MIPRO, or the bootstrap fallback + why) |

## 4. Getting Started

Two install channels, both backing the same MCP server:

- **Claude Code plugin:** `/plugin marketplace add francisco-perez-sorrosal/bit-agora` →
  `/plugin install knotica@bit-agora` → `/knotica:setup`.
- **CLI + Claude Desktop:** `uv tool install --from . knotica` → `knotica init --desktop --yes`.
  Full Desktop + AWM use case: [`docs/CLAUDE_DESKTOP.md`](./CLAUDE_DESKTOP.md).
  Summary: [README](../README.md).

Development:

```
uv sync                     # install deps + the project (editable)
uv sync --group evals       # when working on eval / compile
uv run pytest               # run the test suite
uv run knotica doctor       # deterministic health checks
uv run knotica mcp          # serve the MCP server over stdio
uv run knotica mcp --http   # + dashboard at http://127.0.0.1:8765/
```

The vault is a separate git repo at a user-configured path (dev default `~/dev/data/knotica`); never
hardcode vault paths — all access goes through `VaultStore`. Design canon:
[`docs/PRE_PLAN.md`](./PRE_PLAN.md).
