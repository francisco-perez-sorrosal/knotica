# Architecture Guide

<!-- Developer navigation guide. Every component name and file path here is verified against the codebase;
     only components that exist on disk are listed as Built. Design rationale + planned components live in
     .ai-state/DESIGN.md; the converged design lives in docs/PRE_PLAN.md.
     Created by systems-architect; updated by implementer; verified by doc-engineer at checkpoints. -->

> **Status: core band Built (2026-07-03).** The `store/`, `search/`, and `core/` subpackages are
> implemented and green (full suite 510 passed / 18 skipped); the `cli/` and `mcp_server/` adapters
> are still docstring-only stubs (Phase 1c/1d not started). This guide gains present-tense component
> claims as modules land. For the design target and rationale, read
> [`.ai-state/DESIGN.md`](../.ai-state/DESIGN.md); for the full converged design, read
> [`docs/PRE_PLAN.md`](./PRE_PLAN.md).

## 1. Overview

| Attribute | Value |
|-----------|-------|
| **System** | Knotica — LLM-Wiki MVP |
| **Type** | Stateless MCP server + CLI over a versioned Obsidian vault; Claude plugin marketplace |
| **Language / Framework** | Python 3.12+ (uv) / official `mcp` SDK (`FastMCP`) |
| **Architecture pattern** | Hexagonal, single-mutation-core |
| **Last verified against code** | 2026-07-03 — core band Built (`store/`, `search/`, `core/`); `cli/` + `mcp_server/` still stubs |

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

**Built components (core band):**

| Component | Responsibility | Path (verified on disk) |
|---|---|---|
| `store/` | `VaultStore` protocol + `LocalFSStore` — atomic (temp+rename) storage primitives; no git/log/schema knowledge | `src/knotica/store/` |
| `search/` | `SearchBackend` protocol + `RipgrepBackend` — read-only full-text search with cursor paging | `src/knotica/search/` |
| `core/` | Vault semantics: `config`, `schema`, `page`/`links`, `lint`, `vcs`, `lock`, `scrub`, `records`, `transaction.VaultTransaction`, and the four `operations.*` writes. Operations are config-agnostic — `(store, vault_root, *semantic_args)`, resolving config only at the adapter boundary | `src/knotica/core/` |

The `cli/` and `mcp_server/` adapters are **still Designed** (docstring-only stubs, Phase 1c/1d not
started). The full module map — including Designed/Planned rows — is the authoritative table in
[`.ai-state/DESIGN.md` § 3](../.ai-state/DESIGN.md#3-components).

Navigation once built (planned homes):
- Vault mutation logic → `src/knotica/core/` (`transaction.py`, `operations/` — one module per op) — the single writer.
- Storage backend → `src/knotica/store/` (`VaultStore` protocol + `LocalFSStore`).
- Full-text search → `src/knotica/search/`.
- MCP server (tools/resources/prompts) → `src/knotica/mcp_server/` (named to avoid shadowing the `mcp` SDK package; see `dec-draft-8d8c18a1`).
- CLI (`init`/`mcp`/`doctor`/`status`/`migrate`) → `src/knotica/cli/`.
- Plugin layer → repo root (`.claude-plugin/`, `.mcp.json`, `commands/`, `hooks/`, `skills/`).

## 4. Getting Started

Pre-implementation — build steps land with Phase 1. The converged setup path is in
[`docs/PRE_PLAN.md` § First-run experience](./PRE_PLAN.md).
