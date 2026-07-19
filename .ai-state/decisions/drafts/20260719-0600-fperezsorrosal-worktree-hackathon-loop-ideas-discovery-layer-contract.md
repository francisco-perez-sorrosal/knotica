---
id: dec-draft-f4584c2f
title: Gap-fill discovery contract — SearchProvider protocol, frozen SourceCandidate, separate batched enrichment
status: proposed
category: architectural
date: 2026-07-19
summary: The discovery layer exposes a SearchProvider protocol producing a frozen, schema_versioned SourceCandidate; reputability metadata is stamped by a separate provider-agnostic OpenAlex enrichment pass (batched by DOI), and reputability is a deterministic metadata-only tier + score — never textual.
tags: [gapfill, discovery, protocol, schema, reputability, enrichment, phase-p2, forward-compat, frozen-contract]
made_by: agent
agent_type: systems-architect
branch: worktree-hackathon-loop-ideas
pipeline_tier: standard
affected_files:
  - src/knotica/discovery/records.py
  - src/knotica/discovery/provider.py
  - src/knotica/discovery/openalex.py
  - src/knotica/discovery/reputability.py
  - src/knotica/discovery/service.py
dissent: A per-adapter fused enrichment would avoid the DOI-join pass and keep each provider self-sufficient, at the cost of duplicated, un-batched OpenAlex calls.
---

## Context

P2 of the autoresearch gap-fill spine needs a pluggable source-discovery layer whose output (`SourceCandidate`)
is later joined by P3 against the P1 classifier's gap records. The schema P2 freezes is a cross-pipeline contract:
P3 consumes it through JSONL serialization and must not be forced into rework by silent P2 shape changes. Three
provider families exist (general web: Exa, you.com; scholarly-native: OpenAlex/Semantic Scholar/Crossref), and only
the scholarly family surfaces reputability metadata (citations, venue, open-access, FWCI). Reputability scoring must
be ungameable (a prior-art pitfall: textual/stylistic credibility signals are demonstrably gameable) and
client-as-brain-clean (no LLM anywhere in this layer).

## Decision

Expose a single `runtime_checkable` `SearchProvider` protocol (`search(query) -> list[SourceCandidate]`), mirroring
the codebase's `VaultStore`/`SearchBackend` protocol pattern. `SourceCandidate` is a frozen, slotted dataclass
carrying `schema_version = 1` (dec-006 precedent), universal fields (`url`/`title`/`snippet`/`source_provider`) and
optional scholarly fields defaulting to `None`. Search adapters **only originate** candidates; a **separate,
provider-agnostic** `OpenAlexEnricher` (implementing an `Enricher` protocol) stamps `citation_count`/`venue`/
`is_open_access`/`fwci`/`published_date` onto the deduped candidate set via **batched** DOI lookups
(`filter=doi:a|b|c`, ≤50 per OpenAlex request). A `ReputabilityScorer` assigns a `ReputabilityTier`
(peer_reviewed > preprint_known_lab > established_org > general_web) plus a deterministic `[0,1]` score computed
**only from metadata** (venue/domain tier + citation bucket + recency). `DiscoveryService.discover` composes
search → dedup → enrich → score → rank with a total deterministic order `(tier_rank, -score, url)`. The candidate
is **self-contained**: it carries no gap/question/topic linkage — that join is P3's responsibility.

## Considered Options

### Option 1 — Fused per-adapter enrichment
Each provider adapter internally calls OpenAlex to stamp reputability before returning.
- Pros: each adapter is self-sufficient; no separate pass; no DOI-join orchestration.
- Cons: enrichment logic duplicated across every adapter; one OpenAlex call **per candidate per adapter** (no
  batching → far more requests against a USD-credit-budgeted free tier); reputability inconsistent if adapters drift.

### Option 2 — Search protocol + separate provider-agnostic batched enrichment (chosen)
Adapters originate candidates; one `Enricher` stamps metadata over the merged, deduped set.
- Pros: enrichment written once; batches (one call per ≤50 DOIs); uniform reputability regardless of finder; each
  adapter single-responsibility and trivially fakeable; clean composition behind `DiscoveryService`.
- Cons: a second pass + a DOI-join step; DOI-less web hits can't be citation-enriched (they still get a
  domain-based tier).

## Consequences

- Positive: minimal OpenAlex credit spend (batched); a frozen, versioned P3 contract; ungameable reputability;
  every stage fakeable → zero-network contract tests; adapters compose cleanly for future providers.
- Negative: DOI-less candidates surface with `None` scholarly fields (honest but thin); the frozen schema is a
  commitment across pipelines.
- Neutral: `schema_version` makes additive evolution free; breaking changes are versioned, never silent.

## Disconfirmation

- **Falsifier:** If most discovered candidates lacked DOIs (so batched enrichment rarely fired) *and* per-adapter
  fusion measurably simplified the code, the separate-pass rationale would weaken.
- **Steelmanned runner-up:** Fused per-adapter enrichment keeps a provider fully self-describing — a caller using one
  adapter in isolation gets reputability without wiring a service. For a single-provider MVP that is less machinery.
- **Reversal trigger:** If P2 permanently settles on exactly one provider and the `DiscoveryService` facade proves to
  be ceremony, collapse enrichment into that adapter and drop the separate `Enricher` seam.
