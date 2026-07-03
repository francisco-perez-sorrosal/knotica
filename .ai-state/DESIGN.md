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
| **Last verified** | 2026-07-03 by implementer (core-band checkpoint: store/search/core Built + green; cli/mcp_server still Designed — Phase 1c/1d not started) |

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
| `src/knotica/core/` | Vault semantics: `config`, `schema` (root+overlay), `page`/`links`, `lint`, `vcs` (subprocess git), `lock` (fcntl.flock), `scrub`, `records`, **`transaction.VaultTransaction`**, `operations.*` (four ops config-agnostic: `(store, vault_root, *semantic_args)` — no `core.config` import) | store, search | Built |
| `src/knotica/cli/` | `knotica` entry point: `init`, `mcp`, `doctor`, `status`, `migrate` — thin; mutations delegate to `core.operations` | core | Designed |
| `src/knotica/mcp_server/` | `FastMCP` server: tools, resources (schemas + index), prompts (static name / lazy body) — thin; stateless. *Named `mcp_server` (not `mcp`) to avoid shadowing the `mcp` SDK; per-concern modules `server`/`envelope`/`tools_read`/`tools_write`/`resources`/`prompts` (dec-draft-8d8c18a1)* | core | Designed |
| `src/knotica/programs/` | DSPy modules (`query` first) — Phase 3a | core | Planned |
| `src/knotica/agent/`, `evals/` | Headless runners + SIA-compatible evaluator — Phase 2–3 | core | Planned |
| Plugin layer (repo root) | `.claude-plugin/`, `.mcp.json`, `commands/`, `hooks/`, `skills/wiki-maintenance/` | `knotica mcp` entry | Designed |

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

## 5. Data Flow

**Mutating op:** `client → mcp/ tool → core.operations.<op> → resolve config (per call) →
VaultTransaction: flock → store.write_text_atomic → append log.md → scrub → vcs.commit (one commit,
msg `knotica(<op>): <topic> — <title>`) → release flock → Result`.

**Read op:** `client → mcp/ tool → core read fn / search backend → Result` (no lock, no commit).

**Prompt:** `client slash-command → prompts/get → lazy body: resolve config → unconfigured?
setup-guidance : read .knotica/prompts/<op>.md (topic override else root default) → body with full protocol`.

**Unconfigured boot:** server registers tools/prompts/resources with zero vault access; first call resolves
config and returns `unconfigured` until `init`/`setup` writes `config.toml` (picked up per call, no restart).

## 6. Dependencies

<!-- Implementer-owned: pin exact versions in pyproject.toml. -->
Floors, not pins: `mcp>=1.28` (resolves to 1.28.1 in `uv.lock`) — the sole runtime dependency.
Dev group: `pytest`, `ruff`. Build backend: `hatchling` (src layout; repo-root `vault-template/`
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
