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
| **Last verified** | 2026-07-16 by implementer (Phase-2 eval-harness checkpoint: `src/knotica/evals/` + `cli/eval.py` Built + green; full suite green; import-purity held — `import knotica.evals` pulls neither `anthropic` nor `dspy`). Prior: 2026-07-03 by orchestrator (Phase 1e: store/search/core/mcp_server/cli + plugin layer Built + green; 609 passed / 18 skipped; `claude plugin validate` ✔; `init`→`doctor` 9-pass smoke green) |

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
| `src/knotica/cli/` | `knotica` entry point: `init`, `mcp`, `doctor`, `status`, `migrate`, `prompt`, `guillotine`, `okf`, `eval` — thin, self-registering registry; mutations delegate to `core.operations`; never writes the vault directly. `eval` (Phase 2) resolves config and delegates to `evals.harness.run_eval` / `evals.golden.bootstrap` — it never mutates the vault itself | core | Built |
| `src/knotica/mcp_server/` | `FastMCP` server: tools, resources (schemas + index), prompts (static name / lazy body) — thin; stateless. *Named `mcp_server` (not `mcp`) to avoid shadowing the `mcp` SDK; per-concern modules `server`/`envelope`/`tools_read`/`tools_write`/`resources`/`prompts` (dec-draft-8d8c18a1)* | core | Built |
| `src/knotica/programs/` | DSPy modules (`query` first) — Phase 3a | core | Planned |
| `src/knotica/agent/` | Headless runners — Phase 3a+ | core | Planned |
| `src/knotica/evals/` | **Frozen-corpus evaluator (Phase 2):** hand-rolled `score(gold, pred, trace=None)` metric seam **run by `dspy.Evaluate`** over the golden devset (user override 2026-07-15; runner only — no optimizers/`dspy.LM`), via a `BaselineProgram(dspy.Module)` wrapping `BaselineRunner` (direct Messages API driving the clone's `query.md`). LLM-as-judge (pinned Opus, N-median, cached), deterministic citation integrity, hinged budget-relative cost-penalty scalar, golden-set bootstrap/freeze. Writes `metrics.jsonl` via `core.transaction` **on a clone** (never the live vault). `anthropic` + `dspy` isolated in the `evals` dep group (off the MCP launch path); LLM auth via env-only `ANTHROPIC_API_KEY`. *As-built modules: `llm` (LLMClient DI seam), `runner` (baseline query runner), `citations`+`scalar` (deterministic scoring), `cache`+`judge` (cached N-median LLM-as-judge), `scorer` (the `score` seam), `program` (BaselineProgram), `golden` (devset load/verify + bootstrap/freeze), `config` (packaged defaults + `harness_version` fingerprint), `harness` (`run_eval`); `cli/eval.py` is the `knotica eval` entry.* | core, `core.vcs.clone_to`, `evals` dep group (`anthropic`, `dspy`) | Built |
| Plugin layer (repo root) | `.claude-plugin/plugin.json`+`marketplace.json`, `.mcp.json` (`uvx --from ${CLAUDE_PLUGIN_ROOT} knotica mcp`), `commands/*.md` (8 `/knotica:*` aliases), `hooks/` (non-blocking SessionStart pre-warm + nudges), `skills/wiki-maintenance/` | `knotica mcp` entry | Built |

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
  network seam (a Protocol); `AnthropicClient` (real, env-only `ANTHROPIC_API_KEY`) and `FakeLLMClient`
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
client-as-brain (`dec-draft-8591febf`).

**Unconfigured boot:** server registers tools/prompts/resources with zero vault access; first call resolves
config and returns `unconfigured` until `init`/`setup` writes `config.toml` (picked up per call, no restart).

## 6. Dependencies

<!-- Implementer-owned: pin exact versions in pyproject.toml. -->
Floors, not pins: `mcp>=1.28` (resolves to 1.28.1 in `uv.lock`) — the sole runtime dependency.
Dev group: `pytest`, `ruff`. **Eval group** (Phase 2, PEP 735 `[dependency-groups] evals`, `uv sync --group evals`):
`anthropic>=0.116` (Messages API for the headless eval runner + judge; 0.116.0 verified PyPI 2026-07-15) **and
`dspy>=3.2`** (`dspy.Evaluate` as the per-example runner — user override 2026-07-15; 3.2.1 verified PyPI, requires-python
`<3.15`) — both declared **only** in the dependency-group so the built wheel never ships them and
`uvx --from … knotica mcp` never resolves them (so even dspy's heavy tree, incl. litellm, never touches the launch
path), protecting the 24.4 s cold start (`dec-draft-c2ad09bc`). Phase-3a adds DSPy optimizers to the same group.
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

- **dec-draft-6ea4e4f3** — MCP SDK: official `mcp` 1.28.1 over jlowin `fastmcp` v3 (cold-start dep-weight;
  canonicity; swap confined to `mcp/`).
- **dec-draft-9039d858** — Module boundaries + single vault-mutation path (`VaultTransaction`; one writer;
  import-boundary fitness test).
- **dec-draft-6ab0db31** — Config schema + unconfigured contract (per-call resolution; three-state machine).
- **dec-draft-e5cf9cf1** — Freeze record schemas at Phase 0 (qa/metrics/log/commit/provenance; per-record
  `schema_version`; documented in root `SCHEMA.md`).
- **dec-draft-75ee2605** — uvx cold-start pre-warm (setup foreground + background-idempotent SessionStart
  hook; never `alwaysLoad`).

Phase 2 — eval harness (this pipeline, `eval-harness`):

- **dec-draft-6fd2cfdf** — Hand-rolled `score()` metric core, **run by `dspy.Evaluate` as the per-example
  runner now** (user override 2026-07-15; runner only — no optimizers/`dspy.LM`); trace-branch float/bool.
- **dec-draft-8591febf** — Eval LLM access: direct Messages API behind `BaselineRunner` + pinned Opus judge;
  knotica-owned `ANTHROPIC_API_KEY` (env-only) = a new trust boundary distinct from client-as-brain.
- **dec-draft-229044ae** — Scalar = hinged, budget-relative, multiplicative `Q·(1−λ·hinge)`; citation
  validity deterministic-only (faithfulness deferred). Re-affirms record shape, revises additive formula clause.
- **dec-draft-d9e00da0** — Golden set: synthetic-from-pages + human review-freeze; held-out split from day
  one (`golden.jsonl` disjoint from `qa.jsonl`); `source: curate_example` (no enum change).
- **dec-draft-a6f575c0** — `metrics.jsonl` via `VaultTransaction` on the clone (`eval` op + log);
  reproducibility via `artifact_ref`→per-run manifest + `harness_version` fingerprint (no schema bump);
  fitness test extended to `evals/`.
- **dec-draft-c2ad09bc** — Eval deps in a PEP 735 `[dependency-groups] evals` (not an optional-extra) to
  guard the uvx cold start.
- **dec-draft-ee0f5832** — Frozen-corpus mechanism: `VaultVcs.clone_to` + SHA-pin + MANIFEST + determinism kit.
- **dec-draft-75eb597e** — Eval-harness module landing order: corrects the plan's Group B/C/D hints to the
  import-dependency graph (`cache` before `judge`, `scorer` after `judge`, `golden` split into read-side
  then bootstrap/freeze) so every module imports only already-landed siblings (no interface change).
