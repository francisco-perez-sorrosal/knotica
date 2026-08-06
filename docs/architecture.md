# Architecture Guide

<!-- Developer navigation guide. Every component name and file path here is verified against the
     codebase; only what exists on disk is listed. Design rationale, invariants, planned components,
     and the data-flow narratives live in .ai-state/DESIGN.md; the converged design is docs/PRE_PLAN.md.
     Created by systems-architect; updated by implementer; verified by doc-engineer at checkpoints. -->

## 1. Overview

| Attribute | Value |
|-----------|-------|
| **System** | Knotica — an AI-maintained, compounding knowledge wiki |
| **Type** | Stateless MCP server + CLI over a versioned Obsidian vault; Claude plugin |
| **Language / Framework** | Python 3.12+ (uv) / official `mcp` SDK (`FastMCP`) |
| **Architecture pattern** | Hexagonal, single-mutation-core |
| **Last verified against code** | 2026-08-05 |

Verification is not a promise about a manual pass. `scripts/check_architecture_coverage.py` runs on
every `make verify` and fails when a package's module count drifts from `.ai-state/DESIGN.md` § 3a's
inventory, or when any `src/knotica/...` path cited in either architecture document stops resolving on
disk. Paths outside `src/knotica/` — `dashboard/`, `commands/`, `.claude-plugin/` — are ungated and
remain a matter for review.

Knotica is an AI-maintained markdown wiki in an Obsidian vault. The **Claude client's LLM is the brain**
for interactive work; the server exposes deterministic tools and holds no session state. Every vault
mutation flows through a single `VaultTransaction` — flock, buffered secret-scrubbed writes, atomic
apply, log append, and exactly one git commit.

## 2. System Context

The LikeC4 model at [`diagrams/architecture/src/architecture.c4`](diagrams/architecture/src/architecture.c4)
holds the component model and projects four views (`context`, `components`, `adapters`,
`selfImprovement`). Rendering needs `likec4` + `d2`; the command is in the model's header.

A user works the wiki through a Claude client (Code or Desktop) and reads or edits it directly in
Obsidian. The client reaches the knotica MCP server; the user also runs the `knotica` CLI. Both write
the vault — a separate git repo — only through `core.transaction`. Headless paths reach the Anthropic
Messages API; gap-fill discovery reaches you.com and OpenAlex. Deployment is out of scope (local-only).

## 3. Components

<!-- aac:generated source=docs/diagrams/architecture/src/architecture.c4 view=components last-regen=2026-08-05 -->

### 3a. Structural components

