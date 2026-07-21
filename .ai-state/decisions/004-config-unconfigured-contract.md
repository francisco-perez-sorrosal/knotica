---
id: dec-004
title: Config schema and the unconfigured-state contract
status: accepted
category: architectural
date: 2026-07-03
summary: config.toml (schema_version + default_vault + named vaults) resolved per tool call; a three-state machine (UNCONFIGURED/CONFIGURED_NO_VAULT/READY) collapsing to one user-facing unconfigured result with specific remediation.
tags: [config, unconfigured-boot, stateless-server, phase-1]
made_by: agent
agent_type: systems-architect
branch: pipeline-wiki-mvp-core
pipeline_tier: standard
affected_files: [src/knotica/core/config.py, src/knotica/mcp/, src/knotica/cli/]
affected_reqs: [REQ-CFG-01, REQ-CFG-02, REQ-CFG-03, REQ-CFG-04]
dissent: Caching the resolved config with an mtime check (instead of re-reading per call) would cut per-call I/O, at the cost of a subtle staleness window that could break setup-without-restart.
re_affirmed_by:
  - dec-036
  - dec-037
---

## Context

The plugin channel's `.mcp.json` is static, so the server discovers the vault via `config.toml`, not CLI
args (PRE_PLAN §Configuration). The plugin starts the server *before* any config/vault exists, so the
server must boot cleanly unconfigured, and `/knotica:setup` writing the config must take effect **without a
server restart**. The server is stateless — vault + config are the only state, resolved per call.

## Decision

**Schema** (`~/.config/knotica/config.toml`):

```toml
schema_version = 1              # config schema version (distinct from vault SCHEMA.md schema_version)
default_vault  = "main"

[vaults.main]
path = "~/dev/data/knotica"     # ~ and $ENV expanded at resolution time

[vaults.papers]                 # optional additional named vaults
path = "~/dev/data/other-wiki"
```

**Resolution** is per tool call: read `config.toml` fresh; pick `vault=<name>` arg if given else
`default_vault`; expand the path. **Three states**, collapsing to one user-facing contract:

- `UNCONFIGURED` — no `config.toml`, or `default_vault` unresolvable.
- `CONFIGURED_NO_VAULT` — path missing or not a knotica vault (no root `SCHEMA.md` / not a git repo).
- `READY` — path resolves to an initialized vault (git repo + root `SCHEMA.md` with `schema_version`).

Tools/prompts require `READY`; otherwise they return a **structured** `unconfigured` result
(`status: "unconfigured"`, `message`: the specific remediation — run `/knotica:setup` or `knotica init`)
— never an exception. `knotica doctor` reports which of the three states holds for diagnostics.

## Considered Options

### Option A — per-call fresh read, three-state collapse (chosen)
- **Pros:** setup takes effect with no restart; graceful boot is trivial; matches stateless-server.
- **Cons:** re-reads a small TOML each call.

### Option B — resolve-once-at-boot + reload on signal
- **Pros:** no per-call I/O.
- **Cons:** breaks setup-without-restart unless a reload mechanism is added; more moving parts; conflicts
  with graceful-boot-before-config-exists.

### Option C — mtime-cached resolution
- **Pros:** cuts per-call I/O while preserving reload.
- **Cons:** a staleness window and added complexity for a cost that is negligible at MVP scale. Deferred
  as a later optimization, not MVP.

## Consequences

- **Positive:** one user-facing "not set up" contract for three underlying states; no restart choreography;
  stateless-server preserved.
- **Negative:** trivial repeated TOML parsing (acceptable; optimizable later per Option C).

## Disconfirmation

- **Falsifier:** if per-call TOML parsing shows up as measurable latency in the cold-start drill or under
  loop load, per-call resolution was the wrong default.
- **Steelmanned runner-up (Option C):** an mtime-cached resolver keeps setup-without-restart (reload on
  mtime change) while removing redundant parses — strictly better if per-call I/O ever matters.
- **Reversal trigger:** adopt Option C if profiling shows config resolution is non-trivial, or once
  headless loops hammer the resolver.
