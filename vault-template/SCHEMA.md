---
schema_version: 1
type: schema
title: "SCHEMA — Root Constitution"
description: "This file is the vault's constitution: the invariants every topic inherits. Topic overlays"
timestamp: "2026-07-08T19:54:39Z"
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

- Within a topic, link by page name: `[[react]]`. A bare page name resolves **only within
  the same directory** as the linking page — never across directories or into the vault
  root (amended 2026-07-03 after Phase-0 validation; this is the rule the tooling
  implements).
- Across topics — and from root pages (`index.md`, `START_HERE.md`) — always use the full
  vault path: `[[agentic-systems/react]]`. The explicit path keeps resolution unambiguous
  and mechanically checkable.
- `SCHEMA.md` files (root or topic overlay) must always be linked by full vault path
  (`[[SCHEMA]]` is ambiguous between the root constitution and a topic overlay — never use
  the bare form).

## Core frontmatter

Every content page (topic pages; not the reserved structural pages listed below) carries YAML
frontmatter with these fields. Knotica is a **native OKF-compatible superset**: the `type`
field satisfies Open Knowledge Format requirements (any non-empty string is valid OKF).

| Field | Value | Meaning |
|---|---|---|
| `type` | string | **OKF-required** page kind (`concept`, `paper`, `method`, `source`, `schema`, …). Open taxonomy — topic overlays may constrain allowed values. |
| `title` | string | **OKF-recommended** human title. |
| `description` | string | **OKF-recommended** one-line summary. |
| `resource` | string | **OKF-recommended** canonical URL for references (maps from `origin_url` on sources). |
| `timestamp` | RFC 3339 | **OKF-recommended** last-significant-update instant, UTC (`2026-07-08T15:30:00Z`). |
| `tags` | list of strings | **OKF-recommended** topical tags. |
| `topic` | string | The topic directory this page belongs to. |
| `created` | RFC 3339 | When the page was created (UTC). |
| `updated` | RFC 3339 | Date of the last content update (UTC). |
| `confidence` | `low` \| `medium` \| `high` | Confidence in the page's claims. |
| `sources` | list of strings | Citation keys of supporting sources under `sources/<topic>/`. |
| `status` | `active` \| `stale` | `stale` marks pages needing review. |
| `supersedes` | page reference (optional) | Page this one replaces. |
| `superseded_by` | page reference (optional) | Page that replaces this one. |

## Reserved names

The following top-level names may **not** be used as topic names (lint- and tool-enforced):

`sources`, `notes`, `index.md`, `log.md`, `SCHEMA.md`, `START_HERE.md`, `.knotica`, `.git`

Topic names are kebab-case or lowercase (e.g. `agentic-systems`).

### Personal notes — unscored, excluded from all quality measures

The `notes/` directory is a reserved, unscored folder family. It exists to hold human-authored
personal reflection and marginalia on your vault's content — independent of the page and source
graph that quality metrics and the autoimprovement loop operate over.

The layout is `notes/<topic>/<file>.md`, mirroring `sources/<topic>/<key>.md`. Notes within a
topic's notes folder are **never scored**. They are excluded by construction from:

- Search results — a search never returns a note, only pages and sources
- Lint's content-page count and orphan checks
- The loop's change detection and improvement observation

Hand-author notes freely in Obsidian or any text editor — the vault scaffold and all tooling treat
notes as a scoped, private workspace that coexists with but never contaminates your KB's quality
guarantees.

### Personal notes — on-disk format

A note is a Markdown file with YAML frontmatter and an optional `## Anchors` section that pins
quoted moments to specific vault locations. Create notes by typing them directly in Obsidian or
your editor — no special tools required. The file format is the only contract; a note authored
by hand and one written by the capture tool are indistinguishable on disk.

#### Filename and identity

The filename is `<YYYYMMDD-HHMMSS>-<slug>.md`, where:

