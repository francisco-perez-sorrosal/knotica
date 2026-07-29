---
id: dec-draft-04910c92
title: Expose evals as a PEP 621 extra (keep the group) for flexible headless install
status: accepted
category: architectural
date: 2026-07-28
summary: Add anthropic+dspy as a PEP 621 [project.optional-dependencies] evals extra alongside the existing PEP 735 dependency-group, so headless install is reachable via uvx --from '<src>[evals]', pip install knotica[evals], and uv sync --extra evals — partially superseding dec-013's group-only stance while re-affirming that the lean uvx cold-start path stays opt-in and unbloated.
tags: [evals, packaging, pep-621, pep-735, cold-start, uvx, headless, install-flexibility, mcp]
made_by: agent
agent_type: orchestrator
branch: feat-kb-config-install-flexibility
pipeline_tier: standard
affected_files: [pyproject.toml, .mcp.json, commands/headless.md, docs/CLAUDE_DESKTOP.md]
supersedes: dec-013
dissent: Declaring the eval deps in the wheel's optional-dependencies metadata reintroduces the exact leakage path dec-013 closed by construction — a downstream that installs knotica[evals], or a mis-set env marker, can now pull anthropic+dspy near the server where the group made that impossible; the isolation is now a matter of the launch command staying `--from <root>` (not `[evals]`) rather than an unforgeable metadata guarantee.
---

## Context

dec-013 placed `anthropic`+`dspy` in a PEP 735 `[dependency-groups] evals` rather than a `[project.optional-dependencies]` extra, precisely so the wheel `uvx --from ${CLAUDE_PLUGIN_ROOT} knotica mcp` resolves never declares the eval deps as installable metadata — the strongest possible cold-start isolation. That decision explicitly deferred the extra ("add an optional-extra then") and named its own reversal trigger: **"switch to (or add) an optional-extra if end-user-run eval enters scope."**

That trigger has now fired. The KB-config/install-flexibility work makes the server-side headless engine (`query`, `compile`, `loop`/Arena) a first-class, user- and agent-reachable capability on the Claude Code plugin channel — not just a maintainer's CLI tool. The user's requirement was explicit: the *best UX with least friction*, letting a user enable headless that **reuses the already-installed plugin** rather than re-fetching. The cleanest mechanism for that is `uvx --from '${CLAUDE_PLUGIN_ROOT}[evals]'`, which resolves the deps from the installed plugin copy — but the `[extra]` suffix requires a PEP 621 extra to exist. A dependency-group is not reachable through `uvx --from` or `pip install pkg[...]`; only an extra is.

## Decision

Add `[project.optional-dependencies] evals = ["anthropic>=0.116", "dspy>=3.2"]` as the **source of truth**, and retain the identically-named PEP 735 `[dependency-groups] evals` as a **compatibility alias** so existing `uv sync --group evals` dev/CI flows and docs keep working unchanged. This partially supersedes dec-013: its central "group, **not** an optional-extra" stance is replaced by "group **and** extra"; its core rationale — the eval deps must stay **opt-in and off the lean cold-start path** — is re-affirmed and unchanged (nothing is added to the base wheel resolution unless `[evals]` is explicitly requested). The plugin's own `.mcp.json` stays lean (`--from ${CLAUDE_PLUGIN_ROOT}`, no `[evals]`); headless is enabled per-surface out-of-band (a user-scoped `claude mcp add` override, Desktop's `--with` launch, or the documented `[evals]` reuse), never by default.

## Considered Options

### Option A — Extra as source of truth + group as alias (chosen)
- **Pros:** unlocks every install path at once (`uvx --from '<src>[evals]'` reuse-the-plugin, `pip install knotica[evals]`, `uv sync --extra evals`, Desktop `--with`); zero breakage (group alias preserves current call sites/docs); an extra is the semantically correct home for a user-facing optional capability (groups are a dev-tooling concept).
- **Cons:** duplicated package list across extra and group (two sources for the same fact until a later DRY cleanup); relaxes dec-013's "never in wheel metadata" guarantee.

### Option B — Keep group only (status quo, dec-013)
- **Pros:** strongest isolation by construction; single declaration.
- **Cons:** the reuse-the-plugin `[evals]` toggle is impossible; headless-from-the-plugin requires a full `git+https://` re-fetch or manual `--with anthropic --with dspy`; contradicts the now-in-scope flexibility requirement.

### Option C — Extra only (drop the group)
- **Pros:** single source of truth; no duplication.
- **Cons:** breaks every `uv sync --group evals` call site and doc in one step; churn not justified now (defer to a bounded cleanup).

## Consequences

- **Positive:** headless is one guided command (`/knotica:headless on`) or one `[evals]` suffix away, reusing the installed plugin; the lean default install is byte-for-byte unchanged (extra is opt-in); dev/CI unaffected (group alias).
- **Negative:** the isolation guarantee weakens from "unforgeable metadata" to "the launch command must stay `--from <root>`"; a downstream `pip install knotica[evals]` now resolves the heavy tree (by design, but it is a reachable path the group forbade); the extra/group duplication is a small ongoing DRY debt.

## Disconfirmation

- **Falsifier:** if a real install path is found where the plugin's lean server unintentionally pulls `anthropic`/`dspy` *because* the extra now exists in wheel metadata (e.g. a host that auto-installs all extras), the "lean cold-start preserved" claim is false and the group-only guarantee would have to be restored.
- **Steelmanned runner-up (Option B):** the group-only stance is the only mechanism that makes eval-dep leakage *structurally impossible* rather than *conventionally avoided*; if headless-from-Code were rare, the safer default (git+https re-fetch for the few who want it) would beat exposing metadata that every consumer can now resolve.
- **Reversal trigger:** revert to group-only (Option B) if the plugin's cold-start regresses due to the extra, or if a downstream is observed pulling the eval deps through the newly-declared metadata without explicitly asking for `[evals]`.

## Prior Decision

Supersedes `dec-013` (Eval dependency isolation via PEP 735 dependency-group, not an optional-extra). What changed: end-user/agent-run headless on the plugin channel entered scope, firing dec-013's own documented reversal trigger. dec-013's isolation *intent* (keep eval deps opt-in and off the lean `uvx --from` path) is re-affirmed verbatim; only its *mechanism* claim ("group, not an extra; never in wheel metadata") is superseded — the extra now coexists with the group. A future supersession would be warranted only if the extra is shown to erode the lean cold-start in practice (the falsifier above).