| Component | Responsibility | Path |
|---|---|---|
| `store/` | `VaultStore` protocol + `LocalFSStore` — atomic temp+rename primitives and the path-escape boundary (`PathOutsideVaultError`). No git, log, or schema knowledge | `src/knotica/store/` |
| `search/` | `SearchBackend` protocol + `RipgrepBackend`, ranking with live Okapi BM25 (k1=1.2, b=0.75, no index, byte-size length proxy). Falls back to a pure-Python markdown walk when `rg` is absent — both engines only choose candidate files; counting, snippets, and scoring run in one shared pass, so results match either way. `cursor.py` is the opaque pagination token (query + sort + offset + families, fail-closed); `retrieval.py` is the headless key-term retrieval both answer paths share | `src/knotica/search/` |
| `core/` | Vault semantics and the sole mutation path: `transaction`, `lock`, `vcs`, `scrub`, the vault vocabulary (`config`, `config_write`, `schema`, `page`, `links`, `lint`, `records`, `errors`, `template`, `vault_layout`, `topics`, `jsonl`), and the shared read/aggregate substrate both CLI and MCP render from (`status`, `doctor`, `metrics`, `prompts`, `datasets_inventory`, `golden_review`, `index_catalog`, `vault_metadata_tree`, `vault_scaffold`, `ingest_activity`, `text_reflow`, `baseline_probe`). Also holds the loop, compile, and gap-fill clusters — see § 3b | `src/knotica/core/` |
| `core/operations/` | The mutating operations. Nine open a `VaultTransaction`, exactly one each: `write_page`, `store_source`, `create_topic`, `curate_example`, `migrate`, `guillotine`, `capture_note`, `reanchor_note` (`reanchor`/`detach`/`archive`), `reflow_sources`. Two carry none of their own — `doctor_repair`, and `promote_note`, which delegates to `curate_example` or `gapfill.report_gap`. `candidate_scope.py` is a routing helper that sends a write onto a candidate worktree | `src/knotica/core/operations/` |
| `core/notes/` | Notes overlay model: `anchor` (document + append-only anchor history), `resolve` (the read-time resolution ladder), `candidates` + `scoring` (fuzzy candidate generation and scoring), `supersession` (page-replaced vs passage-reworded), `reconcile` (post-merge drift-queue notification), `store` (read-only enumeration) | `src/knotica/core/notes/` |
| `mcp_server/` | FastMCP adapter: 23 flat tools, 9 dispatchers, `open_dashboard`, resources, prompts. `vault_ctx.py` resolves config per call and maps house errors to envelopes; `envelope.py` holds the read/write mapper split. Delegates every mutation to `core.operations.*`. Named `mcp_server` to avoid shadowing the `mcp` SDK (`dec-009`) | `src/knotica/mcp_server/` |
| `cli/` | `knotica` console entry point — a self-registering registry whose `COMMAND_NAMES` tuple is the single declaration of the subcommand set: `init`, `desktop`, `mcp`, `doctor`, `status`, `migrate`, `prompt`, `guillotine`, `okf`, `eval`, `datasets`, `compile`, `loop`, `gapfill`, `service`. `common.py` holds the Console, exit codes, and the stdout=data / stderr=messages split | `src/knotica/cli/` |
| `evals/` | Frozen-corpus evaluator: clones at a pinned SHA, scores a held-out golden set through `dspy.Evaluate` over a baseline runner and a cached LLM-as-judge, composes one scalar, appends a `MetricsRecord` to the *clone*. `error_capture.py` classifies a per-example exception; `train_bootstrap.py` cold-starts a topic's `qa.jsonl`; `lexical.py` scores the compile post-eval comparison. `anthropic`/`dspy` live in the `evals` extra and import lazily | `src/knotica/evals/` |
| `programs/` | The DSPy query program — MIPROv2 with a bootstrap fallback, recording `optimizer`/`fallback_reason` on the artifact — plus `CompiledRunner`, selected by `query_engine` behind the single `query` tool | `src/knotica/programs/` |
| `discovery/` | Pluggable source discovery: a `SearchProvider` protocol with an httpx-REST adapter (`YouComProvider`, the sole adapter), a provider-agnostic OpenAlex enrichment pass (batched by DOI, ≤50/call), and a deterministic metadata-only reputability scorer. `normalize.py` is the identity leaf — DOI when present, URL otherwise. No vault access, no LLM, off the MCP cold-start path | `src/knotica/discovery/` |
| `okf/` | Native OKF conformance. One format model (`constants`, `slug`, `datetime_fmt`, `frontmatter`, `links`, `index`, `log_fmt`) with three verbs over it: `check` validates read-only, `export` writes a bundle **outside** the vault, `repair` normalizes the live vault through `core.transaction` — the package's only mutator. Reached from `knotica okf` and the `vault_health` dispatcher | `src/knotica/okf/` |
| `guillotine/` | Memory Guillotine — claim-level retraction, demotion, and evidence audit. A read-only pipeline: `search` finds mentions (excluding its own reports by default; `--include-reports` opts back in), `classify` assigns a `PassageRole`, `score` recommends a verdict against published thresholds, `patch` renders a unified diff, `report` renders the artifacts, `runner` composes them. It never rewrites page prose — the diff is evidence for human review. Persistence lives outside the package in `core/operations/guillotine.py` | `src/knotica/guillotine/` |
| `service/` | Loop-watcher OS-service lifecycle. `manager.py` puts launchd (macOS, live-verified) and systemd `--user` (Linux, code-complete but untested — `status().verified` reports which) behind one interface with an injectable runner. `__main__.py` is the daemon the unit runs: it loads `~/.config/knotica/.env` for keys not already set, then supervises **every** configured topic in one process, re-reading the topic set each cycle so a topic added after install needs no reinstall | `src/knotica/service/`, `src/knotica/service/templates/` |
| Dashboard | Single-file Preact MCP client mounted two ways: as an MCP App (`ui://knotica/dashboard` + `open_dashboard`) and over HTTP (`knotica mcp --http`). The Preact source is the repo-root `dashboard/` tree; `src/knotica/dashboard/__init__.py` resolves the built artifact (wheel-packaged `app.html`, falling back to `dashboard/dist/index.html` in a checkout) so an installed user needs no Node toolchain. Both mounts import that loader — never the reverse | `dashboard/`, `src/knotica/dashboard/`, `src/knotica/mcp_server/app_ui.py`, `src/knotica/mcp_server/http_app.py` |
| Plugin layer | `.claude-plugin/plugin.json`, `.mcp.json` (`uvx --from ${CLAUDE_PLUGIN_ROOT} knotica mcp`), one `/knotica:*` alias per file under `commands/`, `hooks/` (non-blocking SessionStart pre-warm + nudges), and `skills/wiki-maintenance/`. Distribution runs through the external `bit-agora` marketplace | `.claude-plugin/`, `commands/`, `hooks/`, `skills/wiki-maintenance/`, `.mcp.json` |

