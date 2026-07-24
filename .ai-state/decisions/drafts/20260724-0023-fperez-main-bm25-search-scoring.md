---
id: dec-draft-295bb5bc
title: Replace raw-count search scoring with in-backend BM25
status: proposed
category: behavioral
date: 2026-07-24
summary: Search/eval-retrieval ranking moves from summed term-occurrence counts to live-computed Okapi BM25 (k1=1.2, b=0.75, non-negative idf, byte-length normalization); score becomes a float and engines reduce to candidate selection over one shared scoring pass.
tags: [search, retrieval, bm25, eval, ranking, mcp]
made_by: agent
agent_type: orchestrator
branch: main
pipeline_tier: lightweight
affected_files:
  - src/knotica/search/ripgrep.py
  - src/knotica/search/__init__.py
  - tests/test_search.py
---

## Context

The `search` tool and the eval retriever share one scorer (`RipgrepBackend`),
which summed case-insensitive substring occurrences of whitespace-split OR'd
query terms. With no length normalization, no idf, and no tf saturation,
ranking degenerated to document size: stopword-bearing queries ranked the
corpus exactly by file bytes, the three largest stored sources appeared in
nearly every retrieval (hub behavior), and the eval retriever contained the
golden target page for only 16 of 25 golden questions — including both
generation-5 citation failures, which were ranking failures over content that
exists nearly verbatim in the target pages. A prior symptom-level mitigation
(stripping reference lists from the largest sources, vault commits `de93821` /
`667f5e5`) moved individual scores proportionally to removed bytes but fixed
no containment outcome, and violates the ingest protocol's store-full-text
invariant.

## Decision

Score results with Okapi BM25 (k1=1.2, b=0.75, Lucene's non-negative idf
variant), computed live per query inside `RipgrepBackend` — no index, no
cache, preserving the stateless-server and determinism invariants. Document
length and the corpus average use file byte size (stat-only). Ripgrep is
demoted to candidate selection (`--files-with-matches`); term counting,
snippet extraction, and scoring happen in one shared Python pass, so the
ripgrep and fallback engines are envelope-identical by construction.
`SearchResult.score` becomes a float rounded to 4 decimals. Match semantics
(substring, case-insensitive, OR), the pagination envelope, the stopword
filter, and the page/source balanced merge are unchanged.

## Considered Options

- **In-backend BM25 (chosen):** smallest change that fixes the root cause;
  keeps the no-state invariant; idf makes an explicit stopword list
  unnecessary at the scoring layer. Cost: hand-rolled formula to maintain.
- **SQLite FTS5 `:memory:` per call:** stdlib, native `bm25()`; but rebuilds
  an index per query, introduces tokenizer semantics that diverge from the
  documented substring matching, and replaces rather than reuses the existing
  dual-engine contract.
- **BM25 library (`rank_bm25`/`bm25s`/`tantivy`):** new dependency for ~30
  lines of arithmetic on a ~100-document corpus.
- **Hybrid dense retrieval:** targets vocabulary mismatch, which is not the
  observed failure mode; over-engineering at this scale (PRE_PLAN defers it).
- **Do nothing / keep stripping bibliographies:** the strip is
  symptom-masking, breaks source fidelity, and measurably fixed no golden
  containment outcome.

## Consequences

- Eval-path golden containment: target pages 16/25 → 25/25, expected
  citations 14/25 → 19/25; both gen-5 failing goldens now retrieve their
  target page at rank 1. A/B on a vault clone shows restoring the stripped
  reference lists changes neither figure, so the strip commits can be
  reverted to restore full-fidelity sources.
- `score` in the search envelope changes type (int → float) and scale;
  no external MCP consumers exist (per dec-050 rationale).
- Scores are scope-relative (corpus statistics differ between all-topics and
  topic-scoped searches); ordering, not absolute score, is the contract.
- Per-query cost adds one stat-walk of the scope plus reads of matched files
  only; acceptable at MVP scale and unchanged in complexity class for the
  fallback engine.
