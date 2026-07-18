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
| `src/knotica/mcp_server/` | `FastMCP` server: tools, resources (schemas + index), prompts (static name / lazy body) — thin; stateless. *Named `mcp_server` (not `mcp`) to avoid shadowing the `mcp` SDK; per-concern modules `server`/`envelope`/`tools_read`/`tools_write`/`resources`/`prompts`/`tools_datasets`/`tools_golden`/`app_ui` (dec-009)* | core | Built |
| `src/knotica/programs/` | Phase 3a DSPy query compile: MIPROv2 with a bootstrap fallback (records `optimizer`/`fallback_reason` on the artifact when it falls back; offline compile refuses to fabricate a score without LLM credentials) → JSON compiled artifact + `CompiledRunner`, selected by `query_engine` behind the single MCP `query` tool | core | Built |
| `src/knotica/agent/` | Headless outer-loop runners (SIA schema/structure evolution) — Phase 3b | core | Planned |
| `src/knotica/evals/` | **Frozen-corpus evaluator (Phase 2):** hand-rolled `score(gold, pred, trace=None)` metric seam **run by `dspy.Evaluate`** over the golden devset (user override 2026-07-15; runner only — no optimizers/`dspy.LM`), via a `BaselineProgram(dspy.Module)` wrapping `BaselineRunner` (direct Messages API driving the clone's `query.md`). LLM-as-judge (pinned Opus, N-median, cached), deterministic citation integrity, hinged budget-relative cost-penalty scalar, golden-set bootstrap/freeze. Writes `metrics.jsonl` via `core.transaction` **on a clone** (never the live vault). `anthropic` + `dspy` isolated in the `evals` dep group (off the MCP launch path); LLM auth via env-only `ANTHROPIC_API_KEY`. *As-built modules: `llm` (LLMClient DI seam), `runner` (baseline query runner), `citations`+`scalar` (deterministic scoring), `cache`+`judge` (cached N-median LLM-as-judge; `ResponseCache` holds one compute lock per cache key so concurrent workers racing the same judge call block rather than double-compute), `scorer` (the `score` seam), `program` (BaselineProgram), `golden` (devset load/verify + bootstrap/freeze; public `entity_pages` seam), `config` (packaged defaults + `harness_version` fingerprint; `NUM_THREADS=4` default / `MAX_NUM_THREADS=8` cap for `dspy.Evaluate`'s per-question parallelism — `num_threads` deliberately excluded from the fingerprint, parallelism changes wall-time not the measurement), `harness` (`run_eval`, with `on_example`/`on_substage` progress callbacks feeding `core.loop_progress`; lock-guarded usage accounting under concurrent workers), `train_bootstrap` (Phase 3a: `bootstrap_trainset` — LLM-synthesized cold-start `qa.jsonl` records grounded in a topic's own entity pages, `source: seed_train`, one `VaultTransaction` commit, refuses golden collisions); `cli/eval.py` is the `knotica eval` entry, `cli/datasets.py` the `bootstrap-train`/`freeze` entry.* | core, `core.vcs.clone_to`, `evals` dep group (`anthropic`, `dspy`) | Built |
| `src/knotica/core/loop.py` + `loop_state.py` + `loop_heartbeat.py` + `loop_progress.py` | **Autonomous self-improvement watcher (Phase 3a):** `LoopRunner.observe_default` evals new default-branch content on a clone (content-aware trigger — `.knotica/` state + `log.md` never re-trigger, `.knotica/prompts/` edits do; gated by `_observation_hold` — an active-ingest hold, staleness-bounded 600s, and an `observe_quiet_seconds` HEAD-stability debounce, watch mode only), merges the metrics commit home, and auto-freezes the first observation as the gate baseline; on a harness-version change (instrument rotation) the baseline unconditionally re-freezes rather than comparing across instruments. `LoopState.baseline_policy` (`"latest"` default / `"best"`) governs whether a winning observation also ratchets the baseline up; `set_baseline_policy`/`rebaseline(mode)` change/reset it without an eval; `mark_observed()` is the manual-reconciliation recovery escape hatch. `poll_once`/`_process_candidate` gates at most one pending `loop/c/*` candidate per tick; a regression triggers `_heal_prompts_after_regression` (arena prompt race; default-branch content is never reverted). `_ensure_union_log_merge` self-heals `log.md merge=union` into the vault's `.gitattributes` before every merge; `_prune_result_branches` deletes merged `loop/r/*` audit pointers beyond the newest 5 (unmerged ones kept). `loop_state.LoopState`/`LoopStage`/`LoopDecision` persist runner state via `VaultTransaction`; `loop_heartbeat` writes/reads runner liveness and `loop_progress` writes/reads in-flight eval progress, both as gitignored files under `.knotica/locks/` (not `VaultStore`, no git, no commits). `cli/loop.py` is the `knotica loop` entry (`--watch`/`--once`/`--set-baseline`/`--baseline-policy`/`--rebaseline`/`--mark-observed`/`--observe-quiet`/`--eval-threads`) | core (`vcs`, `arena`, `transaction`, `ingest_activity`), `evals.harness.run_eval` | Built |
| Plugin layer (repo root) | `.claude-plugin/plugin.json`+`marketplace.json`, `.mcp.json` (`uvx --from ${CLAUDE_PLUGIN_ROOT} knotica mcp`), `commands/*.md` (10 `/knotica:*` aliases incl. `/knotica:loop`), `hooks/` (non-blocking SessionStart pre-warm + nudges), `skills/wiki-maintenance/` | `knotica mcp` entry | Built |

**Dependency rule (fitness-checkable):** arrows point inward toward `store/`. `mcp_server/` and `cli/` may import
`core/` but must **not** import git bindings/subprocess-git or call `store.write_*` directly — the *only*
writer of the vault is `core.transaction`. An import-boundary test enforces this.

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
