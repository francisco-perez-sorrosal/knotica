---
id: dec-011
title: Deterministic source-citation integrity as a lint/doctor check
status: accepted
category: behavioral
date: 2026-07-04
summary: A page may cite only sources the vault actually stores; lint flags any citation (declared or inline) that resolves to no stored source, so claims cannot silently outrun their evidence.
tags: [lint, doctor, ingest, verifiability, quality]
made_by: agent
agent_type: orchestrator
branch: pipeline-wiki-mvp-core
pipeline_tier: lightweight
affected_files:
  - src/knotica/core/lint.py
  - src/knotica/cli/doctor.py
  - tests/test_lint.py
  - tests/test_cli_doctor.py
dissent: The inline citation-key regex can, in principle, flag a key-shaped token that was never meant as a knotica citation; mitigated by WARN-level severity and a distinctive surname+year+tag shape.
---

## Context

A long-paper ingest stored only a skeleton of a survey (abstract + TOC + intro + conclusion) yet the
distilled pages cited §3/§4/§5/§6/§7 — sections whose text the vault never held. Every such citation was
unverifiable, and nothing caught it: a citation is prose/frontmatter, not a wikilink, so the existing
mechanical lint (link/frontmatter/index/log) was blind to it. Trustworthy, combinable context is the whole
point of the wiki, and it rests on claims being traceable to stored evidence.

## Decision

Add a deterministic lint check, `LintCheck.CITATION_UNRESOLVED`: for every content page, the set of cited
citation keys — the declared `sources:` frontmatter plus citation-key-shaped tokens in the body
(`[a-z][a-z]+\d{4}[a-z0-9-]*`, e.g. `hu2025memory-s3-forms`) — must each resolve to a stored source at
`sources/<topic>/<key>.md`. Unresolved citations are reported as `Violation`s and surfaced under a new
`doctor` **citations** row (WARN, with remediation). Paired with the section-chunked ingest prompt (which
tells the client to cite section-chunk keys), this makes "pages outran the evidence" mechanically visible:
a page citing `hu2025memory-s3-forms` fails the check unless that chunk was actually stored.

## Considered Options

- **A. Do nothing / rely on the client.** Rejected — the failure is silent and recurred.
- **B. Semantic check only** (the LLM `lint` operation reviews claims-vs-source). Useful but non-deterministic
  and not a hook/CI gate; complements, does not replace, a mechanical check.
- **C. Frontmatter-only check.** Simpler, but misses inline `(key §N)` citations not mirrored in frontmatter.
- **D. Frontmatter + inline, key-resolves-to-stored-source (chosen).** Deterministic, catches both surfaces,
  fits lint's remit, WARN-level so a key-shaped false positive never blocks.

## Consequences

- Positive: unverifiable citations are caught at `lint_check`/`doctor` time (and can gate the SessionStart
  hook); the eval metric's `citation_validity` / `lint_violation_penalty` now has a concrete mechanical
  signal, so a future DSPy loop is rewarded for ingests that keep pages within their stored evidence.
- Negative: the inline regex is heuristic — a non-citation token of shape `word+YYYY+tag` could be flagged
  (WARN only). Topic-scoped resolution assumes a page cites sources under its own topic (current convention).
- Neutral: adds one `doctor` row (additive; JSON schema unchanged at v1).

## Disconfirmation

- **Falsifier:** if real vaults accumulate frequent false-positive citation WARNs, the inline heuristic is too
  broad and should narrow to parenthesized-citation contexts or frontmatter-only.
- **Steelmanned runner-up (C, frontmatter-only):** if clients reliably mirror every inline citation in
  `sources:`, the inline scan is redundant surface area.
- **Reversal trigger:** a first-class sectioned-source model with a per-source manifest would let the check
  verify *section coverage* directly, superseding the key-resolves heuristic.