### 3b. Capabilities

Cross-cutting features composed from the components above; none owns a single directory.

| Capability | Where it lives |
|---|---|
| **Single-mutation vault write path** — flock → buffered secret-scrubbed writes → atomic apply → log append → one path-scoped commit | `src/knotica/core/transaction.py`, `lock.py`, `vcs.py`, `scrub.py`, `src/knotica/core/operations/` |
| **Autonomous loop lifecycle** — observe → gate → heal, per topic, on a clone | `src/knotica/core/loop.py` + `loop_state.py`, `loop_heartbeat.py`, `loop_progress.py`, `loop_factory.py`, `loop_promote.py`, `loop_retry_backoff.py`, `loop_attempt.py`, `loop_cadence_config.py`, `arena.py`, `arena_resolve.py`, `candidate_gate.py`, `branch_namespaces.py`, `branch_scoreboard.py`, `branch_delete.py`, `best_effort.py`; CLI `src/knotica/cli/loop.py` |
| **MCP tool surface** — 23 flat tools + 9 action dispatchers + `open_dashboard` | `src/knotica/mcp_server/tools_dispatch_*.py`, `src/knotica/mcp_server/dispatch_telemetry.py` |
| **Query compile & promote** — trainset → MIPROv2 on a clone → `compile/*` branch → review → promote | `src/knotica/core/compile_run.py`, `compile_state.py`, `compile_promote.py`, `compiled.py`, `query_engine.py`, `trainset.py`, `models_config.py`, `prompt_diff.py`; `src/knotica/programs/` |
| **Gap-fill spine** — diagnose → discover → approve → gated ingest | `src/knotica/core/gap_classifier.py`, `gapfill.py`, `gapfill_config.py`, `source_gate.py`, `source_ingest.py`; `src/knotica/discovery/`; `src/knotica/mcp_server/tools_suggestions.py`, `tools_source_ingest.py`; `src/knotica/cli/gapfill.py` |
| **Notes overlay** — capture → resolve → recall → correct → promote | `src/knotica/core/notes/`, `src/knotica/core/notes_config.py`, `src/knotica/core/operations/capture_note.py`, `reanchor_note.py`, `promote_note.py`; `src/knotica/mcp_server/tools_notes.py`, `tools_dispatch_notes.py`, `tools_dispatch_notes_read.py`, `tools_dispatch_notes_mutations.py`, `tools_dispatch_notes_common.py`; `dashboard/src/NotesPane.tsx` |

