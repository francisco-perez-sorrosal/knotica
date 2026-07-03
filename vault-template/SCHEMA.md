---
schema_version: 1
---

# SCHEMA — Root Constitution

This file is the vault's constitution: the invariants every topic inherits. Topic overlays
(`<topic>/SCHEMA.md`) **extend but never contradict** it — contradictions are lint violations.
Evolution is governed exclusively by `knotica migrate`; record formats evolve **additive-only**
(new optional fields, never breaking renames), and a breaking change requires a `schema_version`
bump plus a migration step.

## Wikilinks

- Link pages with wikilink syntax: `[[page]]`, or `[[page|display text]]` for custom text.
  Omit the `.md` extension.
- Every wikilink must resolve to a page inside this vault. Unresolved wikilinks are lint
  violations.
- Never link into a dot-folder (`.knotica/`, `.obsidian/`): Obsidian hard-ignores dot-paths,
  so such links render broken and hide content from the reader.

## Cross-topic linking

- Within a topic, link by page name: `[[react]]`.
- Across topics — and from root pages (`index.md`, `START_HERE.md`) — always use the full
  vault path: `[[agentic-systems/react]]`. The explicit path keeps resolution unambiguous
  and mechanically checkable.

## Core frontmatter

Every content page (topic pages; not the reserved structural pages listed below) carries YAML
frontmatter with these fields:

| Field | Value | Meaning |
|---|---|---|
| `type` | string | Page kind. Topic overlays define the allowed entity types. |
| `topic` | string | The topic directory this page belongs to. |
| `created` | `YYYY-MM-DD` | Date the page was created. |
| `updated` | `YYYY-MM-DD` | Date of the last content update. |
| `confidence` | `low` \| `medium` \| `high` | Confidence in the page's claims. |
| `sources` | list of strings | Citation keys of supporting sources under `sources/<topic>/`. |
| `status` | `active` \| `stale` | `stale` marks pages needing review. |
| `supersedes` | page reference (optional) | Page this one replaces. |
| `superseded_by` | page reference (optional) | Page that replaces this one. |
| `tags` | list of strings | Free-form topical tags. |

## Reserved names

The following top-level names may **not** be used as topic names (lint- and tool-enforced):

`sources`, `index.md`, `log.md`, `SCHEMA.md`, `START_HERE.md`, `.knotica`, `.git`

Topic names are kebab-case or lowercase (e.g. `agentic-systems`).

## Per-operation commit discipline

- Every mutating operation (`write_page`, `store_source`, `create_topic`, `curate_example`,
  `migrate`) produces **exactly one git commit** and appends **exactly one** entry to `log.md`,
  as a single atomic unit.
- The commit message follows the frozen format below, so the operation→commit index is
  recoverable from `git log`.
- A failed mid-operation write is rolled back to the pre-operation commit — the vault is never
  left half-committed.
- Manual edits (Obsidian, plain file tools) are welcome but should follow the same spirit:
  commit per logical change, append a matching `log.md` entry.

## Secret scrubbing

- All content written by a knotica operation is scanned for secret patterns (API keys, tokens,
  private keys) **before** it is committed, so secrets never enter git history.
- Matches are redacted, and the operation reports a `SECRET_SCRUBBED` warning listing the
  redacted spans — the write still succeeds. Review the spans before relying on the page.
- Patterns are deliberately conservative; content that legitimately looks token-like (e.g.
  hashes quoted from a paper) may occasionally be redacted — the span report makes this visible.
- Manual edits are not scrubbed: do not paste secrets into the vault.

## Machine-record schemas (frozen)

The five record formats below are **frozen**. The JSONL and frontmatter records each carry
their own `schema_version` field (currently `1`); the two line formats (log entry, commit
message) are versioned by this constitution's `schema_version`. All evolve additive-only under
`knotica migrate`.

### 1. `qa.jsonl` — curated examples (record `schema_version: 1`)

Per-topic flywheel dataset at `.knotica/datasets/qa.jsonl` (topic-relative), one JSON object
per line, appended by `curate_example`:

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Unique record id. |
| `schema_version` | integer | Record schema version (`1`). |
| `topic` | string | Topic the example belongs to. |
| `created` | string (ISO 8601) | When the example was curated. |
| `query` | string | The user's query. |
| `pages_used` | array of strings | Vault paths of the pages used to answer. |
| `answer` | string | The answer given. |
| `citations` | array of strings | Pages/sources cited in the answer. |
| `verdict` | `good` \| `bad` \| `corrected` | User verdict on the answer. |
| `corrected_answer` | string or `null` | The corrected answer when `verdict` is `corrected`. |
| `source` | `curate_example` \| `distillation` | How the record was captured. |
| `model` | string | Model that produced the answer. |

Consumption: the Phase-3a DSPy trainset is the records with `verdict` in `{good, corrected}`
(gold answer = `corrected_answer` when present, else `answer`); `bad` records are retained for
analysis.

### 2. `metrics.jsonl` — per-generation eval history (record `schema_version: 1`)

Per-topic eval history at `.knotica/metrics.jsonl` (topic-relative), one JSON object per line.
**No file ships in the template** — its producer is the Phase-2 eval harness; absence means
"not yet evaluated".

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | integer | Record schema version (`1`). |
| `topic` | string | Topic evaluated. |
| `timestamp` | string (ISO 8601) | When the evaluation ran. |
| `generation` | integer | Improvement-loop generation; `0` = baseline. |
| `harness_version` | string | Version of the eval harness. |
| `scalar` | number | The single eval scalar (formula below). |
| `components` | object | `{qa_accuracy, citation_validity, lint_violations, token_cost}`. |
| `n_examples` | integer | Number of examples evaluated. |
| `corpus_ref` | string | Frozen corpus reference, `git:<sha>`. |
| `artifact_ref` | string or `null` | Compiled-artifact reference, when one was evaluated. |

Scalar formula (locked): `scalar = qa_accuracy + citation_validity − lint_violation_penalty −
token_cost_penalty`.

### 3. Log entry

Appended to `log.md` (append-only, one entry per mutating operation, newest last):

```
## [YYYY-MM-DD] <op> | <topic> | <title>
- <touched page path>   (optional bullets, one per touched page)
```

The H2 line is exact — greppable and Obsidian-renderable.

### 4. Commit message

```
knotica(<op>): <topic> — <title>
```

One commit per operation; `<op>` is the operation name. The separator is an em-dash (`—`)
with surrounding spaces.

### 5. Source provenance frontmatter (record `schema_version: 1`)

Sources are stored **immutably** under `sources/<topic>/<citation_key>.md` with this
frontmatter:

| Field | Value | Meaning |
|---|---|---|
| `schema_version` | integer | Record schema version (`1`). |
| `type` | `source` | Marks the file as a stored source. |
| `topic` | string | Topic the source belongs to. |
| `citation_key` | string | Citation key; also the filename (e.g. `wang2024awm`). |
| `retrieved` | string (ISO 8601) | When the source was retrieved. |
| `origin_url` | string | Where the source came from. |
| `sha256` | string | Hex digest of the stored content. |
| `source_type` | `html` \| `pdf` \| `markdown` \| `text` | Original format. |
| `ingested_by` | string | Model/agent identifier that performed the ingest. |

A source, once stored, is never rewritten: re-storing identical content is a no-op; storing
different content under the same citation key is an error — pick a new key.
