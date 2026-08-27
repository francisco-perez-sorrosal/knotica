# Native OKF

Knotica vaults are **OKF-compatible supersets**: structurally compatible with [Open Knowledge Format (OKF) v0.1](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md) while preserving Knotica-specific affordances (Obsidian wikilinks, topic overlays, agent metadata).

OKF compatibility is not a migration target — it is the portable substrate beneath Knotica.

## Contents

- [Model](#model)
- [Frontmatter](#frontmatter)
- [Reserved files](#reserved-files)
- [Links](#links)
- [Commands](#commands)
- [What `okf repair` actually does](#what-okf-repair-actually-does)
- [Lint vs OKF check](#lint-vs-okf-check)
- [Module layout](#module-layout)

## Model

| Layer | Role |
|-------|------|
| **Native vault** | Working format. Wikilinks allowed. Knotica extension fields preserved. |
| **OKF export** | Portable artifact, built **outside** the vault. Wikilinks become standard Markdown links — except note embeds and block references (unless `--lossy-embeds`) and unresolved links, which are left as-is with a warning; `--export-ready` fails the export when any remain. `--pure` strips Knotica extensions. |

## Frontmatter

Every non-reserved `.md` file is a concept document. Minimum OKF requirement:

```yaml
---
type: concept
---
```

OKF does **not** restrict `type` to a fixed enum — any non-empty string is valid. Knotica's own taxonomy is *normalized* by `okf repair` (it rewrites Title-Case forms such as `Concept` → `concept`) but validated by nothing: any non-empty string passes both `okf check` and `okf repair`. It recognizes 16 values: `concept`, `paper`, `method`, `source`, `schema`, `benchmark`, `system`, `tool`, `entity`, `guide`, `report`, `playbook`, `metric`, `api`, `dataset`, `table`.

When `type` is missing, `okf repair` and `okf export` both infer it from the file's path:

| Path pattern | Inferred `type` |
|---|---|
| `sources/…` | `source` |
| `references/…` | `reference` |
| `SCHEMA.md` (any topic overlay) | `schema` |
| `START_HERE.md` | `guide` |
| `reports/…` | `report` |
| `playbooks/…` | `playbook` |
| `metrics/…` | `metric` |
| `apis/…` | `api` |
| `datasets/…` | `dataset` |
| `tables/…` | `table` |

`reference` is not one of the 16 taxonomy values above — Knotica only ever produces it from path inference (a `references/…` path) or from `okf repair` normalizing a Title-Case `Reference` on a page outside `sources/`.

Recommended OKF fields:

```yaml
title:
description:
resource:
tags:
timestamp: 2026-07-08T15:30:00Z   # RFC 3339 UTC
```

Knotica extensions (preserved in the native vault and the default export):

| Field | Meaning |
|---|---|
| `topic` | Topic directory the page belongs to. |
| `created` | RFC 3339 creation timestamp (UTC). |
| `updated` | RFC 3339 last-update timestamp (UTC). |
| `confidence` | `low` \| `medium` \| `high` — confidence in the page's claims. |
| `sources` | Citation keys of supporting sources under `sources/<topic>/`. |
| `status` | `active` \| `stale` — marks a page needing review. |
| `supersedes` | Page reference this one replaces. |
| `superseded_by` | Page reference that replaces this one. |
| `schema_version` | Integer record-schema version (root/topic overlays and the frozen machine-record formats). |
| `citation_key` | A source's citation key; also its filename under `sources/<topic>/`. |
| `origin_url` | Where a source came from; `okf repair` maps it to `resource` when `resource` is absent. |
| `source_type` | `html` \| `pdf` \| `markdown` \| `text` — a source's original format. |
| `retrieved` | ISO 8601 timestamp of when a source was retrieved. |
| `sha256` | Hex digest of the stored source body, sealing it against silent edits. |
| `ingested_by` | Model/agent identifier that performed the ingest. |
| `knotica_schema_version` | Preserved as an extension field; no code path sets it in this release. |

`--pure` export strips all of the above, keeping only `type`, `title`, `description`, `resource`, `tags`, `timestamp`, `citation_key`.

## Reserved files

| File | Rules |
|------|-------|
| `index.md` | No frontmatter. Markdown catalog body. |
| `log.md` | OKF date-grouped update log (`## YYYY-MM-DD`, newest first). Not a concept file. |

## Links

**Native vault:** wikilinks preferred (`[[agent-memory]]`, `[[topic/page|alias]]`).

**OKF export:** standard Markdown links (`[Agent Memory](/agentic-systems/agent-memory.md)`) — except note embeds, block references, and unresolved links, which are left as wikilinks with a warning.

## Commands

### CLI

```bash
knotica tend okf check
knotica tend okf export -o /tmp/knotica-okf
knotica tend okf repair --dry-run   # or --apply
```

All three subcommands need a configured vault; without one they fail the same way every `knotica` command does (`NOT_CONFIGURED`, fixable with `knotica init`).

| Subcommand | Flag | Default | What it does |
|---|---|---|---|
| `check` | `--strict` | off | Escalate unresolved and ambiguous links from warnings to failures. |
| `check` | `--export-ready` | off | Same escalation as `--strict`, plus fail if any wikilink remains — a preview of whether `okf export` would produce a fully Markdown-link-clean bundle. |
| `export` | `--output` / `-o` *(required)* | — | Bundle destination path, outside the vault. |
| `export` | `--pure` | off | Strip Knotica extension fields (see [Frontmatter](#frontmatter)). |
| `export` | `--link-style` | `bundle-relative` | `bundle-relative` (`/topic/page.md`) or `relative` (`../page.md`). |
| `export` | `--lossy-embeds` | off | Convert note embeds and block references to plain links, dropping embed semantics. |
| `export` | `--force` | off | Overwrite a non-empty output directory. |
| `export` | `--export-ready` | off | Fail the export itself if the resulting bundle isn't fully Markdown-link clean. |
| `repair` | `--dry-run` | — | Preview the fix plan; no writes. |
| `repair` | `--apply` | — | Write the fixes and commit. |
| `repair` | `--force` | off | Apply even when the vault has uncommitted changes (see [What `okf repair` actually does](#what-okf-repair-actually-does)). |
| all three | `--verbose` — must precede the subcommand: `knotica tend okf --verbose check` | off | Print per-warning / per-file detail. |

> [!IMPORTANT]
> `okf repair` requires exactly one of `--dry-run` or `--apply` — a bare `knotica tend okf repair` errors rather than defaulting to a preview.

### MCP

`vault_health` — one of Knotica's nine action-parameterized MCP dispatchers (see [architecture.md](architecture.md)) — carries two OKF actions:

| Action | Parameters | Equivalent to |
|---|---|---|
| `vault_health action=okf_check` | `strict` (bool, default `false`) | `knotica tend okf check [--strict]` |
| `vault_health action=okf_repair` | `mode` (`dry-run` default \| `apply`), `force` (bool, default `false`) | `knotica tend okf repair --dry-run` / `--apply [--force]` |

Both accept `vault` to target a non-default configured vault.

> [!IMPORTANT]
> OKF **export has no MCP path** — no dispatcher action calls it. A Desktop or Chat user can check and repair OKF compatibility through `vault_health`, but producing a portable bundle requires the CLI (`knotica tend okf export`).

## What `okf repair` actually does

Repair adds missing frontmatter and `type` (inferring it from the path when absent), normalizes timestamps to RFC 3339, removes the deprecated `knotica_kind` field, maps `origin_url` → `resource`, and canonicalizes `log.md`. It does **not** rewrite wikilinks to Markdown in the active vault — that conversion happens only at export.

Behavior a user will observe beyond the frontmatter fix:

| Behavior | Detail |
|---|---|
| Writes a report into the vault, and commits it | One Markdown report per run, under `.knotica/reports/okf/`, in the same commit as the fixes. |
| Requires a committable (git) vault | Refuses to apply without a `.git` directory — a repair with no commit would have no audit trail or rollback point. |
| Skips files with uncommitted changes | A dirty page is declined and listed as `skipped_dirty` rather than rewritten. `--force` does **not** override this — it only lets the run proceed at all on a dirty tree; the dirty pages themselves stay byte-identical and out of the commit. Commit or stash them and re-run to repair them. |
| Relocates legacy report directories | Reports found under the pre-fix `reports/okf/` location move to `.knotica/reports/okf/`, and the now-empty legacy directories are pruned. |
| Prints the rollback command | On `--apply`, the CLI prints `git revert <sha>` for the commit it just made. |
| Is a true no-op on a clean, already-compliant vault | Nothing to fix, relocate, or skip means no report and no commit — not a fresh empty report every run. |

## Lint vs OKF check

Knotica's mechanical lint (`lint_check` MCP tool, `vault_health action=lint`, `/knotica:lint`) and `knotica tend okf check` are separate gates with separate link-resolution policies: lint uses conservative same-directory wikilink resolution; `tend okf check` resolves through the same OKF link tiers `okf export` uses. They intentionally disagree on stricter link cases — a page can pass one and fail the other.

> [!NOTE]
> The lint engine has an internal `profile` parameter (`"knotica"` default, or `"okf"` — an OKF-shaped gate requiring only a non-empty `type`, skipping the orphan and source-citation checks). No CLI flag, MCP argument, or test anywhere in the codebase ever passes `profile="okf"` — every real entry point runs the default profile. Use `knotica tend okf check` for OKF-specific validation; the `okf` profile is not user-reachable.

## Module layout

One format model, three verbs over it:

```
src/knotica/okf/
  constants.py    # reserved filenames, field sets, type taxonomy, RFC 3339 regexes
  slug.py         # heading slug generation (wikilink -> Markdown anchor)
  datetime_fmt.py # RFC 3339 timestamp normalization
  frontmatter.py  # concept-file validation, normalization, type/title/description inference
  links.py        # InternalLink parse / resolve / export rewrite
  index.py        # VaultIndex -- vault-wide resolution index
  log_fmt.py      # log.md shape checks and native <-> OKF normalization
  check.py        # read-only compatibility checker (`okf check`)
  export.py       # bundle export, outside the vault (`okf export`)
  repair.py       # the package's only mutator -- fixes the live vault (`okf repair`)
```
