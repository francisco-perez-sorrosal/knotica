# Architecture

<!-- Design-target architecture document. Created by systems-architect, updated by implementer,
     validated by verifier/sentinel. Section ownership per skills/software-planning.
     Canonical converged design: docs/PRE_PLAN.md (v7). Decisions: .ai-state/decisions/. -->

## 1. Overview

| Attribute | Value |
|-----------|-------|
| **System** | Knotica — LLM-Wiki MVP |
| **Type** | Stateless MCP server + CLI over a versioned Obsidian vault; Claude plugin marketplace |
| **Language / Framework** | Python 3.12+ (uv) / official `mcp` SDK 1.28.1 (`FastMCP`) |
| **Architecture pattern** | Hexagonal, single-mutation-core (one writer through a `VaultTransaction`) |
| **Source stage** | Pipeline `wiki-mvp-core` (Phases 0–1) — systems-architect creation |
| **Last verified** | 2026-07-18 by doc-engineer (loop-ideas delta pass: baseline policy `latest`/`best` + `rebaseline`/`mark_observed` + instrument re-freeze + `_observation_hold` debounce + `_prune_result_branches` + `_ensure_union_log_merge` on `core.loop`/`loop_state`; new MCP tools `loop_baseline_policy`/`loop_rebaseline`; new CLI flags `--baseline-policy`/`--rebaseline`/`--mark-observed`/`--observe-quiet`/`--eval-threads`; `evals.config` `NUM_THREADS`/`MAX_NUM_THREADS` parallel eval + `evals.cache`/`evals.harness` thread-safety; scope: docs only, no code change). Prior: 2026-07-18 by doc-engineer (loop-ideas reconciliation: `core.loop`/`loop_state`/`loop_heartbeat`/`loop_progress` + `cli/loop.py` Built (autonomous watch → observe → gate → heal); `evals/train_bootstrap.py` Built (cold-start `qa.jsonl` seeding); `programs/` Built (Phase 3a compile, was Planned); scope: docs only, no code change). 2026-07-16 by implementer (Phase-2 eval-harness checkpoint: `src/knotica/evals/` + `cli/eval.py` Built + green; full suite green; import-purity held). 2026-07-03 by orchestrator (Phase 1e: store/search/core/mcp_server/cli + plugin layer Built + green; 609 passed / 18 skipped) |

Knotica implements Karpathy's llm-wiki pattern: an AI-maintained compounding markdown knowledge base in
an Obsidian vault, with per-topic self-improving loops (DSPy inner, SIA outer) planned for Phases 2–3.
The **client's LLM is the brain**; the server exposes only deterministic tools and is **stateless** — the
vault (a git repo) and `config.toml` are the only state, resolved per call. The load-bearing structural
property is that **every vault mutation flows through one code path** — a `VaultTransaction` in `core`
that flock-guards the op, performs atomic writes, appends the log, secret-scrubs, and makes exactly one
git commit — so MCP tools, the CLI, and future headless loops cannot drift into inconsistent discipline.

## 2. System Context

<!-- L0: system boundary + external actors. Source: docs/diagrams/architecture/src/architecture.c4 -->
<!-- TODO(diagram): render context.svg via `likec4 gen d2 … && d2 …` (see .c4 header); not yet rendered. -->
Rendered diagram pending: `docs/diagrams/architecture/rendered/context.svg` (source authored at
`docs/diagrams/architecture/src/architecture.c4`).

External actors and dependencies:
- **User** — operates a Claude client and reads/edits the vault directly in Obsidian.
- **Claude client (Code / Desktop)** — client-as-brain; performs ingest/query/lint guided by vault schemas.
- **Obsidian** — frontend over plain markdown + wikilinks + frontmatter (no plugin).
- **Vault (git repo)** at `~/dev/data/knotica` — the wiki itself; a separate private repo; the sync channel
  for future remote loops.
- **`uv`/`uvx`** — hard prerequisite; launches the server from the plugin checkout.

Deployment is out of scope (Phases 0–3 are local-only; no `SYSTEM_DEPLOYMENT.md`).

## 3. Components

<!-- L1 skeleton (systems-architect owns skeleton; implementer fills as-built).
     Source: docs/diagrams/architecture/src/architecture.c4 -->
<!-- TODO(diagram): render components.svg (see .c4 header); not yet rendered. -->
Rendered diagram pending: `docs/diagrams/architecture/rendered/components.svg`.

