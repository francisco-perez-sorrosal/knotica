---
id: dec-013
title: Eval dependency isolation via PEP 735 dependency-group, not an optional-extra
status: accepted
category: architectural
date: 2026-07-15
summary: Place anthropic and dspy (dspy adopted now per the 2026-07-15 user override) in a PEP 735 [dependency-groups] evals rather than [project.optional-dependencies], so the built wheel that uvx --from resolves for the MCP server never declares the eval deps, giving strictly stronger cold-start isolation and matching the existing dev-group precedent.
tags: [evals, phase-2, dependencies, packaging, pep-735, cold-start, uvx]
made_by: agent
agent_type: systems-architect
branch: worktree-pipeline-eval-harness
pipeline_tier: standard
affected_files: [pyproject.toml]
affected_reqs: [REQ-DEP-01]
dissent: An optional-extra (pip install knotica[evals]) would make the eval tooling installable by an end user via standard packaging, which a dependency-group cannot; if end-user-run eval is ever in scope, the group choice has to be revisited or supplemented.
re_affirms: dec-007
---

## Context

The MVP's single measured operational risk is the cold `uvx --from ${CLAUDE_PLUGIN_ROOT} knotica mcp` env resolution (24.4 s with fastmcp-class deps; the whole `dec-007` SDK choice was made to shrink it). Phase 2 adds `anthropic` **and `dspy`** (dspy adopted now per the 2026-07-15 user override, `dec-012`; `dspy` pulls a large transitive tree incl. litellm) — deps that must **not** touch that launch path. The codebase sets no precedent for optional-extras: `pyproject.toml` has `[dependency-groups] dev` (PEP 735) and no `[project.optional-dependencies]` section at all. Two mechanisms are available under the `hatchling` backend: `[project.optional-dependencies] evals` (end-user-installable via `pip install knotica[evals]`; excluded from the base `uvx --from` resolution) or `[dependency-groups] evals` (PEP 735; dev-tooling-only, never shipped in the built distribution). The decision hinges on "who runs `knotica eval`?"

## Decision

Use **PEP 735 `[dependency-groups] evals = ["anthropic>=0.116", "dspy>=3.2"]`**, installed via `uv sync --group evals`. Phase-2 `knotica eval` is a **repo-dev / CI / local-maintainer** trigger (manual, local-only, gated on Phases 0-3 running smoothly locally per PRE_PLAN) — end users do not run eval in the MVP (client-as-brain; eval is the maintainer's objective-function tooling). A dependency-group gives *strictly stronger* cold-start isolation than an optional-extra: the built wheel that `uvx --from` resolves **never declares the eval deps as installable metadata at all**, so there is no path by which they enter the server's resolution. It also matches the existing `dev`-group shape exactly (consistency). When/if end-user-installable eval is ever in scope (not in MVP), add an optional-extra then.

## Considered Options

### Option A — PEP 735 `[dependency-groups] evals` (chosen)
- **Pros:** strongest cold-start isolation (never in the wheel's install metadata); matches the existing `dev` precedent; `uv`-native (`uv sync --group evals`); zero risk of leaking into `uvx --from`.
- **Cons:** not end-user-pip-installable (dependency-groups are a dev/workspace concept, not a distributable extra); a non-uv consumer cannot `pip install knotica --group evals`.

### Option B — `[project.optional-dependencies] evals`
- **Pros:** end-user-installable (`pip install "knotica[evals]"`); standard, portable across tools.
- **Cons:** declared in the wheel's metadata (weaker isolation — one mis-set env marker or a downstream that installs `[evals]` reintroduces the deps near the server); introduces the project's first optional-extras section (no precedent) for a need the MVP doesn't have.

## Consequences

- **Positive:** the 24.4 s cold-start risk is protected by construction even though `dspy` (a heavy tree) is now in the group — because the group is off the built wheel, so `uvx --from` resolution is unaffected and the heavier resolution is borne only by `uv sync --group evals`; the pattern mirrors `dev`; further additions are one line; `AC7` (launch path resolves neither `anthropic` nor `dspy`) is trivially satisfiable and testable.
- **Negative:** if the roadmap ever wants end-users to run eval locally without `uv`, this choice must be supplemented with an optional-extra; the eval tooling is a `uv`/repo-scoped capability for now.

## Disconfirmation

- **Falsifier:** if the Phase-1 cold-start drill / a later measurement shows optional-extras do **not** measurably worsen `uvx --from` resolution (the base resolution genuinely ignores extras), then the "strictly stronger isolation" rationale is marginal and the end-user-installability of Option B would dominate.
- **Steelmanned runner-up (Option B):** knotica's whole distribution story is a plugin + `uv tool install` for end users; making eval an optional-extra keeps a single, standard, tool-agnostic installation surface (`pip install "knotica[evals]"`) and future-proofs for the day a user wants to run their own eval locally — and since `uvx --from` resolves only the base package, the isolation is already adequate without the group's stricter guarantee.
- **Reversal trigger:** switch to (or add) an optional-extra if (a) end-user-run eval enters scope, (b) a non-`uv` consumer needs the eval deps, or (c) measurement shows extras don't affect the cold start (removing the isolation advantage of the group).

## Prior Decision

Re-affirms `dec-007`: that decision minimized the *server* dependency env to shrink the cold start; this decision keeps the new eval deps out of the server env **entirely**, extending the same cold-start-protection rationale to the Phase-2 addition rather than eroding it.
