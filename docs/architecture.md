# Architecture Guide

<!-- Developer navigation guide. Every component name and file path here is verified against the codebase;
     only components that exist on disk are listed as Built. Design rationale + planned components live in
     .ai-state/DESIGN.md; the converged design lives in docs/PRE_PLAN.md.
     Created by systems-architect; updated by implementer; verified by doc-engineer at checkpoints. -->

> **Status: pre-implementation (2026-07-03).** No `src/` code exists on disk yet — the Built-component set
> is empty, so this guide currently carries **zero** present-tense component claims. It will be filled by
> the implementer as modules land. For the design target and rationale, read
> [`.ai-state/DESIGN.md`](../.ai-state/DESIGN.md); for the full converged design, read
> [`docs/PRE_PLAN.md`](./PRE_PLAN.md).

## 1. Overview

| Attribute | Value |
|-----------|-------|
| **System** | Knotica — LLM-Wiki MVP |
| **Type** | Stateless MCP server + CLI over a versioned Obsidian vault; Claude plugin marketplace |
| **Language / Framework** | Python 3.12+ (uv) / official `mcp` SDK (`FastMCP`) |
| **Architecture pattern** | Hexagonal, single-mutation-core |
| **Last verified against code** | 2026-07-03 — no code on disk (pre-implementation) |

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

**No components are Built yet.** The planned module map (all **Designed/Planned**, none on disk) is the
authoritative table in [`.ai-state/DESIGN.md` § 3](../.ai-state/DESIGN.md#3-components). As modules land
under `src/knotica/`, the implementer moves each row here with a verified file path and present-tense
description, keeping this guide a strict subset of the Built components in `DESIGN.md`.

Navigation once built (planned homes):
- Vault mutation logic → `src/knotica/core/` (`transaction.py`, `operations.py`) — the single writer.
- Storage backend → `src/knotica/store/` (`VaultStore` protocol + `LocalFSStore`).
- Full-text search → `src/knotica/search/`.
- MCP server (tools/resources/prompts) → `src/knotica/mcp/`.
- CLI (`init`/`mcp`/`doctor`/`status`/`migrate`) → `src/knotica/cli/`.
- Plugin layer → repo root (`.claude-plugin/`, `.mcp.json`, `commands/`, `hooks/`, `skills/`).

## 4. Getting Started

Pre-implementation — build steps land with Phase 1. The converged setup path is in
[`docs/PRE_PLAN.md` § First-run experience](./PRE_PLAN.md).