- `<YYYYMMDD-HHMMSS>` is the timestamp when the note was created (e.g., `20260729-142211`)
- `<slug>` is the note's heading or opening words, lowercased, hyphenated, truncated to 40 characters (e.g., `reward-hacking-is-goodhart`)

Store notes in `notes/<topic>/` — the topic organizes your personal reflections by subject, mirroring the `sources/<topic>/` structure. For example, a reflection on agentic systems lives at `notes/agentic-systems/20260729-142211-reward-hacking-is-goodhart.md`.

The frontmatter `id` field (see below) equals the filename stem exactly — just the timestamp and slug, no directory or `.md` extension. Files are never renamed after creation; the id is stable.

#### Frontmatter

Every note carries YAML frontmatter. The minimum required fields are `type`, `id`, `topic`, and `created`. All others are optional and carry defaults:

| Field | Required? | Type | Default | Meaning |
|-------|-----------|------|---------|---------|
| `type` | Yes | string | — | Always `note`. |
| `id` | Yes | string | — | Filename stem: `<YYYYMMDD-HHMMSS>-<slug>`. |
| `topic` | Yes | string | — | Topic folder the note belongs to (e.g., `agentic-systems`). |
| `created` | Yes | string | — | RFC 3339 timestamp, UTC (e.g., `2026-07-29T14:22:11Z`). |
| `schema_version` | No | integer | `1` | Schema version. Omit unless you have a reason to override. |
| `intent` | No | string | `reflection` | One of `reflection`, `dispute`, `gap`, `question` — nothing else. `reflection` is private and stays in the notes layer forever; the other three mark the note as *promotable*, meaning you may later choose to carry it into the wiki. Marking is not promoting: crossing into the wiki is always a separate, deliberate act. |
| `updated` | No | string | `created` value | RFC 3339 timestamp of the last edit, UTC. Defaults to `created` if omitted. |
| `status` | No | string | `active` | Either `active` or `archived`. Archiving retires a note without deleting it; nothing ever removes a note file. |
| `tags` | No | list of strings | `[]` | Keywords for searching and organizing. |

Example frontmatter:

```yaml
---
type: note
id: 20260729-142211-reward-hacking-is-goodhart
topic: agentic-systems
created: 2026-07-29T14:22:11Z
intent: reflection
updated: 2026-07-29T14:22:11Z
status: active
tags: [metrics, incentives]
---
```

#### Body and anchors section

Write the note's body freely — your thoughts, reflections, questions, anything. Markdown is supported (headings, emphasis, lists, etc.). The body is plain prose with no constraints.

After the body (or if the note has no body), you can add an optional `## Anchors` section. This section pins quoted passages from other vault pages to your reflection. Start with a level-2 heading:

```markdown
## Anchors
```

Under this heading, list one anchor per bullet point. The format is strict but tolerant:

```markdown
- [[<vault-path>[#<Heading>]]] — `<fidelity>` · pinned@`<sha>`
  > <quote>
```

**Anchor line reference:**

- `[[vault-path]]` — **optional.** A wikilink to the page you're pinning, written without the `.md` suffix, as Obsidian expects.
  - The optional `#<Heading>` after the path names a specific section. Omit it to pin the whole page.
  - Omit the wikilink entirely when the note belongs to the topic but not to any one page. Write the bullet as `` - `topic` · pinned@`<sha>` `` and keep the quote underneath — that way the passage you were reacting to is still recorded even though nothing points at a page.
- `<fidelity>` — how specific the pin is: `span` (a single sentence or phrase), `page` (the whole page), or `topic` (the topic as a whole, no specific page). Two further values, `block` and `section`, are not yet produced; a file containing one is read without complaint and left untouched.
- `pinned@`<sha>`` — the git commit SHA the page was at when you read it. Backticks are required. This is what makes the passage permanently recoverable: however much the page is rewritten later, the text you actually saw can always be retrieved from that commit.
- `<quote>` — **optional.** The exact text you're pinning, on the following line, beginning with `>`. **Copy it character-for-character from the page** — it is matched verbatim to locate your note later, so an approximation still stores fine but will not be found, and the note will read as unanchored. Omit the line entirely if you're pinning a whole page rather than a passage.

