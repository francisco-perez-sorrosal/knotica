---
id: dec-008
title: Module boundaries and a single vault-mutation path
status: accepted
category: architectural
date: 2026-07-03
summary: Hexagonal core with one VaultTransaction context manager; all mutating ops (MCP tools, CLI, future loops) route through core.operations; store/search are protocols; mcp/cli adapters never write the vault directly.
tags: [architecture, module-boundaries, mutation, git, flock, invariant, phase-1]
made_by: agent
agent_type: systems-architect
branch: pipeline-wiki-mvp-core
pipeline_tier: standard
affected_files: [src/knotica/core/, src/knotica/store/, src/knotica/search/, src/knotica/mcp/, src/knotica/cli/]
affected_reqs: [REQ-MUT-01, REQ-MUT-02, REQ-MUT-03, REQ-MUT-04, REQ-MUT-05, REQ-TOOL-02]
dissent: A mutation-service object injected into adapters (rather than a core context manager the adapters call by function) would give richer lifecycle hooks for the Phase-3 loops, at the cost of a wider, more mockable seam.
re_affirms: dec-007
re_affirmed_by:
  - dec-046
---

## Context

The locked invariants (`CLAUDE.md`, PRE_PLAN §Settled decisions) require: one git commit per mutating
vault op, flock-guarded; a `VaultStore` abstraction so the backend is swappable (Archil-ready); a
stateless server; and — critically — that **all mutation paths share one code path** so MCP tools, the
CLI, and future headless DSPy/SIA loops cannot drift into inconsistent commit/log/lock behavior. The
module boundary must be drawn so this is structurally guaranteed, not merely conventional.

## Decision

Adopt a **hexagonal, single-writer core**:

- `store/` — `VaultStore` protocol + `LocalFSStore` (atomic temp+rename writes, read, list, delete). Pure
  storage; no git/log/schema knowledge. Innermost/most stable.
- `search/` — `SearchBackend` protocol + `RipgrepBackend`. Read-only.
- `core/` — all vault semantics: `config` (per-call resolution), `schema` (root+overlay), `page`/`links`,
  `lint`, `vcs` (git via `subprocess`), `lock` (`fcntl.flock`), `scrub`, `records` (frozen schemas), and
  the load-bearing **`transaction.VaultTransaction`** context manager plus `operations.*`
  (`write_page`, `store_source`, `create_topic`, `curate_example`, `migrate`), each of which opens a
  transaction.
- `cli/` and `mcp/` — thin interface adapters; both call `core.operations.*` for mutations and `core`
  read fns / `search` for reads. **Neither imports git/subprocess-for-git nor calls `store.write_*`
  directly.** The *only* writer of the vault is `core.transaction`.

`VaultTransaction` semantics: on enter acquire the vault flock (`.knotica/locks/vault.lock`, gitignored,
in-vault so it coordinates across processes and future Archil mounts); perform atomic writes via `store`;
append the `log.md` entry; secret-scrub; on clean exit make exactly one git commit with the structured
message `knotica(<op>): <topic> — <title>`; on exception restore the working tree to the pre-op commit;
always release the lock in `finally`.

The one-writer property is enforceable by an **import-boundary fitness test** (`mcp/` and `cli/` may not
import git bindings/subprocess-git or call `store.write_*`).

## Considered Options

### Option A — one `VaultTransaction` in core, adapters call by function (chosen)
- **Pros:** exactly one writer; the invariant is structural and grep/import-testable; adapters stay thin;
  reads bypass the transaction cleanly.
- **Cons:** a little indirection; reads that "could just open a file" must still go through `core`/`store`.

### Option B — a mutation-service object injected into each adapter
- **Pros:** richer lifecycle hooks (useful for Phase-3 loop instrumentation); explicit dependency.
- **Cons:** wider seam, more surface to mock, easier to bypass or duplicate discipline per adapter;
  weakens the "single code path" guarantee.

### Option C — git/lock inside `store/`
- **Pros:** one place owns persistence + versioning.
- **Cons:** conflates swappable storage with vault-level audit discipline; an Archil `store` that isn't
  git-backed would break the abstraction. Rejected — commit-per-op is a vault discipline, not a storage
  primitive.

## Consequences

- **Positive:** the load-bearing invariant is guaranteed by construction and testable; the D1 SDK swap and
  future backend swaps are confined by the boundary; Phase-3 loops inherit the same discipline for free.
- **Negative:** every write travels through `core.operations` — no shortcut writes; contributors must
  learn the transaction seam (documented in `docs/architecture.md`).

## Disconfirmation

- **Falsifier:** if the Phase-3 headless loops turn out to need mutation lifecycle hooks that a plain
  context manager cannot express cleanly (forcing per-loop bypasses), the single-context-manager shape was
  too rigid.
- **Steelmanned runner-up (Option B):** the loops are the whole point of knotica; giving them a first-class
  injected mutation service with open/prepare/commit/rollback hooks would make generation instrumentation,
  dry-run clones, and eval attribution natural, and DI could still be enforced with a shared base class.
- **Reversal trigger:** revisit if Phase-3 needs mutation hooks the context manager cannot host, or if the
  import-boundary fitness test starts requiring frequent exceptions (a sign the seam is in the wrong place).
