# Architecture Guide

<!-- Developer navigation guide. Every component name and file path here is verified against the codebase;
     only components that exist on disk are listed as Built. Design rationale + planned components live in
     .ai-state/DESIGN.md; the converged design lives in docs/PRE_PLAN.md.
     Created by systems-architect; updated by implementer; verified by doc-engineer at checkpoints. -->

> **Status: MVP tree + Phase-2 `evals/` Built (2026-07-16).** The `store/`, `search/`, `core/`,
> `mcp_server/`, `cli/`, and `evals/` subpackages plus the plugin layer (`.claude-plugin/`, `commands/`,
> `hooks/`, `skills/wiki-maintenance/`, `.mcp.json`) are implemented on disk. `programs/` and `agent/`
> (Phase 3a+) remain Planned. Components appear in the Built table below only once they exist on disk. For
> the design target and rationale, read [`.ai-state/DESIGN.md`](../.ai-state/DESIGN.md); for the full
> converged design, read [`docs/PRE_PLAN.md`](./PRE_PLAN.md).

## 1. Overview

| Attribute | Value |
|-----------|-------|
| **System** | Knotica — LLM-Wiki MVP |
| **Type** | Stateless MCP server + CLI over a versioned Obsidian vault; Claude plugin marketplace |
| **Language / Framework** | Python 3.12+ (uv) / official `mcp` SDK (`FastMCP`) |
| **Architecture pattern** | Hexagonal, single-mutation-core |
| **Last verified against code** | 2026-07-16 — MVP tree + Phase-2 `evals/` Built (`store/`, `search/`, `core/`, `mcp_server/`, `cli/`, `evals/` + the plugin layer); `programs/`, `agent/` Planned (Phase 3a+) |

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
| `core/` | Vault semantics: `config`, `schema`, `page`/`links`, `lint`, `vcs`, `lock`, `scrub`, `records`, `template` (read-only packaged-template locator), `transaction.VaultTransaction`, and the four `operations.*` writes. Operations are config-agnostic — `(store, vault_root, *semantic_args)`, resolving config only at the adapter boundary | `src/knotica/core/` |
| `mcp_server/` | FastMCP adapter: read tools, mutating tools, resources, and prompts. Resolves config per call; delegates every mutation to `core.operations.*`; never writes the vault directly | `src/knotica/mcp_server/` |
| `cli/` | `knotica` console entry point — self-registering subcommand registry (`init`/`mcp`/`doctor`/`status`/`migrate`/`prompt`/`guillotine`/`okf`/`eval`). Reads via `core` read functions; mutations only through `core.operations.*`; never writes the vault directly | `src/knotica/cli/` |
| `evals/` | Frozen-corpus evaluator (Phase 2, headless `knotica eval`): clones the vault at a pinned SHA, scores a topic's held-out golden set through `dspy.Evaluate` over a baseline runner + cached LLM-as-judge, composes one stable scalar, and appends a `MetricsRecord` to the *clone's* `metrics.jsonl` through `core.transaction` — never the live vault. `--bootstrap` stages synthetic golden candidates for human review (never auto-frozen). `anthropic`+`dspy` are isolated in the `evals` dependency group, off the MCP launch path | `src/knotica/evals/` |
| Plugin layer | Claude plugin marketplace surface: manifests, eight `/knotica:*` command aliases, SessionStart pre-warm hook, the maintenance skill, and the MCP server registration | `.claude-plugin/`, `commands/`, `hooks/`, `skills/wiki-maintenance/`, `.mcp.json` |

The single-writer boundary (adapters never mutate the vault; the sole writer is `core.transaction`) is
enforced statically by `tests/test_architecture_boundaries.py`; `evals/` routes its metrics writes through
`core.transaction` on a clone, and the fitness test's `ADAPTER_PACKAGES` includes `evals`. The full module
map — including the Built `evals/` row (Phase 2) and the Planned `programs/`/`agent/` rows (Phase 3a+) — is
the authoritative table in [`.ai-state/DESIGN.md` § 3](../.ai-state/DESIGN.md#3-components).

Navigation:
- Vault mutation logic → `src/knotica/core/` (`transaction.py`, `operations/` — one module per op) — the single writer.
- Storage backend → `src/knotica/store/` (`VaultStore` protocol + `LocalFSStore`).
- Full-text search → `src/knotica/search/`.
- MCP server (tools/resources/prompts) → `src/knotica/mcp_server/` (named to avoid shadowing the `mcp` SDK package; see `dec-draft-8d8c18a1`).
- CLI (`init`/`mcp`/`doctor`/`status`/`migrate`/`prompt`/`guillotine`/`okf`/`eval`) → `src/knotica/cli/`.
- Eval harness (headless `knotica eval`, Phase 2) → `src/knotica/evals/` (`harness.run_eval` orchestrates; `scorer.build_metric` is the `dspy.Evaluate` metric; `judge`/`golden`/`config` are the instrument, devset, and pins).
- Plugin layer → repo root (`.claude-plugin/`, `.mcp.json`, `commands/`, `hooks/`, `skills/wiki-maintenance/`).

## 4. Getting Started

Two install channels, both backing the same MCP server (see the [README](../README.md) for the
end-user walkthrough):

- **Plugin:** `/plugin marketplace add francisco-perez-sorrosal/bit-agora` → `/plugin install knotica@bit-agora`
  → `/knotica:setup`.
- **CLI:** `uv tool install --from . knotica` → `knotica init`.

Development:

```
uv sync                     # install deps + the project (editable)
uv run pytest               # run the test suite
uv run knotica doctor       # deterministic health checks
uv run knotica mcp          # serve the MCP server over stdio
```

The vault is a separate git repo at a user-configured path (dev default `~/dev/data/knotica`); never
hardcode vault paths — all access goes through `VaultStore`. The converged setup path and rationale are
in [`docs/PRE_PLAN.md`](./PRE_PLAN.md).