A bullet needs at minimum a fidelity and a `pinned@` token — that pair is what marks it as an anchor rather than an ordinary list item.

**Full example:**

```markdown
## Anchors

- [[agentic-systems/agent-memory#Working memory]] — `span` · pinned@`a3f9c21`
  > the model learns to satisfy the metric rather than the goal
```

**Tolerance and recovery:**

- Extra whitespace around `—` or `·` is fine; the parser is forgiving about separators.
- A missing `#Heading` is OK (the quote still pins to the page).
- An unparseable bullet line (e.g., a typo in the link or fidelity) is skipped silently — the note remains valid, and readable anchors survive.
- A bare `## Anchors` heading with no bullets is valid.
- A note with no `## Anchors` section at all is valid (a topic-level reflection with no specific quotes).

**Known limitation:** a body line that consists of *exactly* the text `## Anchors` (as prose) is indistinguishable from the section marker and is dropped on round-trip. This is an edge case; write `### Anchors` or reword the prose if you need to mention the anchor format literally inside a note.

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

Native vault `log.md` files use the **OKF date-grouped shape** (newest first):

```
# Directory Update Log

## 2026-07-08

* **Update**: Added [Agent memory](agentic-systems/agent-memory.md), updated [index](index.md).
```

Date headings use `## YYYY-MM-DD` (no brackets). Each bullet is `* **Kind**: prose with optional Markdown links`.

Mutating operations still record the same facts; `knotica okf repair` can convert legacy
Knotica operation headings (`## [YYYY-MM-DD] <op> | <topic> | <title>`) into OKF shape.
The frozen commit-message grammar (§4) is unchanged.

Legacy Knotica heading (parseable, convertible):

```
## [YYYY-MM-DD] <op> | <topic> | <title>
- <touched page path>   (optional bullets, one per touched page)
```

### 3b. OKF reserved files

Only `index.md` and `log.md` are OKF-reserved:

- `index.md` — **no frontmatter**; Markdown catalog body only.
- `log.md` — date-grouped update log; not a concept document.

All other `.md` files (including `SCHEMA.md`, `START_HERE.md`, `reports/*.md`) are concept
documents and require YAML frontmatter with non-empty `type`.

### 3c. OKF interoperability commands

```bash
knotica okf check              # native OKF compatibility (wikilinks allowed)
knotica okf check --strict     # graph-integrity strictness
knotica okf export -o <path>   # pure OKF bundle (Markdown links)
knotica okf export --pure -o <path>
knotica okf repair --dry-run   # fix structural drift in the active vault
knotica okf repair --apply
```

Pure OKF export converts wikilinks to standard Markdown links; the working vault keeps
wikilinks as the preferred authoring syntax.

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
| `sha256` | string | Hex digest of the stored markdown **body** — the bytes after the provenance frontmatter block's trailing blank line, trailing newline included (the frontmatter cannot hash itself; clarified 2026-07-03). |
| `source_type` | `html` \| `pdf` \| `markdown` \| `text` | Original format. |
| `ingested_by` | string | Model/agent identifier that performed the ingest. |

A source, once stored, is never rewritten: re-storing identical content is a no-op; storing
different content under the same citation key is an error — pick a new key.

**Correcting a defective source** (e.g., a conversion bug discovered later): store the fixed
content under a new suffixed key (`<key>-v2`), update the pages' `sources` references, and
note the supersession in the new source's body. The defective source stays (immutability),
but nothing may cite it going forward. Conversion judgment calls (repairing renderer
artifacts, dropping figures) should be reported to the user *before* storing — immutability
plus the `sha256` seal makes them permanent (amended 2026-07-03 after Phase-0 validation).