<!-- aac:end -->

### 3c. Navigation

- Vault mutation → `src/knotica/core/transaction.py` (the single writer) and `src/knotica/core/operations/`.
- Storage → `src/knotica/store/`. Full-text search and BM25 ranking → `src/knotica/search/ripgrep.py`;
  pagination → `src/knotica/search/cursor.py`; headless retrieval → `src/knotica/search/retrieval.py`.
- Topic identity ("is this a topic?", "which topics exist?") → `src/knotica/core/topics.py`; its pure,
  store-free counterpart for *path* classification is `src/knotica/core/vault_layout.py`.
- MCP tools, resources, prompts → `src/knotica/mcp_server/`; per-call config resolution and error
  mapping → `src/knotica/mcp_server/vault_ctx.py` and `envelope.py`.
- CLI → `src/knotica/cli/`; the subcommand registry is `src/knotica/cli/__init__.py`.
- Eval harness → `src/knotica/evals/`; the instrument fingerprint → `src/knotica/evals/config.py`.
- Loop → `src/knotica/core/loop.py` and its siblings; CLI entry `src/knotica/cli/loop.py`.
- Query compile → `src/knotica/core/compile_run.py` and siblings; the DSPy program → `src/knotica/programs/`.
- OKF conformance → `src/knotica/okf/`; CLI entry `src/knotica/cli/okf.py`.
- Claim audit → `src/knotica/guillotine/`; its transaction-bearing wrapper is
  `src/knotica/core/operations/guillotine.py`; CLI entry `src/knotica/cli/guillotine.py`.
- Running the loop as an OS service → `src/knotica/service/`; CLI entry `src/knotica/cli/service.py`.
- Dashboard → `dashboard/` (Preact source), `src/knotica/dashboard/` (artifact loader),
  `src/knotica/mcp_server/app_ui.py` + `http_app.py` (the two mounts).

## 4. Interfaces

**33 MCP tools** — 23 flat conversational tools, 9 operator dispatchers, and `open_dashboard`. Every
tool resolves config per call and returns a structured envelope rather than raising. Mutating
dispatcher actions take `mode=dry-run|apply`.

| Dispatcher | Actions |
|---|---|
| `loop` | `run_once` \| `run_eval` \| `set_baseline` \| `baseline_policy` \| `rebaseline` \| `cadence` |
| `branches` | `scoreboard` \| `promote_loop` \| `promote` \| `delete` |
| `compile` | `run` \| `status` \| `promote` |
| `datasets` | `inventory` \| `records` \| `bootstrap` \| `bootstrap_train` \| `freeze` |
| `arena` | `status` \| `history` |
| `golden` | `load` \| `save` |
| `notes` | `list` \| `read` \| `drift` \| `reanchor` \| `detach` \| `promote` \| `archive` |
| `vault` | `list` \| `status` \| `use` \| `add` \| `create` |
| `vault_health` | `doctor` \| `repair` \| `okf_check` \| `okf_repair` \| `lint` \| `metadata_tree` |

The flat tools, by module: `tools_read.py` — `list_topics`, `read_page`, `search`, `list_links`,
`lint_check`; `tools_write.py` — `write_page`, `store_source`, `create_topic`, `curate_example`;
`tools_status.py` — `wiki_status`, `metrics_read`, `baseline_probe`; `tools_suggestions.py` —
`suggestions_read`, `suggestions_review`, `gap_report`; `tools_source_ingest.py` —
`source_ingest_open`, `source_ingest_submit`; `tools_ingest.py` — `ingest_progress`,
`ingest_activity_read`; `tools_query.py` — `query`; `tools_notes.py` — `note_capture`;
`tools_prompt_diff.py` — `prompt_diff`; `tools_guide.py` — `read_protocol`.

