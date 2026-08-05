# Architecture Guide

<!-- Developer navigation guide. Every component name and file path here is verified against the codebase;
     only components that exist on disk are listed as Built. Design rationale + planned components live in
     .ai-state/DESIGN.md; the converged design lives in docs/PRE_PLAN.md.
     Created by systems-architect; updated by implementer; verified by doc-engineer at checkpoints. -->

> **Status: MVP + Phase-2 `evals/` + Phase-3a `programs/`/compile/loop + consolidation P-A/P-B/P-C/P-D + notes-overlay Phase 1 (Built as of 2026-07-30).** The `store/`,
> `search/`, `core/`, `mcp_server/`, `cli/`, `evals/`, and `programs/` packages plus the autonomous
> `knotica loop` watcher, the cold-start dataset bootstrap, the plugin layer, and the dashboard MCP App
> are on disk. **P-A loop-internals (branch namespaces, best_effort, arena-race, runner factory)** and
> **P-B tool-surface two-tier dispatcher** (7 operator dispatchers + 18-core + 4-straggler conversational tools;
> additive-alias migration window active) are Built. **P-C transparency/routing** (skill + slimmed instructions)
> partially Built. **Notes overlay Phase 1** — personal marginalia (`core/notes/`, `note_capture`, the `notes`
> dispatcher's `list`/`read` actions, the dashboard Notes tab, `/knotica:note`) — is Built; the resolution ladder's
> `fuzzy` rung, `reanchor`/`detach`/`promote`/`archive`, and the eval-bridge are Phase 2+. Outer-loop `agent/`
> (SIA / Phase 3b) and **P-D service lifecycle** remain Planned. For design rationale, read
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
| **Last verified against code** | 2026-08-05 — the three `core/` modules this guide was silent on are now named (`topics.py`, `jsonl.py`, `loop_attempt.py`), and the `vault_layout.py` row no longer describes the four inline reserved-name walks that `core/topics.py` replaced. **Verification is no longer a promise about a manual pass**: `scripts/check_architecture_coverage.py` runs on every `make verify` and fails when a package's module count drifts from `.ai-state/DESIGN.md` § 3's inventory table or when any `src/knotica/...` path cited here stops resolving on disk. This guide lagged the code by three commits precisely because it was the one artifact pair with no gate (td-038). Prior: 2026-08-04 — §3 gained rows for three packages this guide had been silent on (`okf/`, `guillotine/`, `service/`), and the Dashboard row now says what its `src/knotica/dashboard/` path actually holds; the `core/` row names the compile chain and the shared read/aggregate substrate it had been omitting. Prior: 2026-07-30 — MVP tree + Phase-2 `evals/` + Phase-3a `programs/`/compile/loop + notes-overlay Phase 1 (`core/notes/`, `capture_note`, `note_capture` tool, `notes` dispatcher's `list`/`read` actions, the dashboard Notes tab, `/knotica:note`, the `wiki-maintenance` note-routing section) all Built; `agent/` (SIA outer loop) Planned (Phase 3b); notes overlay's `fuzzy` resolution rung, `reanchor`/`detach`/`promote`/`archive`, and the eval bridge are Phase 2+, not yet on disk |

Knotica is an AI-maintained markdown wiki in an Obsidian vault. The **Claude client's LLM is the brain**;
the server exposes deterministic tools and holds no session state. Every vault mutation flows through a
single `VaultTransaction` (flock + atomic write + log append + secret-scrub + one git commit).

> **P-A/P-B/P-C Built — Loop-internals consolidation + tool-surface two-tier + transparency/routing (2026-07-21).** 
> Phase-A extracted four shared primitives from the loop's seams (`branch_namespaces.py`, `best_effort.py`, 
> `_run_arena_and_resolve`, `build_loop_runner` factory) — behavior-preserving refactor, characterization-test-gated.
> Phase-B collapses the 49-tool flat MCP surface into a two-tier architecture: **7 operator dispatchers** 
> (loop, branches, compile, datasets, arena, golden, vault_health) route domain-specific actions; **18 core 
> conversational tools** (read/write/query/status/ingest) form the base; 4 stragglers and `open_dashboard` 
> complete the surface. Total: **30 tools** (18 core + 4 stragglers + `open_dashboard` + 7 dispatchers). The
> 26 deprecated aliases the consolidation initially kept for a migration window were removed outright once
> the migration premise (external clients) never held for a single-consumer, self-operated project (see
> `dec-050`, superseding the relevant clause of `dec-045`).
> New `INVALID_ARGUMENT` error code for argument validation (distinct from cursor errors); 
> `wiki_status(view="scope")` provides the cheapest scope-check for client-side routing.
> **P-C Built** — four-layer conversational-routing architecture (skill symptom-detection + `_INSTRUCTIONS` stable-invariants-only + tool-description guards on mutating tools + vault prompts as sole evolvable substrate); SessionStart topic-awareness seed + attention-nudge (`knotica status --nudge`); per-client reliability tiers (Tier-1 Claude Code skill+hooks; Tier-2 Desktop instructions-only).
> Planned: **P-D** (loop service lifecycle + attention model).

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
| `search/` | `SearchBackend` protocol + `RipgrepBackend` — read-only full-text search with cursor paging. A result's `kind` is the path's folder family: `ResultKind` is an alias of `core.vault_layout.Family` (`"page"`/`"source"`/`"note"`) rather than a parallel literal, and `ripgrep._classify` derives `(topic, kind)` by delegating to `family_of`/`topic_of` instead of matching path segments itself — so a search result can never disagree with the rest of the codebase about what a path holds. This is the one place `search/` depends on `core/`: a single import of the `core.vault_layout` leaf (which imports nothing from `knotica`), so the edge is one-directional and acyclic | `src/knotica/search/` |
| `core/` | Vault semantics: `config`, `schema`, `page`/`links`, `lint`, `vcs`, `lock`, `scrub`, `records`, `template` (read-only packaged-template locator), `transaction.VaultTransaction`, the four `operations.*` writes, and the loop spine (`loop.LoopRunner` — observe/gate/heal; `loop_state` — persisted `LoopState`/`LoopStage`/`LoopDecision`; `loop_heartbeat` — runner-liveness file under `.knotica/locks/`; `loop_progress` — in-flight per-question eval progress, same locks dir). Operations are config-agnostic — `(store, vault_root, *semantic_args)`, resolving config only at the adapter boundary. **P-A consolidation (Built):** `branch_namespaces.py` owns the single source of truth for all five branch-prefix constants and classify/parse helpers (formerly scattered across four modules); `best_effort.py` owns the shared failure-isolation context manager for six fallback sites across `loop.py` and `source_gate.py`. **Compile chain:** `compile_run.py` (doctor → gate → clone → MIPRO → branch → post-eval), `compile_state.py` (progress the dashboard polls), `compile_promote.py`, `compiled.py` (the artifact under `.knotica/compiled/`), `query_engine.py` — the one answer path shared by the MCP `query` tool, the dashboard Ask pane, and the arena — plus `trainset.py`, `models_config.py`, and `prompt_diff.py`. **Shared read/aggregate substrate:** `status.py` and `doctor.py` (one aggregation rendered by both CLI and MCP), `metrics.py`, `datasets_inventory.py`, `golden_review.py`, `vault_scaffold.py`/`config_write.py`, `prompts.py`, `index_catalog.py`, `vault_metadata_tree.py`, `ingest_activity.py`, `text_reflow.py`, `topics.py` (topic *identity* against the store — `is_topic`, `require_topic`, and the shared `topic_directories` vault-root walk), `jsonl.py` (lenient JSONL reading for the append-only logs whose value is the rows that *do* parse) | `src/knotica/core/` |
| `core/vault_layout.py` | Vault folder families. Declares `RESERVED_TOP_LEVEL_NAMES` (the single declaration — `sources`, `notes`, the four root files, `.knotica`, `.git`), the family constants (`SOURCES_DIR`, `NOTES_DIR`, `TOP_LEVEL_FAMILY_DIRS`, `SCORED_FAMILIES`), and two pure classifiers — `family_of(rel_path) -> "page"|"source"|"note"` and `topic_of(rel_path) -> str`. Because `notes` is a reserved top-level name, topic enumeration excludes `notes/` for free — do not remove that membership without replacing the guarantee. Enumeration reads that constant through `core/topics.py` now; the four call sites that each carried their own reserved-name skip (`vault_metadata_tree`, `status`, `mcp_server.tools_read`, `service.manager`) no longer reference it directly. A path that is absolute, empty, or contains a `..` segment raises `ValueError`. Imports nothing from `knotica`, so any layer may depend on it | `src/knotica/core/vault_layout.py` |
| `core/notes/` | **Notes overlay, Phase 1 (Built):** `anchor.py` (note document model — frontmatter + `## Anchors` bullet grammar, pure string functions, tolerant parsing so a hand-typed note never raises), `resolve.py` (the read-time resolution ladder, steps 0–3: `unanchored`/`anchor-invalid`/`orphaned`/`exact`/`shifted`; a pure function of two text blobs, re-runs on every read so a resolver fix applies retroactively), `store.py` (`list_notes`/`read_note` — read-only enumeration + resolution, no lock, no `VaultTransaction`). Phase 1 fidelities: `span`/`page`/`topic` only; Phase 1 statuses: `exact`/`shifted`/`orphaned`/`unanchored` only — `fuzzy` and `block`/`section` fidelity have no producer yet (Phase 2) | `src/knotica/core/notes/` |
| `core/operations/capture_note.py` | **`capture_note` — the one-shot note write (Built):** validates topic/note/intent, plans the anchor (substring match against claimed pages, degrading to page or topic fidelity on ambiguity rather than guessing), then one `VaultTransaction` write. Anchoring never fails the call — a degraded anchor rides back as an `ANCHOR_DEGRADED` warning on a success envelope | `src/knotica/core/operations/capture_note.py` |
| `mcp_server/` | FastMCP adapter: read tools, mutating tools, resources, and prompts. Resolves config per call; delegates every mutation to `core.operations.*`; never writes the vault directly. **Notes overlay:** flat tool `note_capture` (`tools_notes.py`) and dispatcher `notes` registering **exactly** `action=list` and `action=read` — `drift`/`reanchor`/`detach`/`promote`/`archive` are a later phase and rejected with `INVALID_ARGUMENT`. The `notes` dispatcher is split into `tools_dispatch_notes.py` (thin router: MCP tool registration + dispatch) and `tools_dispatch_notes_actions.py` (per-action payload construction, argument validation, the resolved-anchor status vocabulary) ahead of the mutating actions landing | `src/knotica/mcp_server/` |
| `cli/` | `knotica` console entry point — self-registering subcommand registry (`init`/`mcp`/`doctor`/`status`/`migrate`/`prompt`/`guillotine`/`okf`/`eval`/`compile`/`datasets`/`loop`). Reads via `core` read functions; mutations only through `core.operations.*`; never writes the vault directly. `loop` (`cli/loop.py`) owns the watch/once/set-baseline entry to `core.loop.LoopRunner`, plus the heartbeat thread | `src/knotica/cli/` |
| `evals/` | Frozen-corpus evaluator (Phase 2, headless `knotica eval`): clones the vault at a pinned SHA, scores a topic's held-out golden set through `dspy.Evaluate` over a baseline runner + cached LLM-as-judge, composes one stable scalar, and appends a `MetricsRecord` to the *clone's* `metrics.jsonl` through `core.transaction` — never the live vault. `--bootstrap` stages synthetic golden candidates for human review (never auto-frozen). `run_eval(..., on_example=, on_substage=)` progress seams feed `core.loop_progress`. `error_capture.classify_error(exc) -> (error_class, detail)` (`"rate_limit_429"`/`"parse_error"`/`"other"`) is a small, dependency-light leaf module the runner and scorer seams both import to classify a caught per-example exception, at `src/knotica/evals/error_capture.py`. `train_bootstrap.bootstrap_trainset` cold-starts a fresh topic's `qa.jsonl` from its own entity pages (LLM-grounded, `source: seed_train`; displaced by curated records over time). `anthropic`+`dspy` are isolated in the `evals` extra, off the MCP launch path | `src/knotica/evals/` |
| `programs/` | Phase 3a DSPy query compile (MIPROv2 with bootstrap fallback) → JSON compiled artifact (`optimizer`/`fallback_reason` recorded on fallback; never fabricates a compile score without LLM credentials) + `CompiledRunner`; selected by `query_engine` behind the single MCP `query` tool | `src/knotica/programs/` |
| `okf/` | Native OKF conformance — Knotica as an OKF-compatible superset. One format model (`constants.py`, `slug.py`, `datetime_fmt.py`, `frontmatter.py`, `links.py`, `index.py`, `log_fmt.py`) with three verbs over it: `check.py` validates read-only, `export.py` writes a self-contained bundle **outside** the vault, and `repair.py` normalizes the live vault — the only mutator in the package, and it commits through `core.transaction.VaultTransaction` rather than writing or shelling out itself. `okf/` is the one non-adapter package covered by the raw-write scan in `tests/test_architecture_boundaries.py`, which is what keeps `repair.py` on that path. Reached from `knotica okf check\|export\|repair` and the `vault_health` dispatcher's `okf_check`/`okf_repair` actions | `src/knotica/okf/` |
| `guillotine/` | Memory Guillotine — claim-level retraction, demotion, and evidence audit. A read-only pipeline: `search.py` finds candidate mentions (skipping dot-folders and its own `<topic>/reports/guillotine/` output, so one trial's report never becomes the next one's evidence), `classify.py` assigns each passage a role (`ASSERTS`/`QUALIFIES`/`CONTRADICTS`/`REFUTES`/`QUOTES`/`MENTIONS`), `score.py` recommends a verdict against published thresholds, `patch.py` localizes the contested passage and renders a unified diff, `report.py` renders the Markdown/JSON artifacts, and `runner.run_guillotine` composes them. It **never rewrites page prose** — the diff is evidence for human review, never applied; re-grounding a weakened claim flows through the retracted-gap → discovery → approved-ingest path. Artifact persistence lives outside the package in `core/operations/guillotine.py`, which owns the `VaultTransaction`. Reached from `knotica guillotine` | `src/knotica/guillotine/` |
| `service/` | Loop-watcher OS-service lifecycle — install / uninstall / status, plus the supervised daemon. `manager.py` puts launchd (macOS; the live-verified target) and systemd `--user` (Linux; code-complete but untested — `status().verified` reports which you have) behind one interface, with an injectable runner over the `launchctl`/`systemctl` calls. `__main__.py` is the daemon the installed unit runs (`python -m knotica.service`): it loads `~/.config/knotica/.env` into the process environment for keys not already set — launchd and systemd start a near-empty environment and the eval credential is env-only — then supervises **every** configured topic in one process, re-reading the topic set from `config.toml` on each cycle, so a topic added after install needs no reinstall. Reached from `knotica service install\|uninstall\|status` | `src/knotica/service/`, `src/knotica/service/templates/` |
| Dashboard | Single-file Preact MCP client: MCP App (`ui://knotica/dashboard` + `open_dashboard`) and HTTP mount (`knotica mcp --http`). The Preact source is the repo-root `dashboard/` tree; `src/knotica/dashboard/__init__.py` is the loader that resolves the built artifact (wheel-packaged `app.html`, falling back to `dashboard/dist/index.html` when you are working from a checkout) so an installed user needs no Node toolchain. Both mounts import that loader — the dependency never runs the other way. **Notes overlay, Phase 1 (Built):** a Notes tab (`NotesPane.tsx`) — Browse view only (card list, intent + anchor-status filters, empty/loading/error/partial states, `[ Open in Obsidian ]` link rendering the vault-relative path). The drift-review queue, promotion dialog, and notes graph are Phase 2+ (no `reanchor`/`promote`/`archive` action to back them yet) | `dashboard/`, `src/knotica/dashboard/`, `src/knotica/mcp_server/app_ui.py` |
| Plugin layer | Claude plugin marketplace surface: manifests, eleven `/knotica:*` command aliases (`commands/*.md`, incl. `/knotica:loop` and, new for notes-overlay Phase 1, `/knotica:note`), SessionStart pre-warm hook, the maintenance skill (Phase 1 addition: a note-vs-gap-vs-source routing section), and the MCP server registration | `.claude-plugin/`, `commands/`, `hooks/`, `skills/wiki-maintenance/`, `.mcp.json` |

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
  `loop_progress.py`, plus the extracted siblings `arena.py`/`arena_resolve.py`, `candidate_gate.py`,
  `loop_factory.py`, `branch_*.py`, `loop_promote.py`, `loop_retry_backoff.py` (how long a failed
  eval waits), `loop_attempt.py` (whether a re-attempt records anything new — the identity that stops
  a permanent failure paying commits forever), `loop_cadence_config.py`, `best_effort.py`; CLI entry
  `src/knotica/cli/loop.py`.
- Topic identity ("is this name a topic?", "which topics are there?") → `src/knotica/core/topics.py`;
  its pure, store-free counterpart for *path* classification is `src/knotica/core/vault_layout.py`.
- Query compile → `src/knotica/core/compile_run.py` and siblings (`compile_state`, `compile_promote`,
  `compiled`, `query_engine`); the DSPy program itself is `src/knotica/programs/`.
- OKF conformance → `src/knotica/okf/` (`check`/`export`/`repair`); CLI entry `src/knotica/cli/okf.py`.
- Claim audit → `src/knotica/guillotine/`; its transaction-bearing wrapper is
  `src/knotica/core/operations/guillotine.py`; CLI entry `src/knotica/cli/guillotine.py`.
- Running the loop as an OS service → `src/knotica/service/` (`manager.py`, `__main__.py`,
  `templates/`); CLI entry `src/knotica/cli/service.py`.
- Dashboard → repo-root `dashboard/` (Preact source), `src/knotica/dashboard/` (packaged-artifact
  loader), `src/knotica/mcp_server/app_ui.py` + `http_app.py` (the two mounts).
- Plugin layer → repo root (`.claude-plugin/`, `.mcp.json`, `commands/`, `hooks/`, `skills/wiki-maintenance/`).

## 3b. MCP Tool Surface — Two-Tier Dispatcher Architecture (P-B, Built)

**Surface Reduction:** P-B consolidates the 49-tool flat surface into a two-tier dispatcher architecture:

| Tier | Surface | Tool Count |
|---|---|---|
| **Conversational core** | 18 direct tools: read/write/query/status/ingest/suggestions/guide; high semantic density | 18 |
| **Operator dispatchers** (P-B) | 7 domain-specific dispatchers routing actions; single entry point per domain | 7 |
| **Stragglers** | 4 tools not yet dispatched + `open_dashboard` | 5 |
| | **Total** | 30 |

**The Seven Dispatcher Tools (P-B):**

| Dispatcher | Actions | Wraps |
|---|---|---|
| `loop(action=...)` | `run_once` \| `run_eval` \| `set_baseline` \| `baseline_policy` \| `rebaseline` \| `cadence` | loop observation/gating/baseline/cadence management; `run_once` and `run_eval` are both two-phase (nonce-gated) — a single call never bills |
| `branches(action=...)` | `scoreboard` \| `promote_loop` \| `promote` \| `delete` | branch-based candidate/result management |
| `compile(action=...)` | `run` \| `status` \| `promote` | DSPy compile workflow |
| `datasets(action=...)` | `inventory` \| `records` \| `bootstrap` \| `bootstrap_train` \| `freeze` | trainset bootstrap/freeze/audit |
| `arena(action=...)` | `status` \| `history` | prompt-variant racing |
| `golden(action=...)` | `load` \| `save` | golden-set review |
| `vault_health(action=...)` | `doctor` \| `repair` \| `okf_check` \| `okf_repair` \| `lint` \| `metadata_tree` | vault integrity/indexing |

Each dispatcher validates its `action` enum and returns `INVALID_ARGUMENT` (new in P-B; see § Error Grammar below) for unrecognized values. Mutating actions accept optional `mode=dry-run|apply` (dry-run performs validation only; apply persists changes). All dispatchers are called with identical syntax: `dispatcher_name(action="action_name", ...)` — the action becomes the primary routing axis.

**Core 18 Conversational Tools:**

`read_page`, `search`, `list_links`, `backlinks` (read); `write_page`, `write_wikilink`, `create_topic`, `curate_example` (write); `store_source`, `read_source`, `mark_ingested`, `list_source_cache` (source management); `query` (headless compile runner); `wiki_status` (new `view="scope"` parameter in P-B for cheap routing checks); `gap_report` (client-as-brain gap reporting); `suggestions_read`, `suggestions_review` (approval queue); `source_ingest_open`, `source_ingest_submit` (P4 ingest); `read_protocol` (protocol pointer).

**New `wiki_status(view="scope")`:**

A new parameter-value pair enables cheap routing-scope checks without eval or compile snapshots. Returns `{schema_version, vault_name, topics[], totals}` — deterministic, stateless, vault-path-read only. Used by the client-side routing layer (P-C) to decide whether a detected wiki-relevant conversation should route to a dispatcher or stay in natural chat.

**Error Grammar — `INVALID_ARGUMENT`:**

A new error code (distinct from `INVALID_CURSOR`) signals argument validation failures — bad `mode`/`status`/`limit`/`action`/`reference_pages`/etc. — with argument-specific fix text. All dispatchers and mutating tools validate their inputs before execution and return this error envelope (not a raw exception) for unrecognized action values, missing required args, or out-of-range enums. Non-argument errors (e.g., vault corruption, network failure) use existing error codes.

**Dispatch Telemetry:**

Every dispatcher invocation logs a structured line `{tool, action, topic}` for observability, plus a rejected-action line for unrecognized `action` values. Logs support measurement of per-domain selection ambiguity (whether one dispatcher should revert to flat tools). Telemetry is deterministic and tied to invocation, not evaluation.

**Dependency Boundary (P-B):**

The seven dispatcher modules (`tools_dispatch_*.py`) import only their wrapped payload-helper modules (e.g., `tools_dispatch_loop` imports payload functions from `tools_vault`) and `core.errors`. No dispatcher imports another dispatcher; `dispatch_telemetry` is an import-cycle-free leaf. The former thin-tool modules (`tools_vault.py`, `tools_scoreboard.py`, `tools_compile.py`, `tools_datasets.py`, `tools_arena.py`, `tools_golden.py`) now hold only payload-helper functions and no `@mcp.tool` registrations — the 26 flat-tool aliases they used to register (kept for one release cycle per `dec-045`'s fifth ruling) were removed once the migration-window premise (external clients) never held for a single-consumer, self-operated project (`dec-050` partially supersedes `dec-045`; the topology rulings stand unchanged).

**Built (Phase P2, gap-fill discovery):** `src/knotica/discovery/` provides a pluggable
source-discovery layer — a `SearchProvider` protocol with an `httpx`-REST adapter (`YouComProvider` with bearer auth; Exa was cut by user directive but the protocol stays pluggable for future adapters), a separate
provider-agnostic OpenAlex enrichment pass stamping citation/venue/open-access metadata, and a deterministic
metadata-only reputability scorer — producing ranked, frozen `SourceCandidate` records for the loop's gap-fill
suggestion queue. It is a pure outbound-network boundary (no vault access, no LLM) and stays off the MCP
cold-start path. `discovery/normalize.py` is the package's identity leaf — `normalize_doi`, `normalize_url`,
and `source_key`, the single declaration of when two candidates are the same source (DOI when present, URL
otherwise). Three callers need that one rule: the service's dedup, the enricher's join, and `core/gapfill.py`'s
suggestion-queue dedup, which reaches it under the same lazy-import rule as the rest of `discovery/`. **Note:** the you.com API wire shape is documented from the public REST spec but not yet live-verified
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

`harness_evaluate` (`core/loop.py`, the production `evaluate` callable) also owns the single-writer
aggregation for per-example eval outcomes: one `threading.Lock` guards an in-memory `outcomes` list
and every progress write, so `on_outcome` events from `run_eval`'s concurrent scoring threads land
coherently — each write (whether triggered by an outcome, an example-start, or a substage heartbeat)
carries the full accumulated `examples` list, not just its own triggering event's data. This is the
`wiki_status`-visible `loop.progress.examples` list rendered by the dashboard's Loop pane.

**P-A Loop-internals consolidation (Built):** The loop's implementation uses shared extraction points to reduce seams and duplication: `src/knotica/core/branch_namespaces.py` is the single source of truth for all five branch-prefix constants (`loop/c/`, `loop/r/`, `loop/x/`, `loop/wip/`, `compile/`) and the branch-classification helpers (`classify_candidate`, `_parse_candidate_branch`, `_parse_wip_branch`) — formerly scattered across `loop.py`, `source_ingest.py`, `source_gate.py`, and `compile_promote.py`. `src/knotica/core/best_effort.py` owns a shared failure-isolation context manager that wraps the six previously-hand-written `try/except: pass|return None` sites across `loop.py` and `source_gate.py`, guaranteeing deterministic fallback behavior for operations that fail gracefully (discovery, pruning, ingest-activity reconciliation). `src/knotica/core/arena_resolve.py` (`run_arena_and_resolve`, a free function) unifies the arena race-and-resolve choreography between the prompt-healing and candidate-gating paths — extracted from `loop.py` in the `loop-py-extraction` pass (td-008). `src/knotica/core/loop_factory.py` (`build_loop_runner`, re-exported from `core/loop.py` for backward compatibility) is the factory that constructs `LoopRunner` instances, preserving each call site's current effective configuration values without convergence (unifying construction, deferring config policy unification to a future decision). `src/knotica/core/candidate_gate.py` holds the candidate-gate path (`poll_once`/`next_candidate`/`process_candidate`/`keep`/`discard`, free-functions-on-runner mirroring `source_gate.py`'s idiom) — `LoopRunner.poll_once`/`._keep` remain thin delegators. Despite these three extractions, `loop.py` is still over the 800-line ceiling (1087 lines, td-008 stays in-flight) — the deferred `observe_default` cluster is the next move.

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
`loop(action=baseline_policy)`); readable via `wiki_status.loop.baseline_policy`.

**Rebaseline from history** — `LoopRunner.rebaseline(mode)` (CLI `--rebaseline {best,latest}`, MCP
`loop(action=rebaseline)`) freezes a new baseline directly from `metrics.jsonl` with no eval: it restricts to
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

`--once` (CLI) skips the quiet window (an explicit one-shot invocation observes immediately) but still
respects the ingest hold. `loop(action=run_once)` (MCP) skips the quiet window too,
but — since the eval-cadence-and-billed-trigger work — require the same two-phase nonce confirm as
`run_eval` before observing: a bare call only returns a preview, never bills.

#### Eval cadence and model configuration (Built)

A global `[loop]` config table adds a throttle distinct from the settling debounce above:
`eval_min_interval_hours` (default 0 = current per-content-boundary behavior), `eval_window` quiet-hours, and `eval_num_threads`. A new `_cadence_hold` guard sits after `_observation_hold` in `observe_default()` only (candidate-gate evals stay eager); a failed eval re-arms instead of consuming the cursor (resolves td-011). A global `[models]` config table sets per-task model ids (worker, judge, query); worker/judge fold into `harness_version` for baseline refreezes on model change; query changes only the MCP query tool, never the frozen eval instrument. Cadence and a two-phase (nonce-gated) "run eval now" trigger are reachable from Claude Desktop via `loop` dispatcher actions `cadence` and `run_eval`. Full reference: [`docs/CLAUDE_DESKTOP.md` § Configuration](./CLAUDE_DESKTOP.md#configuration-models-and-eval-cadence).

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

## 3d. Notes Overlay (Phase 1, Built)

A **note** is the user's personal marginalia on a KB topic — never scored, never part of the wiki
corpus, never touched by the loop. It lives at `notes/<topic>/<YYYYMMDD-HHMMSS>-<slug>.md`, a
reserved top-level folder family (`core/vault_layout.py`) excluded by omission from
`SCORED_FAMILIES`, from `search`, and from the link graph the orphan-detector walks.

**Capture (`note_capture`, flat conversational tool).** One call, one commit: the client passes
the user's words verbatim as `note`, the passage it displayed as `quote`, and the pages it
synthesized that passage from as `pages`. `capture_note` (`core/operations/capture_note.py`)
substring-matches `quote` against the claimed pages' working-tree text and pins the strongest
anchor it can prove — `span` (unambiguous match), `page` (no quote, or a claimed page that could
not be read), or `topic` (no page claimed, or the quote matched more than one claimed page).
**Anchoring never fails the call**: every degradation rides back as an `ANCHOR_DEGRADED` warning
on a success envelope, never an error. The response's `placement` field is a pre-composed sentence
("Saved as a reflection, anchored to the passage you quoted in *alignment-failures* (exact).") so
the client never re-derives a location claim from `fidelity` × `status`.

**Recall and inspection (`notes` dispatcher).** Phase 1 registers **exactly** `action=list` and
`action=read` — both read-only, no lock, no commit. `list` is the only recall path (notes sit
outside the wiki corpus, so `search` never finds one); it filters by `intent`
(`reflection`/`dispute`/`gap`/`question`) and by resolved anchor `status`, paginates with an
opaque cursor, and returns `intent_counts`/`status_counts` for the whole topic. `read` returns one
note in full. The other five actions the interface design names — `drift`, `reanchor`, `detach`,
`promote`, `archive` — are Phase 2+; supplying one is rejected with `INVALID_ARGUMENT` rather than
silently accepted.

**Resolution ladder (`core/notes/resolve.py`, steps 0–3 of the design's five-step ladder).** A pure
function of `(historical_text, head_text, anchor)`, recomputed on every read rather than cached, so
a resolver improvement applies retroactively to every existing note:

| Status | Meaning |
|---|---|
| `unanchored` | No page was ever claimed at capture (no quote, an unreadable claimed page, or a quote matching several claimed pages) — the ordinary, common outcome of a degraded capture, not drift |
| `anchor-invalid` | The quote was never found in the page as it stood at `pinned_at` — a data-integrity outcome (hand-edited or forged), checked before any comparison to the live page |
| `orphaned` | A page *was* claimed but the page is gone, or the quote is gone from it — something the anchor once pointed at is now missing |
| `exact` | The quote occurs verbatim at its historical offset |
| `shifted` | The quote occurs verbatim at a different offset (the page moved but the passage survives) |

`fuzzy` (keyword/similarity matching past a verbatim miss) and `block`/`section` fidelity are
Phase 2 — the ladder stops at step 3 deliberately: an absent capability is simpler than a stub that
lies about being tested. `wiki_status` carries a per-topic `notes: {total, drifted}` count (drifted
= any anchor `orphaned`) consumed by the dashboard tab badge and the SessionStart nudge.

**Human surface.** A note is an ordinary Markdown file with YAML frontmatter and an optional
`## Anchors` section of real `[[wikilinks]]`, hand-writable in Obsidian with no special tooling —
see [`vault-template/SCHEMA.md`](../vault-template/SCHEMA.md) for the frontmatter fields and the
anchor-bullet grammar. `/knotica:note` and the dashboard's Notes tab (Browse view only — see the
Dashboard row in § 3) are the two client-side entry points; `skills/wiki-maintenance/SKILL.md`
carries the note-vs-gap-vs-source routing judgment.

## 4. Getting Started

Two install channels, both backing the same MCP server:

- **Claude Code plugin:** `/plugin marketplace add francisco-perez-sorrosal/bit-agora` →
  `/plugin install knotica@bit-agora` → `/knotica:setup`.
- **CLI + Claude Desktop:** `uv tool install --from . knotica` → `knotica init --desktop --yes`
  (first-time). `knotica desktop install` maintains the Desktop entry afterwards — it patches
  only `claude_desktop_config.json`, so it cannot re-point the active vault the way re-running
  the wizard would.
  Full Desktop + AWM use case: [`docs/CLAUDE_DESKTOP.md`](./CLAUDE_DESKTOP.md).
  Summary: [README](../README.md).

Development:

```
uv sync                     # install deps + the project (editable)
uv sync --extra evals       # when working on eval / compile
uv run pytest               # run the test suite
uv run knotica doctor       # deterministic health checks
uv run knotica mcp          # serve the MCP server over stdio
uv run knotica mcp --http   # + dashboard at http://127.0.0.1:8765/
```

The vault is a separate git repo at a user-configured path (dev default `~/dev/data/knotica`); never
hardcode vault paths — all access goes through `VaultStore`. Design canon:
[`docs/PRE_PLAN.md`](./PRE_PLAN.md).
