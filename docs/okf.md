# Knotica Native OKF

Knotica vaults are **OKF-compatible supersets**: structurally compatible with [Open Knowledge Format (OKF) v0.1](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md) while preserving Knotica-specific affordances (Obsidian wikilinks, topic overlays, agent metadata).

## Model

| Layer | Role |
|-------|------|
| **Native vault** | Working format. Wikilinks allowed. Knotica extension fields preserved. |
| **OKF export** | Portable artifact. Standard Markdown links only. Optional `--pure` strips extensions. |

OKF compatibility is not a migration target — it is the portable substrate beneath Knotica.

## Frontmatter

Every non-reserved `.md` file is a concept document. Minimum OKF requirement:

```yaml
---
type: concept
---
```

OKF does **not** restrict `type` to a fixed enum — any non-empty string is valid. Knotica uses its own taxonomy directly in `type`:

```yaml
type: concept
type: paper
type: method
type: source
type: schema
```

Recommended OKF fields:

```yaml
title:
description:
resource:
tags:
timestamp: 2026-07-08T15:30:00Z   # RFC 3339 UTC
```

Knotica extensions (preserved in native vault and default export):

```yaml
topic: agentic-systems
created: 2026-07-03T00:00:00Z
updated: 2026-07-08T15:30:00Z
confidence: high
sources: [wang2024awm]
status: active
```

Sources use `type: source` with `resource` mapped from `origin_url` when absent.

## Reserved files

| File | Rules |
|------|-------|
| `index.md` | No frontmatter. Markdown catalog body. |
| `log.md` | OKF date-grouped update log (`## YYYY-MM-DD`, newest first). Not a concept file. |

## Links

**Native vault:** wikilinks preferred (`[[agent-memory]]`, `[[topic/page|alias]]`).

**OKF export:** standard Markdown links (`[Agent Memory](/agentic-systems/agent-memory.md)`).

## Commands

```bash
knotica okf check                    # native OKF compatibility
knotica okf check --strict           # fail on broken/ambiguous links
knotica okf check --export-ready     # preview export cleanliness

knotica okf export -o /tmp/knotica-okf
knotica okf export --pure -o /tmp/knotica-okf-pure
knotica okf export -o /tmp/out --link-style relative

knotica okf repair --dry-run         # preview structural fixes
knotica okf repair --apply           # fix active vault (one git commit)
```

Repair adds missing frontmatter/`type`, normalizes timestamps to RFC 3339, removes deprecated `knotica_kind`, and maps `origin_url` → `resource`. It does **not** rewrite wikilinks to Markdown in the active vault.

## Lint vs OKF check

- `lint_vault(profile="knotica")` (default) — full Knotica mechanical checks with conservative same-directory wikilink resolution.
- `lint_vault(profile="okf")` — OKF gate: non-empty `type`, OKF link-resolution tiers, log path checks; `SCHEMA.md` / `START_HERE.md` exempt from Knotica core fields.

`knotica okf check` remains the native OKF compatibility command; Knotica lint and OKF check intentionally use different link policies.

## Module layout

```
src/knotica/okf/
  check.py       # compatibility checker
  export.py      # bundle export
  repair.py      # vault repair
  frontmatter.py # validation and normalization
  links.py       # InternalLink parse/resolve/export rewrite
  index.py       # VaultIndex
  log_fmt.py     # log.md normalization
```
