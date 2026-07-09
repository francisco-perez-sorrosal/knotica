---
schema_version: 1
type: schema
title: "SCHEMA — agentic-systems overlay"
description: "This overlay extends the root constitution (root `SCHEMA.md`) for the `agentic-systems`"
timestamp: "2026-07-08T19:54:39Z"
---

# SCHEMA — agentic-systems overlay

This overlay extends the root constitution (root `SCHEMA.md`) for the `agentic-systems`
topic: papers on agentic AI systems, and the methods, systems, benchmarks, concepts, and
people/labs around them. It never contradicts the root — contradictions are lint violations.

New topics normally start with an **empty** overlay (divergence is earned); this seed topic
ships its conventions because they were designed with the vault itself.

## Entity types

The `type` frontmatter field on pages of this topic must be one of:

| type | Page kind |
|---|---|
| `paper` | One published paper (the ingest anchor; named by a readable slug, cited by its citation key). |
| `method` | A technique or algorithm a paper introduces or uses. |
| `system` | A concrete built system or agent implementation. |
| `benchmark` | An evaluation suite or dataset. |
| `concept` | A recurring idea that spans papers. |
| `person-or-lab` | A researcher or group worth tracking. |

## Page template

Every content page body follows this section order:

1. **Summary** — a few sentences: what it is and why it matters.
2. **Key claims** — bulleted claims, **each with a citation** naming the supporting source
   citation key (optionally a section), e.g. `(wang2024awm §3.1)`, or a supporting page.
3. **Relations** — wikilinks to related entity pages, one line each stating the relationship.
4. **Open questions** — what is unresolved, untested, or worth a future ingest.

## Ingest rule

- Store the raw source **first**, under `sources/agentic-systems/<citation_key>.md`
  (citation key = first-author surname + year + short tag, e.g. `wang2024awm`).
- Each paper ingest touches: the paper's page, every affected entity page (create or update),
  the global index, and the operation log — one commit per operation, per the root constitution.
- Set `confidence` honestly: `high` for claims read directly from the source, `medium` for
  single-source synthesis, `low` for speculation or unverified claims.
