---
id: dec-108
title: Discovery dedups against the vault, and URL identity canonicalizes host-known edition permalinks
status: accepted
category: behavioral
date: 2026-08-30
summary: One host-aware URL canonicalization rule in the shared identity leaf collapses SEP archive editions everywhere source_key is asked; the service sanitizes candidate URLs (syntactic floor, no reachability probe); and the drain drops candidates the vault already stores, counted as candidates_already_in_vault
tags: [gap-fill, discovery, dedup, url-canonicalization, fill, honesty]
made_by: agent
agent_type: orchestrator
branch: main
pipeline_tier: lightweight
affected_files:
  - src/knotica/discovery/normalize.py
  - src/knotica/discovery/service.py
  - src/knotica/core/source_inventory.py
  - src/knotica/core/gapfill.py
---

# Discovery dedups against the vault, and URL identity canonicalizes host-known edition permalinks

## Context

A field report joined three defects into one shape. Discovery proposed the SEP
"Bounded Rationality" entry fourteen times against a vault that had ingested it
weeks earlier (`sources/<topic>/` provenance records the `origin_url`, but the
drain never read it). Nine *archive editions* of that one entry
(`/archives/win2018/…` through `/archives/win2024/…`) counted as nine
independent sources and out-ranked each other across three gaps. And five of
those URLs carried a broken-case `archIves` segment straight from the provider,
staged unvalidated.

## Decision

- **Identity**: `discovery/normalize.py` — already the single declaration of
  "when are two candidates the same source", consumed by the service dedup, the
  OpenAlex join, and the suggestion-queue dedup — gains a host-scoped
  canonicalization table (`canonicalize_url`). The one rule today collapses SEP
  archive editions to the living entry, case-insensitively on the archive
  segment. `normalize_url` canonicalizes first, so every existing consumer —
  and every *already staged* archive-edition record, re-keyed at dedup time —
  collapses by construction.
- **Sanitize stage**: `DiscoveryService.discover` drops candidates failing a
  syntactic URL floor (`is_http_url`: http/https + host) and rewrites survivors'
  stored URLs to canonical form — one seam covering every provider, present and
  future.
- **Vault dedup**: the drain reads `core/source_inventory.stored_source_url_keys`
  (normalized `origin_url`/`resource` of every stored source) and skips
  candidates the vault already holds, counting them as
  `candidates_already_in_vault` on `RefreshResult`, surfaced by the CLI, the MCP
  discover payload, and the dashboard's discover outcome sentence. The
  comparison is URL-to-URL, not `source_key`: stored provenance records no DOI,
  and DOI-first keying would let a DOI-carrying candidate slip past a
  URL-recorded ingest of the same source.

## Considered Options

### Shared-leaf canonicalization + service sanitize + drain-side vault dedup (chosen)

- Pro: the identity change lands once and reaches all three dedup sites plus
  historical records; the vault check sits at the only seam that has both the
  store and the discovery output.
- Con: `core/gapfill.py` grows again (documented ratchet raise; td-042 owns the
  split).

### Reachability probe at discovery time

- Rejected: a network HEAD per candidate adds latency and nondeterminism to a
  billed two-phase path, and canonicalization already repairs the observed
  malformed URLs. Syntactic-only validation is recorded in `is_http_url`'s
  docstring as deliberate.

### Per-provider URL validation in each adapter

- Rejected: each future adapter would have to remember it; the service stage
  covers all of them once.

### General www/edition stripping in `normalize_url` without a host table

- Rejected: over-normalization merges genuinely different sources (`/archives/`
  paths on other hosts, `www.` semantics elsewhere); the module's own docstring
  warns against exactly this, so rules stay host-scoped.

## Consequences

- Editions of one SEP entry stage once, under the canonical URL; a candidate
  matching an ingested source never reaches the queue, and the drain says how
  many it dropped rather than staging little in silence.
- A reopened gap can still be re-sourced: rejected records do not dedup
  discovery, and the vault check matches only *stored* sources.
- Adding the next many-URLs-one-source host (e.g. another encyclopedia's
  edition scheme) is one entry in the host table plus tests, with every
  consumer inheriting it.