Resources: `knotica://schema/root`, `knotica://schema/topic/{topic}`, `knotica://schema/resolved/{topic}`,
`knotica://index`, plus the `ui://knotica/dashboard` MCP-App resource. Prompts: `ingest`, `query`,
`lint`, `curate` — static names, lazily resolved bodies.

Each dispatcher validates `action` against its own `_ACTIONS` tuple and returns `INVALID_ARGUMENT` for
anything else; `INVALID_CURSOR` remains distinct. `wiki_status(view="scope")` is the cheapest
routing check — `{schema_version, vault_name, topics[], totals}`, deterministic and read-only.
`dispatch_telemetry.py` logs one line per invocation and one per rejected action.

`loop action=run_eval` and `loop action=run_once` are two-phase: a bare call returns a preview and a
nonce, and only a confirmed second call bills. `run_eval` passes `force=True` and so bypasses the
cadence hold; `run_once` honours it.

Python-level seams (`clone_to`, `LLMClient.complete`, `build_metric`, `run_eval`, `harness_version`,
the `LoopRunner` methods, the heartbeat/progress pairs) are specified in
[`.ai-state/DESIGN.md` § 4](../.ai-state/DESIGN.md#4-interfaces).

## 5. Data Flow

Data flows — the mutating and read paths, prompt resolution, note capture and resolution, the eval run,
unconfigured boot, and the watch tick, plus the baseline-transition table and the branch topology — are
described in [`.ai-state/DESIGN.md` § 5](../.ai-state/DESIGN.md#5-data-flow).

To start tracing: a mutation enters at an `@mcp.tool` in `src/knotica/mcp_server/` or a subcommand in
`src/knotica/cli/`, and every one of them converges on `src/knotica/core/transaction.py`. A loop tick
enters at `LoopRunner.observe_default` in `src/knotica/core/loop.py`.

## 6. Dependencies

Runtime, headless-extra, and dev dependencies with their versions and rationale are listed in
[`.ai-state/DESIGN.md` § 6](../.ai-state/DESIGN.md#6-dependencies), verified against `pyproject.toml`
and `uv.lock`.

## 7. Constraints

System invariants — and the fitness test or mechanism enforcing each — are listed in
[`.ai-state/DESIGN.md` § 7](../.ai-state/DESIGN.md#7-constraints). The two that most often surprise a
newcomer: `core.transaction` is the only caller of `store.write_text_atomic`/`delete` codebase-wide,
and the `notes` family is kept off scored surfaces by two different mechanisms (omission from
`SCORED_FAMILIES`, and explicit filters at each point of use) that must not be conflated.

## 8. Decisions

<!-- aac:authored owner=systems-architect last-reviewed=2026-08-05 -->

Architectural decisions are recorded as ADRs in [`.ai-state/decisions/`](../.ai-state/decisions/). The
canonical, auto-generated cross-reference is
[`DECISIONS_INDEX.md`](../.ai-state/decisions/DECISIONS_INDEX.md). For design-target rationale, see
[`.ai-state/DESIGN.md`](../.ai-state/DESIGN.md) — this guide intentionally does not summarize decisions
inline.

<!-- aac:end -->

## Getting started

Install, first run, and the command surface are in the [README](../README.md); the Desktop path is in
[`docs/CLAUDE_DESKTOP.md`](./CLAUDE_DESKTOP.md). For development:

```
uv sync --extra evals       # deps + the project (editable); the extra adds eval/compile
uv run pytest               # full suite
make verify                 # topology, ADR health, architecture coverage, mypy, tests, ruff
make test-groups            # list scoped test groups; make test-group GROUP=<id> runs one
uv run knotica doctor       # deterministic health checks
uv run knotica mcp          # serve over stdio
uv run knotica mcp --http   # + dashboard at http://127.0.0.1:8765/
```

The vault is a separate git repo at a user-configured path; never hardcode vault paths — all access
goes through `VaultStore`. Design canon: [`docs/PRE_PLAN.md`](./PRE_PLAN.md).