| Component | Responsibility | Depends on | Status |
|---|---|---|---|
| `src/knotica/store/` | `VaultStore` protocol + `LocalFSStore` — atomic (temp+rename) storage primitives; no git/log/schema knowledge | stdlib | Built |
| `src/knotica/search/` | `SearchBackend` protocol + `RipgrepBackend` — read-only full-text search | store paths | Built |
| `src/knotica/core/` | Vault semantics: `config`, `schema` (root+overlay), `page`/`links`, `lint`, `vcs` (subprocess git), `lock` (fcntl.flock), `scrub`, `records`, `template` (read-only packaged-template locator, shared by `cli.init` + `operations.migrate`), **`transaction.VaultTransaction`**, `operations.*` (four ops config-agnostic: `(store, vault_root, *semantic_args)` — no `core.config` import) | store, search | Built |
| `src/knotica/cli/` | `knotica` entry point: `init`, `mcp`, `doctor`, `status`, `migrate`, `prompt`, `guillotine`, `okf`, `eval`, `compile`, `datasets`, `loop` — thin, self-registering registry; mutations delegate to `core.operations`; never writes the vault directly. `eval` (Phase 2) resolves config and delegates to `evals.harness.run_eval` / `evals.golden.bootstrap`. `datasets` wraps `bootstrap-train` (→ `evals.train_bootstrap.bootstrap_trainset`) and `freeze`. `loop` (Phase 3a) wraps `core.loop.LoopRunner` for `--watch`/`--once`/`--set-baseline`, plus the heartbeat thread — none of these mutate the vault itself | core | Built |
| `src/knotica/mcp_server/` | `FastMCP` server: tools (18 core conversational + 7 operator dispatchers + 4 stragglers + `open_dashboard` during P-B migration), resources (schemas + index), prompts (static name / lazy body) — thin; stateless. *Named `mcp_server` (not `mcp`) to avoid shadowing the `mcp` SDK; per-concern modules `server`/`envelope`/`tools_read`/`tools_write`/`resources`/`prompts`/`tools_datasets`/`tools_golden`/`app_ui` (dec-009)* **P-B tool-surface (Built):** 7 operator dispatchers (`tools_dispatch_*.py` modules) collapse 26 thin tools into a two-tier architecture (action-routing per domain); `INVALID_ARGUMENT` error code for argument validation; `wiki_status` new `view="scope"` parameter for cheap routing checks; `dispatch_telemetry` logs per invocation for post-migration ambiguity measurement; 26 deprecated aliases in original modules with one-release-cycle migration window | core | Built |
| `src/knotica/programs/` | Phase 3a DSPy query compile: MIPROv2 with a bootstrap fallback (records `optimizer`/`fallback_reason` on the artifact when it falls back; offline compile refuses to fabricate a score without LLM credentials) → JSON compiled artifact + `CompiledRunner`, selected by `query_engine` behind the single MCP `query` tool | core | Built |
| `src/knotica/agent/` | Headless outer-loop runners (SIA schema/structure evolution) — Phase 3b | core | Planned |
| `src/knotica/evals/` | **Frozen-corpus evaluator (Phase 2):** hand-rolled `score(gold, pred, trace=None)` metric seam **run by `dspy.Evaluate`** over the golden devset (user override 2026-07-15; runner only — no optimizers/`dspy.LM`), via a `BaselineProgram(dspy.Module)` wrapping `BaselineRunner` (direct Messages API driving the clone's `query.md`). LLM-as-judge (pinned Opus, N-median, cached), deterministic citation integrity, hinged budget-relative cost-penalty scalar, golden-set bootstrap/freeze. Writes `metrics.jsonl` via `core.transaction` **on a clone** (never the live vault). `anthropic` + `dspy` isolated in the `evals` dep group (off the MCP launch path); LLM auth via env-only `ANTHROPIC_API_KEY`. *As-built modules: `llm` (LLMClient DI seam), `runner` (baseline query runner), `citations`+`scalar` (deterministic scoring), `cache`+`judge` (cached N-median LLM-as-judge; `ResponseCache` holds one compute lock per cache key so concurrent workers racing the same judge call block rather than double-compute), `scorer` (the `score` seam), `program` (BaselineProgram), `golden` (devset load/verify + bootstrap/freeze; public `entity_pages` seam), `config` (packaged defaults + `harness_version` fingerprint; `NUM_THREADS=4` default / `MAX_NUM_THREADS=8` cap for `dspy.Evaluate`'s per-question parallelism — `num_threads` deliberately excluded from the fingerprint, parallelism changes wall-time not the measurement), `harness` (`run_eval`, with `on_example`/`on_substage` progress callbacks feeding `core.loop_progress`; lock-guarded usage accounting under concurrent workers), `train_bootstrap` (Phase 3a: `bootstrap_trainset` — LLM-synthesized cold-start `qa.jsonl` records grounded in a topic's own entity pages, `source: seed_train`, one `VaultTransaction` commit, refuses golden collisions); `cli/eval.py` is the `knotica eval` entry, `cli/datasets.py` the `bootstrap-train`/`freeze` entry.* | core, `core.vcs.clone_to`, `evals` dep group (`anthropic`, `dspy`) | Built |
| `src/knotica/core/loop.py` + `loop_state.py` + `loop_heartbeat.py` + `loop_progress.py` | **Autonomous self-improvement watcher (Phase 3a):** `LoopRunner.observe_default` evals new default-branch content on a clone (content-aware trigger — `.knotica/` state + `log.md` never re-trigger, `.knotica/prompts/` edits do; gated by `_observation_hold` — an active-ingest hold, staleness-bounded 600s, and an `observe_quiet_seconds` HEAD-stability debounce, watch mode only), merges the metrics commit home, and auto-freezes the first observation as the gate baseline; on a harness-version change (instrument rotation) the baseline unconditionally re-freezes rather than comparing across instruments. `LoopState.baseline_policy` (`"latest"` default / `"best"`) governs whether a winning observation also ratchets the baseline up; `set_baseline_policy`/`rebaseline(mode)` change/reset it without an eval; `mark_observed()` is the manual-reconciliation recovery escape hatch. `poll_once`/`_process_candidate` gates at most one pending `loop/c/*` candidate per tick; a regression triggers `_heal_prompts_after_regression` (arena prompt race; default-branch content is never reverted). `_ensure_union_log_merge` self-heals `log.md merge=union` into the vault's `.gitattributes` before every merge; `_prune_result_branches` deletes merged `loop/r/*` audit pointers beyond the newest 5 (unmerged ones kept). `loop_state.LoopState`/`LoopStage`/`LoopDecision` persist runner state via `VaultTransaction`; `loop_heartbeat` writes/reads runner liveness and `loop_progress` writes/reads in-flight eval progress, both as gitignored files under `.knotica/locks/` (not `VaultStore`, no git, no commits). `cli/loop.py` is the `knotica loop` entry (`--watch`/`--once`/`--set-baseline`/`--baseline-policy`/`--rebaseline`/`--mark-observed`/`--observe-quiet`/`--eval-threads`). **P-A consolidation (Built):** extracted `core/branch_namespaces.py` owns all five branch-prefix constants and classify/parse helpers (formerly scattered across `loop.py`, `source_ingest.py`, `source_gate.py`, `compile_promote.py`); extracted `core/best_effort.py` owns the shared failure-isolation context manager used by six previously-hand-written `try/except` sites across `loop.py` and `source_gate.py` for deterministic fallback behavior; `_run_arena_and_resolve` (loop.py ln 635) unifies the arena race-and-resolve choreography between `_heal_prompts_after_regression` and `_race_then_resolve`; `build_loop_runner` (loop.py ln 1067) is the single factory that constructs `LoopRunner` with config values unified, preserving each call site's current effective values (MCP gate and CLI watcher) without convergence — unifying construction eliminates duplication while deferring a future config-convergence decision. | core (`vcs`, `arena`, `transaction`, `ingest_activity`), `evals.harness.run_eval`, `branch_namespaces`, `best_effort` | Built |
| `src/knotica/discovery/` | **Gap-fill source discovery (Phase P2, built):** pure outbound-network boundary — reads/writes no vault, holds no state; only inward edge is `core.errors`. `SearchProvider` protocol + `httpx`-REST adapter (`YouComProvider` bearer auth, sole MVP adapter; Exa cut by user directive) originates frozen `SourceCandidate` records; a separate provider-agnostic `OpenAlexEnricher` (keyless polite-pool REST, batched `filter=doi:…` ≤50/call) stamps `citation_count`/`venue`/`is_open_access`/`fwci`; a deterministic metadata-only `ReputabilityScorer` (packaged `DEFAULT_TIER_TABLE` + vault-override seam) assigns a `ReputabilityTier` + `[0,1]` score; `DiscoveryService` composes search→dedup→enrich→score→rank into a total order `(tier,-score,url)`. Protocol remains pluggable for future adapters. No LLM anywhere. Off the MCP cold-start path (import-boundary fitness test extended: `mcp_server ⊬ discovery`; `httpx` lazy-imported — no new dependency, httpx already transitive via `mcp`). Env-only key `KNOTICA_YOUCOM_API_KEY` (you.com wire shape NOT live-verified — Step 31 deferred; config accepts both `youcom` and `exa` for future pluggability), fail-before-network, never logged (mirrors `evals/llm.py`). Consumed by the P3 suggestion queue via `SourceCandidate.to_record()`/`from_record()` (dec-027, dec-026) | `core.errors`, `httpx` (transitive) | Built |
| `src/knotica/core/gap_classifier.py` + `records.GapRecord` | **P1 four-way fault classifier (Phase 3a, gap-fill spine):** at the `observe_default` regression hook (wired between the `regressed` decision and `_heal_prompts_after_regression`, via the lazily-imported `LoopRunner._maybe_redirect_to_gaps`), reads the dec-023 v2 manifest on `outcome.clone_root` (`held_out_delta` per-id score/trace diffs) + the golden set (`QARecord.pages_used`) + a live clone page-existence check, and classifies each regressed golden id via an ordered first-match cascade into `genuine_gap` / `dilution` (knowledge-cause) / `generation_fault` / `retrieval_fault` (prompt/neutral). Knowledge-cause verdicts persist as `GapRecord`s (`schema_version 1`) to `<topic>/.knotica/gaps/gaps.jsonl` on every route (own `VaultTransaction`, `open`-dedup on `(qa_id, fault_class)`, observe-safe `.knotica/` path); the route only decides the heal: prompt/neutral/ambiguous/exception/mixed → existing arena heal (unchanged), all-knowledge-cause → skip the futile heal. Gap records have three origins: `measured` (loop regression classifier), `reported` (client-as-brain via `gap_report` MCP tool), and `retracted` (guillotine verdicts). Deterministic classifier, no LLM, fingerprint-neutral (not part of the harness); core-only top-level imports, imported **lazily** by the loop (mirrors the `run_eval` lazy import). Gap queue is the committed P1→P3 hand-forward contract. | core (`records`, `page`, `transaction`), `evals.golden.load` (lazy, `QARecord`-only — no `dspy`) | Built |
| `src/knotica/core/gapfill.py` + `records.SuggestionRecord` + `mcp_server/tools_suggestions.py` + `cli/gapfill.py` | **P3 suggestion queue + approval surface (gap-fill spine, built):** joins open `genuine_gap` `GapRecord`s (P1) to ranked `SourceCandidate`s (P2) as `schema_version 1` `SuggestionRecord`s (candidate embedded as an **opaque dict** so `core/records.py` keeps no edge into `discovery/`) persisted to a **committed** `<topic>/.knotica/suggestions/suggestions.jsonl` (own `VaultTransaction`, observe-safe, `(gap_id, source_key)`-dedup). `core/gapfill.py` is the sole `discovery`-touching module (all `discovery` imports lazy) — holds `formulate_query` (deterministic, `text=gap.question`, no LLM), `build_default_discovery_service` (the config→service factory P2 left unbuilt; `None` on missing key), `refresh_suggestions_for_gaps` (the drain), `apply_decision` (approve/reject/defer/mark_ingested, discovery-free). Triggers: **on-demand** `knotica gapfill discover` (primary) + **opt-in** loop-side batch (`[gapfill] discover_on_regression`, default off, failure-isolated, `max_gaps`-capped). MCP surfaces (`suggestions_read`, `suggestions_review` dry-run|apply) + a `wiki_status.suggestions` per-topic count block (incl. `approved_awaiting_ingest`) are **discovery-free** (safe on cold-start path). Lifecycle `pending → approved|rejected|deferred` (P3) → `ingested` via `mark_ingested` (P4). No LLM anywhere; no ingest-protocol change (dec-014 untouched). | core (`records`, `transaction`, `page`), `discovery.DiscoveryService` (lazy), `evals`/loop for the demo path | Built |
| `src/knotica/core/source_gate.py` + `source_ingest.py` + `records.SuggestionRecord.gate_outcome` + `mcp_server/tools_source_ingest.py` + `core/operations/candidate_scope.py` + page-subset filter on `evals/train_bootstrap.py`+`evals/golden.py` | **P4 source-candidate gate (gap-fill spine, Built):** the interactive client ingests an approved source onto a `loop/c/<topic>/source-<id8>` branch through a server-managed **git worktree keyed by suggestion_id** (default working tree untouched; per-call flock; one commit per write); the loop's existing clone→eval→gate merges a gap-closing source (auto `mark_ingested` + page-subset trainset upgrade over git-derived merged pages) or **quarantines** a dilutive one (`loop/x/*`, bounded per-question diff, additive `gate_outcome`, never arena). `candidate_kind` is a branch-name convention (no persisted field). Contamination guard: held-out golden candidates client-synthesized from the source **before** ingest, disjoint from `qa.jsonl`, human-gated freeze. Ingest path LLM-free (dec-014); post-merge grower uses the headless-loop LLM. New modules `source_ingest.py`, `source_gate.py`, `tools_source_ingest.py` isolate new logic to avoid worsening `loop.py`/`records.py` ceilings (td-008/009, user-deferred). ADRs `dec-037`/`3b1145b5`/`97c5122a` | core (`vcs` worktree seam, `transaction`, `loop`, `records`, `gapfill`), `evals` (headless grower), interface `dec-034`/`64b4196f` | Built |
| Plugin layer (repo root) | `.claude-plugin/plugin.json`+`marketplace.json`, `.mcp.json` (`uvx --from ${CLAUDE_PLUGIN_ROOT} knotica mcp`), `commands/*.md` (10 `/knotica:*` aliases incl. `/knotica:loop`), `hooks/` (non-blocking SessionStart pre-warm + nudges), `skills/wiki-maintenance/` | `knotica mcp` entry | Built |

**Dependency rule (fitness-checkable):** arrows point inward toward `store/`. `mcp_server/` and `cli/` may import
`core/` but must **not** import git bindings/subprocess-git or call `store.write_*` directly — the *only*
writer of the vault is `core.transaction`. An import-boundary test enforces this.

<!-- aac:authored owner=doc-engineer -->

## 3c. P-B Tool-Surface Dispatcher Architecture (Built)

**P-B consolidates the 49-tool flat MCP surface into a two-tier dispatcher-based architecture without changing operational semantics.** All 26 replaced tools remain callable via additive aliases for one release cycle; the dispatcher modules are import-cycle-free and tied to `dispatch_telemetry` for observability.

**Dispatcher Modules (7 new):**

1. `tools_dispatch_loop.py` — routes `action ∈ {run_once, set_baseline, baseline_policy, rebaseline}` to `tools_vault.py` loop-payload helpers
2. `tools_dispatch_branches.py` — routes `action ∈ {scoreboard, promote_loop, promote, delete}` to `tools_scoreboard.py`
3. `tools_dispatch_compile.py` — routes `action ∈ {run, status, promote}` to `tools_compile.py`
4. `tools_dispatch_datasets.py` — routes `action ∈ {inventory, records, bootstrap, bootstrap_train, freeze}` to `tools_datasets.py`
5. `tools_dispatch_arena.py` — routes `action ∈ {status, history}` to `tools_arena.py`
6. `tools_dispatch_golden.py` — routes `action ∈ {load, save}` to `tools_golden.py`
7. `tools_dispatch_vault_health.py` — routes `action ∈ {doctor, repair, okf_check, okf_repair, lint, metadata_tree}` to `tools_vault.py` health-payload helpers

Each dispatcher validates its `action` enum and returns `INVALID_ARGUMENT` for unrecognized values. Mutating actions accept optional `mode=dry-run|apply`. Every dispatcher is registered into `build_server()` alongside (not instead of) the thin tools, enabling the one-release-cycle alias migration.

**Migration Telemetry:**

`dispatch_telemetry.DEPRECATED_ALIASES` is the single source of truth for the 26 alias mappings. Each dispatcher invocation logs `{tool, action, topic}` to `dispatch_telemetry` for post-migration measurement of per-domain selection ambiguity (enabling a future decision to revert one dispatcher back to flat tools without touching the others).

**New Error Code — `INVALID_ARGUMENT`:**

Distinct from `INVALID_CURSOR` (cursor format errors), `INVALID_ARGUMENT` signals argument-validation failures: unrecognized `action` enum values, missing required arguments, out-of-range scalars, etc. All dispatchers and mutating tools validate inputs before execution and return this envelope (not a raw exception) on failure.

**New `wiki_status(view="scope")`:**

A new parameter-value pair enables cheap routing-scope checks without eval or compile snapshots. Returns `{schema_version, vault_name, topics[], totals}` — deterministic, vault-path-read only. Used by P-C client-side routing to decide whether a detected wiki-relevant conversation should route to a dispatcher or stay in natural chat.

**Migration Window:**

For one release cycle, 26 deprecated tools remain registered with a deprecation note in their `description` field. The original thin-tool modules (`tools_scoreboard.py`, `tools_compile.py`, etc.) continue to export their tools; the dispatchers are additive. Clients calling via the old tool names are logged and work unchanged; new code should call via dispatchers.

**P-B Rationale & Decisions (dec-040, dec-041):**

- **Action routing:** consolidates domain-related actions into a single entry point per domain, reducing surface cognitive load and enabling structured input validation at the action enum level
- **Additive aliases:** one-release-cycle migration window preserves backward compatibility; no forced client update
- **Telemetry:** logs enable measurement of whether the consolidation succeeded (low mis-selection rate) or whether one domain should revert to flat tools
- **Immutable semantics:** no operation behavior changes; every dispatcher action is 1:1 with a replaced tool

<!-- aac:end -->

## 4. Interfaces

<!-- Implementer-owned: concrete signatures fill in during Phase 1. Behavioral contracts below. -->
Tool/prompt contracts are specified at the behavioral level in `.ai-work/wiki-mvp-core/SYSTEMS_PLAN.md`
§ Interfaces; JSON-schemas + wording are owned by the interface-designer (`INTERFACE_DESIGN.md`).
Mutating tools (`write_page`, `store_source`, `create_topic`, `curate_example`) → one commit each via
`core.operations`; read tools (`read_page`, `search`, `list_links`/`backlinks`, `lint_check`) → no commit.
Every tool/prompt honors the `unconfigured` contract (structured result, not an exception).

**Eval harness (Phase 2, as-built `src/knotica/evals/`):**

- `VaultVcs.clone_to(dest_root: str | PurePath, ref: str | None = None) -> VaultVcs` — clone the source
  vault (optionally at `ref`) into a throwaway tree; the frozen-corpus mechanism (a read/checkout method,
  never a mutation, so `evals/` may call it directly).
- `LLMClient.complete(*, snapshot, system, messages, temperature=0.0, max_tokens) -> Completion` — the one
  network seam (a Protocol); `AnthropicClient` (real; env-only credential, OAuth-first: `CLAUDE_CODE_OAUTH_TOKEN`
  preferred, noisy fallback to the metered `ANTHROPIC_API_KEY` — user override 2026-07-16) and `FakeLLMClient`
  (zero-network) implement it.
- `BaselineProgram(store, topic, runner)` with `forward(question: str) -> dspy.Prediction` — the
  `dspy.Module` the metric runs over; calls `runner.run` only (no `dspy.LM`, so `dspy.settings.lm` stays unset).
- `build_metric(...)` → the closed-over `score(gold, prediction, trace=None) -> float | bool` — the
  triple-consumer seam: the bounded per-example quality float when `trace is None`, the bool
  `quality >= threshold` when `trace` is set (`dspy.Evaluate`'s 2-arg metric convention).
- `run_eval(topic, *, source_root=None, ref=None, llm_client=None, config=DEFAULT_CONFIG, ...) -> EvalRunResult`
  — the orchestrator: clone → `golden.load` → `dspy.Evaluate` → compose scalar → one `VaultTransaction` on
  the clone (source vault byte-identical). Returns the appended `MetricsRecord` plus the `clone_root` it
  committed to, so the clone-relative `artifact_ref` resolves and the eval commit is reviewable.
- `golden.load(store, topic) -> list[QARecord]` (read + `MANIFEST.json` verify) /
  `bootstrap(store, topic, llm_client, snapshot) -> list[dict]` (stage candidates, no commit) /
  `freeze(store, vault_root, topic, accepted) -> FreezeResult` (one commit) — the golden-set read + write sides.
- `harness_version(judge_prompt_hash, config=DEFAULT_CONFIG) -> str` — the instrument fingerprint recorded
  per run so two scalars from different instruments are never silently compared.

**Diagnostic manifest schema v2 (Phase 3a gap-fill P0 — built, dec-023):**
The per-run manifest (`<topic>/.knotica/eval-runs/gen-<N>/manifest.json`, the `artifact_ref` target) is
the diagnostic substrate the P1 four-way fault classifier reads. Additive over the current shape, it
self-versions via `manifest_schema_version` (the read-time capability probe; today's unversioned shape
is implicit v1) and adds: `per_example[].id` (the stable `QARecord.id`, an edit-stable join key mapped
onto the `dspy.Example` in `golden.to_example`); `per_example[].pages` (the ordered top-K retrieval
trace as `pages_used`-form page names, carried through a new `Prediction.pages` field, forwarded in
`BaselineProgram.forward`); and a populated `held_out_delta` object (scalar delta + per-id score/trace
deltas + `ids_added`/`ids_removed`, keyed on stable id, prior generation discovered via the prior
`MetricsRecord.artifact_ref`; `null`-never-`0` when no comparable prior exists). Retrieval *scores* are
excluded (rank-order only) to stay stable across the Phase-5 vector-backend swap. The change touches no
scalar, no `harness_version` fingerprint input, and no dec-006-frozen record — so it triggers no
baseline re-freeze. Re-affirms dec-006 (version machine-readable records) by extending that discipline
to the previously-unversioned manifest rather than modifying a frozen record.

`knotica eval --topic <t>` (metrics) / `--bootstrap` (stage candidates for review) is the CLI entry
(`cli/eval.py`); it resolves config and delegates, renders the `MetricsRecord` or the staging handoff
(table or `--json`), and never mutates the vault itself.

**Loop + cold-start (Phase 3a, as-built `src/knotica/core/loop*.py` + `evals/train_bootstrap.py`):**

- `run_eval(..., on_example: Callable[[int, int, str], None] | None, on_substage: Callable[[str, int, int], None] | None) -> EvalRunResult`
  — the progress seams the watcher wires to `core.loop_progress.write_progress` so an in-flight
  observation reports per-question ("7/25") and per-substage ("judging") progress.
- `LoopRunner.observe_default(*, auto_baseline: bool = True) -> LoopCycleResult` — the watch tick's
  observe leg (see § 5); `LoopRunner.poll_once() -> LoopCycleResult` — the gate leg (one pending
  `loop/c/*` candidate per call).
- `LoopRunner.set_baseline_policy(policy: "latest" | "best") -> LoopState` — persist the gate policy
  (`ValueError` on an unrecognized value); `LoopRunner.rebaseline(mode: "best" | "latest" = "best") -> LoopState`
  — freeze a new baseline straight from `metrics.jsonl` (no eval), restricted to the current-instrument
  records; `LoopRunner.mark_observed() -> LoopState` — adopt HEAD as observed after manual reconciliation
  (no eval). MCP: `loop_baseline_policy(topic, policy, vault="")`, `loop_rebaseline(topic, mode="best", vault="")`.
- `write_heartbeat(vault_root, topic, *, interval_seconds) -> None` / `read_runner_liveness(vault_root, topic) -> dict`
  — the runner-liveness pair `wiki_status`/the dashboard poll; `write_progress`/`read_progress` — the
  matching in-flight-eval-progress pair. Both are plain filesystem writes under `.knotica/locks/`, no
  `VaultStore`, no commit.
- `bootstrap_trainset(store, vault_root, topic, llm_client, snapshot, *, target_n=30, per_page=5) -> dict`
  — cold-start: for each of the topic's entity pages (`evals.golden.entity_pages`), the LLM synthesizes
  query-style QA pairs grounded in that page, deduped against the existing trainset, refusing any that
  collide with the held-out golden set; appends to `qa.jsonl` with `source: seed_train` in one
  `VaultTransaction`. CLI: `knotica datasets bootstrap-train --topic <t> [--target N]`; MCP tool
  `datasets_bootstrap_train(topic, target=30, vault="")`.

## 5. Data Flow

**Mutating op:** `client → mcp/ tool → core.operations.<op> → resolve config (per call) →
VaultTransaction: flock → store.write_text_atomic → append log.md → scrub → vcs.commit (one commit,
msg `knotica(<op>): <topic> — <title>`) → release flock → Result`.

**Read op:** `client → mcp/ tool → core read fn / search backend → Result` (no lock, no commit).

**Prompt:** `client slash-command → prompts/get → lazy body: resolve config → unconfigured?
setup-guidance : read .knotica/prompts/<op>.md (topic override else root default) → body with full protocol`.

**Eval op (Phase 2, headless — `knotica eval`, no MCP/no client-brain):** `config-resolve SOURCE vault →
VaultVcs.clone_to(tmp) at HEAD (corpus_ref = git:<sha>) → load golden.jsonl → per example: BaselineRunner
drives the clone's query.md + in-process search/read_page → judge (Opus, N-median, cached) + deterministic
citation integrity → hinged budget-relative scalar → MetricsRecord → VaultTransaction(clone, "eval") one
commit + log.md → source vault untouched, eval branch returned`. Runs on a knotica-owned
`ANTHROPIC_API_KEY` (env-only; never on the server launch path) — a new trust boundary distinct from
client-as-brain (`dec-014`).

**Unconfigured boot:** server registers tools/prompts/resources with zero vault access; first call resolves
config and returns `unconfigured` until `init`/`setup` writes `config.toml` (picked up per call, no restart).

**Watch tick (Phase 3a, headless — `knotica loop --topic <t>`, no MCP/no client-brain):** `poll tick →
observe_default: default-branch HEAD moved + content changed? → _observation_hold (active-ingest hold /
observe_quiet_seconds debounce) clear? → _ensure_union_log_merge → VaultVcs.clone_to(tmp) at HEAD →
run_eval(on_example, on_substage → core.loop_progress; num_threads=config.num_threads) → fetch metrics
commit into loop/r/<sha> → checkout default → merge (non-ff) → _prune_result_branches → baseline unset?
auto-freeze : instrument (harness_version) changed? re-freeze : policy=best and scalar>baseline? ratchet
up : compare → write_loop_state (VaultTransaction) → regressed? _heal_prompts_after_regression:
arena.race_variants over prompt variants, promote winner (content unchanged) : gate: poll_once → next
pending loop/c/* tip → evaluate on a clone → keep (fast-forward merge) or discard → write_loop_state`.
Runtime files (`.knotica/locks/loop-runner-<topic>.json` heartbeat, `.knotica/locks/loop-progress-<topic>.json`)
are machine-local gitignored state — plain filesystem writes, never `VaultStore`, never committed, never
read by anything but `wiki_status`/the dashboard on the same machine.

## 6. Dependencies

<!-- Implementer-owned: pin exact versions in pyproject.toml. -->
Floors, not pins: `mcp>=1.28` (resolves to 1.28.1 in `uv.lock`) — the sole runtime dependency.
Dev group: `pytest`, `ruff`. **Eval group** (Phase 2, PEP 735 `[dependency-groups] evals`, `uv sync --group evals`):
`anthropic>=0.116` (Messages API for the headless eval runner + judge; 0.116.0 verified PyPI 2026-07-15) **and
`dspy>=3.2`** (`dspy.Evaluate` as the per-example runner — user override 2026-07-15; 3.2.1 verified PyPI, requires-python
`<3.15`) — both declared **only** in the dependency-group so the built wheel never ships them and
`uvx --from … knotica mcp` never resolves them (so even dspy's heavy tree, incl. litellm, never touches the launch
path), protecting the 24.4 s cold start (`dec-013`). Phase-3a adds DSPy optimizers to the same group.
Build backend: `hatchling` (src layout; repo-root `vault-template/`
force-included into the wheel as `knotica/vault-template` for `knotica init`; editable/dev installs
fall back to the repo-root copy). `git` and `uv`/`uvx` are user-machine prerequisites, not project
deps. `ripgrep` used via subprocess.

## 7. Constraints

Locked invariants (from `CLAUDE.md` / `docs/PRE_PLAN.md` — do not violate without updating the pre-plan):

- **Client-as-brain**: server exposes deterministic tools only; no server-side LLM until Phase 3a.
- **Stateless server**: no session state; vault + config are the only state, resolved per call; topic is
  always an explicit tool argument.
- **Vault/code separation**: wiki at `~/dev/data/knotica`; all vault access via `VaultStore`; never
  hardcode vault paths.
- **One git commit per mutating op**, flock-guarded (load-bearing — stdio servers may be long-lived and
  shared across sessions).
- **Loops always work on a git clone**, never the live vault; results return as branches.
- **Single source of truth for prompts**: operation prompts live in the vault (`.knotica/prompts/`, root
  defaults + earned topic overrides) — simultaneously the MCP-prompt UX surface and the DSPy/SIA substrate.
- **Graceful unconfigured boot**; **never `alwaysLoad`** on the knotica MCP server.
- **Obsidian hard-ignores dot-paths** — no user-facing content in or linking into `.knotica/`.

## 8. Decisions

Draft ADRs (`.ai-state/decisions/drafts/`, finalized to `dec-NNN` at merge):

- **dec-007** — MCP SDK: official `mcp` 1.28.1 over jlowin `fastmcp` v3 (cold-start dep-weight;
  canonicity; swap confined to `mcp/`).
- **dec-008** — Module boundaries + single vault-mutation path (`VaultTransaction`; one writer;
  import-boundary fitness test).
- **dec-004** — Config schema + unconfigured contract (per-call resolution; three-state machine).
- **dec-006** — Freeze record schemas at Phase 0 (qa/metrics/log/commit/provenance; per-record
  `schema_version`; documented in root `SCHEMA.md`).
- **dec-005** — uvx cold-start pre-warm (setup foreground + background-idempotent SessionStart
  hook; never `alwaysLoad`).

Phase 2 — eval harness (this pipeline, `eval-harness`):

- **dec-012** — Hand-rolled `score()` metric core, **run by `dspy.Evaluate` as the per-example
  runner now** (user override 2026-07-15; runner only — no optimizers/`dspy.LM`); trace-branch float/bool.
- **dec-014** — Eval LLM access: direct Messages API behind `BaselineRunner` + pinned Opus judge;
  knotica-owned `ANTHROPIC_API_KEY` (env-only) = a new trust boundary distinct from client-as-brain.
- **dec-016** — Scalar = hinged, budget-relative, multiplicative `Q·(1−λ·hinge)`; citation
  validity deterministic-only (faithfulness deferred). Re-affirms record shape, revises additive formula clause.
- **dec-018** — Golden set: synthetic-from-pages + human review-freeze; held-out split from day
  one (`golden.jsonl` disjoint from `qa.jsonl`); `source: curate_example` (no enum change).
- **dec-015** — `metrics.jsonl` via `VaultTransaction` on the clone (`eval` op + log);
  reproducibility via `artifact_ref`→per-run manifest + `harness_version` fingerprint (no schema bump);
  fitness test extended to `evals/`.
- **dec-013** — Eval deps in a PEP 735 `[dependency-groups] evals` (not an optional-extra) to
  guard the uvx cold start.
- **dec-017** — Frozen-corpus mechanism: `VaultVcs.clone_to` + SHA-pin + MANIFEST + determinism kit.
- **dec-019** — Eval-harness module landing order: corrects the plan's Group B/C/D hints to the
  import-dependency graph (`cache` before `judge`, `scorer` after `judge`, `golden` split into read-side
  then bootstrap/freeze) so every module imports only already-landed siblings (no interface change).

Phase 3a — gap-fill diagnostic substrate (this pipeline, `gapfill-substrate`):

- **dec-023** — Eval-manifest diagnostic substrate: manifest schema v2 self-versions and adds
  `per_example[].id` (stable join key), `per_example[].pages` (ordered retrieval trace), and a wired
  `held_out_delta` object — the substrate the P1 four-way fault classifier consumes. Additive over
  dec-006-frozen records (re-affirms dec-006); no scalar / fingerprint / `metrics.jsonl` change, hence
  no baseline re-freeze. Rank-order only (scores deferred to a possible v3).

Phase P1 — gap-fill fault classifier (this pipeline, `gapfill-classifier`; draft ids finalize at merge):

- **dec-024** — Four-way fault classifier + heal-redirect: an ordered first-match cascade over
  the dec-023 v2 manifest classifies each regressed golden id into `genuine_gap` / `dilution` /
  `generation_fault` / `retrieval_fault` (co-occurrence resolved by precedence: generation-fault before
  dilution; dilution needs displacement AND a new competitor). Routes prompt/neutral → existing arena heal
  (unchanged); skips the arena **only** when every regressed id is knowledge-cause; null delta / empty set /
  any classifier exception → heal (conservative, self-healing never lost). Deterministic, no LLM, in
  `core/gap_classifier.py` (core-only deps, lazily imported by the loop), fingerprint-neutral. Re-affirms
  dec-023 (confirms its necessary-and-sufficient claim; no fourth substrate item needed).
- **dec-025** — Gap-record schema v1: knowledge-cause verdicts persist as a new
  `schema_version`ed `GapRecord` (in `core/records.py`) to a committed append-only
  `<topic>/.knotica/gaps/gaps.jsonl`, written in its **own** `VaultTransaction` under an observe-safe
  `.knotica/` path (`open`-dedup on `(qa_id, fault_class)`). Stable `gap_id`/`qa_id` join key + status
  lifecycle + self-contained evidence snapshot = the committed P1→P3 contract (P3 filters `genuine_gap` for
  discovery; `dilution` is P4/quarantine input). Committed-not-staged because the stateless MCP server reads
  it in a separate process. Re-affirms dec-006.

Phase P2 — gap-fill discovery layer (this pipeline, `gapfill-discovery`; draft ids finalize at merge):

- **dec-027** — Discovery contract: a `runtime_checkable` `SearchProvider` protocol produces a
  frozen, `schema_version`ed `SourceCandidate` (dec-006 precedent); reputability metadata is stamped by a
  **separate provider-agnostic** `OpenAlexEnricher` (batched by DOI, ≤50/call) rather than fused per-adapter;
  reputability is a deterministic **metadata-only** tier + `[0,1]` score (never textual — ungameable). Candidate
  is self-contained (no gap/question linkage — that join is P3's). Total deterministic rank `(tier,-score,url)`.
- **dec-026** — Discovery HTTP boundary: **direct `httpx` REST** behind one thin shared client for all
  three providers; **no provider SDK** (`exa-py` rejected — it drags `openai>=1.48`+`python-dotenv`; verified PyPI
  2026-07-19). No new dependency (`httpx` already transitive via `mcp`). Env-only keys, fail-before-network,
  never-log — the `evals/llm.py` (dec-014) trust-boundary discipline applied to search APIs. One additive
  `SEARCH_API_ERROR` code in `core.errors`.

Phase P3 — gap-fill suggestion queue + approval surface (this pipeline, `gapfill-queue`; **Built**, draft ids finalize at merge):

- **dec-030** — Suggestion record schema v1: the P1-gap × P2-candidate join persists as a new
  `schema_version`ed `SuggestionRecord` (in `core/records.py`) to a **committed** append-only
  `<topic>/.knotica/suggestions/suggestions.jsonl`, own `VaultTransaction`, observe-safe `.knotica/` path,
  `(gap_id, source_key)`-dedup. Lifecycle `pending → approved|rejected` (P3) → `ingested` (P4), mutated in
  place one-commit-per-transition. Candidate embedded as an **opaque JSON dict** (verbatim
  `SourceCandidate.to_record()`), **not** a typed `SourceCandidate`, so `core/records.py` keeps zero edges into
  `discovery/` (MCP cold-start boundary, dec-013). Committed-not-staged (dashboard MCP server reads it in a
  separate process — re-affirms dec-025 Option B; the golden.py staging precedent does not transfer). Re-affirms
  dec-025 + dec-006.
- **dec-029** — Discovery trigger placement: **on-demand primary** (`knotica gapfill discover` CLI)
  keeps outbound network off the loop's mandatory offline-deterministic heal path; a **config-gated opt-in
  loop-side batch** (`[gapfill] discover_on_regression`, default off) shares the same drain, failure-isolated
  (classifier try/except) + separate transaction. Fixed-budget defense: one drain per regression *event*
  (never per-question), `max_gaps` call cap (default 5, top-|quality_delta|), `(gap_id, source_key)`-dedup.
  `dilution` gaps never drained (P1 contract #3). The committed gap queue is the durable buffer, so deferral
  loses no data. DI-close call vs loop-side-automatic-default-on (reversal: flip default once a key is reliably
  provisioned).

Phase P4 — gap-fill source-candidate gate (this pipeline, `gapfill-source-gate`; **Planned**, draft ids finalize at merge):

- **dec-037** — Source ingest lands on `loop/c/*` via a **server-managed git worktree keyed by
  `suggestion_id`**, resolved per call from an explicit id / opaque `candidate` handle (no server session
  state — dec-004). The interactive client builds on a private `loop/wip/<topic>/source-<id8>` branch (per-call
  flock at the canonical root, one commit per write — dec-008); `source_ingest_submit` publishes it atomically to
  `loop/c/<topic>/source-<id8>` (the readiness boundary; the gate never sees a partial branch). Default working
  tree + default ref untouched throughout; the ingest path stays LLM-free (dec-014). Re-affirms dec-004.
- **dec-036** — `candidate_kind` is a **branch-name convention** (`/source-` infix), not a persisted
  field — git-derived, recovers `suggestion_id` for free (dec-004). `_process_candidate` gains a thin kind-branch
  (source logic in a new `core/source_gate.py`, keeping `loop.py` off the deferred td-008 ceiling); a source
  candidate that regresses the scalar is **quarantined, never raced through the arena** (the arena heals prompt
  regressions; content dilution is not prompt-fixable — racing risks a prompt that masks it, a reward-hacking
  hazard).
- **dec-038** — Refuse = **quarantine** (`loop/x/<topic>/source-<id8>`, kept not deleted) with a
  bounded top-N per-question dilution diff; the suggestion records one **additive nullable `gate_outcome`**
  (merge → `status=ingested` via auto `mark_ingested` + `{merged, loop/r ptr}`; refuse → `status=approved` +
  `{refused, loop/x ptr, reason}`, gate-terminal — consumers filter `approved AND gate_outcome is null`). On
  merge the **trainset** grows for the **git-derived** newly-merged pages (additive `pages` filter on
  `bootstrap_trainset`/`golden.bootstrap`, `None`=today). **Contamination guard**: held-out golden candidates are
  client-synthesized from the source **before** ingest, disjoint from `qa.jsonl`, frozen only through the
  human-gated read-merge-freeze (dec-018) — the automated path grows the trainset, not the frozen gate. Additive
  over dec-030 (re-affirms it; the Addendum pre-sanctioned an additive branch-ref field). Reconciled with the
  interface-designer's `dec-035` (adopts its `gate_outcome` shape + `loop/x/*` namespace) and
  `dec-034` (the `candidate` handle + `source_ingest_open`/`submit` surface).

Feature — eval cadence + per-task models (this pipeline, `eval-cadence-model-config`; **Planned**, draft ids finalize at merge):

- **dec-draft-9e0a147d** — Configurable eval cadence + td-011 re-arm + spend-gated dashboard eval
  trigger. Global `[loop]` table (`eval_min_interval_hours` default 0 = byte-identical,
  `eval_window` quiet-hours, `eval_num_threads` default 4), resolved by a new
  `loop_cadence_config.py` (mirrors `gapfill_config.py`). Cadence hooks a single `_cadence_hold`
  guard into `observe_default()` only — candidate-gate path (`poll_once`) stays eager by
  construction. A wall-clock `now_fn` injectable backs interval + window (monotonic clock can't
  express time-of-day). **td-011 fixed**: the failure handler stops consuming the cursor (drops
  `mark_processed`), sets additive `pending_retry` + `last_eval_started_at` LoopState fields
  (`schema_version=1`, additive-safe), so a failed eval re-arms. Cadence is Desktop-controllable
  via `loop action=cadence` (deterministic config write); the billed `loop action=run_eval`
  trigger is gated by a two-phase decision-envelope nonce (no single-call billing) + `billed`
  annotation + detection→`wiki_status` steering. Global-not-per-topic (no per-topic precedent).
  DI-close vs CLI/HTTP-only trigger (reversal: move off shared MCP surface if telemetry shows
  unbidden agent billing).
- **dec-draft-01a7689b** — Per-task `[models]` config. Keys `worker`=Haiku 4.5
  `claude-haiku-4-5-20251001` (high-volume grounded QA), `judge`=Sonnet 5 `claude-sonnet-5`
  (load-bearing judgment), `query`=Sonnet 5 (user-facing). worker/judge fold into
  `harness_version` (config value populates `HarnessConfig` before CLI override via
  `config_from_toml.with_overrides(**cli)`, so a model swap refreezes the baseline); `query` is
  excluded (never writes the frozen instrument). MIPRO proposal LM is inert → no key (tech debt).
  Per-snapshot `temperature` conditionalization in `evals/llm.py` (Sonnet 5 rejects the arg;
  Haiku 4.5 / 4.6-gen keep sending `temperature=0`); Sonnet 5 judge loses temperature-0
  determinism (absorbed by the 3-sample median; snapshot change refreezes). Re-affirms dec-015 /
  dec-014.

<!-- aac:authored owner=systems-architect last-reviewed=2026-07-21 -->
### Consolidation realized — loop-consolidation pipeline (2026-07-21)

**Status: BUILT (P-A/P-B/P-C complete; P-D remaining planned).** The rows in §3 above describe the codebase as
Built on `main` (dec-001..038). The consolidation pass (`loop-consolidation` pipeline) reshaped surfaces and internals
**without changing any dec-001..038 semantics**. **Deliberately deferred by the consolidation's
scope boundary** (future work, not regressions): leveling plain ingest up to the source-ingest
gate handshake (idempotent resume + mechanical completion backstop — a semantic change to the
ingest trust model, needs its own design pass); removal of the 26 deprecated operator-tool
aliases (separately gated after one release cycle, informed by dispatch telemetry); systemd
service-manager live verification (code-complete, launchd is the verified platform);
`loop.py` cohesion split (td-008 — the consolidation grew it to 1221 lines; extract the
arena-race core + runner factory next). Finalized ADRs (dec-NNN ids assigned at merge):

- **dec-045** — Tiered MCP tool-surface topology: thin conversational core
  (~18 tools, dec-003 principle re-affirmed) + operator long-tail collapsed into 7
  action-parameterized dispatchers (`loop`/`branches`/`compile`/`datasets`/`arena`/`golden`/
  `vault_health`); one server (Option A) now, lazy catalog meta-tool (B) deferred as the
  future-preferred evolution gated on client capability, second server (C) rejected;
  additive-alias non-breaking migration (49 → ~29 model-facing tools). Companion:
  interface-designer `dec-041` (dispatcher shapes) + `dec-040`
  (`INVALID_ARGUMENT` error code, adopted).
- **dec-042** — **[Built, 2026-07-21]** Conversational routing & transparency: four-layer architecture
  (skill symptom-detection + `_INSTRUCTIONS` stable-invariants-only + tool-description guards on mutating tools + vault prompts as sole evolvable substrate);
  per-client routing-reliability tiers (Tier-1 Claude Code skill+hooks / Tier-2 Desktop instructions-only);
  `server.py` `_INSTRUCTIONS` slimmed to detection heuristics + stable invariant guards + a
  `read_protocol` pointer (no enumerated evolvable steps — kills the drifted duplicate at
  the root, no boot-time vault read); new cheapest `wiki_status(view=scope)`; SessionStart
  topic-awareness seed + attention-nudge (`knotica status --nudge`, Tier-1 proactive detection);
  read/offer over-routing guard on every mutating tool. Companion: interface-designer
  `dec-039` (routing-artifact separation).
- **dec-043** — Loop-internals consolidation (behavior-preserving,
  characterization-tests-first): `core/branch_namespaces.py` single-source-of-truth for the
  five branch prefixes; one shared best-effort primitive; one `_run_arena_and_resolve`
  helper; one `build_loop_runner` factory (preserves current per-site config values); and a
  credential-conditional `discover_on_regression` default (realizes dec-029's named
  reversal). `loop.py` returns under ceiling incidentally (td-008). Deferred: candidate-gate
  Protocol, records-schema base (td-009), `harness`/`golden` splits (td-002).
- **dec-044** — Loop becomes a **lifecycle-managed service** (supersedes the
  PRE_PLAN "No periodic daemon in MVP" stance per user guidance): the `knotica loop --watch`
  watcher is automatically installed/spawned/supervised (leading candidate: an OS service
  manager registered by the install flow) under a one-click-install / zero-user-burden bar.
  **Lifecycle only** — loop semantics, client-as-brain (the loop is a headless loop), and the
  stateless MCP server (a *separate* process) are all unchanged. PRE_PLAN's Safety-net clause
  is updated at implementation.

Dashboard stays a **full dual-mode independent peer** (MCP App + standalone HTTP, dec-020);
conversation is added as a co-equal first-class decision surface, not a replacement.
<!-- aac:end -->
