# Ingest — operation protocol

You are performing a knotica **ingest**: persist a source into this wiki and distill it
into schema-conformant entity pages. The tools are deterministic — you do all the
cognitive work (fetching, converting, reading, writing prose). Follow the steps in order.

**Arguments**: `source` — URL or citation of the material to ingest; `topic` — optional
explicit topic (may be empty).

## 0. Make the ingest visible (dashboard)

The knotica dashboard **Ingest** pane streams progress from an activity journal.
Keep the user informed — and **emit stages in protocol order** (never report
`plan` after you have already called `store_source`):

1. At the start, call `ingest_progress` with `stage=resolve_topic`,
   `status=started`, a short `title`, and an empty `run_id` — **save the returned
   `run_id`** and pass it on every later `ingest_progress` call for this ingest.
2. Emit cognitive stages **before** the work they describe, in this exact order:
   `read_schema` → `fetch` → `parse` → **`plan`** → then begin `store_source`.
3. **Plan before store.** Call `ingest_progress(stage=plan, …)` with the page
   list *before* the first `store_source`. Do not postpone planning until after
   sources are stored.
4. Mutating tools (`store_source`, `write_page`, `create_topic`) auto-log
   server-side — you do not need to duplicate those, but you **may** emit a
   `started` event just before a long `store_source`/`write_page`.
5. When the ingest itself is finished, call `ingest_progress` with
   `stage=complete`, `status=ok` **before** offering curation. On hard failure,
   call it with `stage=error`, `status=error`. Curation is a **separate**
   dashboard workflow — `curate_example` auto-logs its own short run; do not
   emit ingest `curate` stages.

Titles should be human-readable (e.g. "Fetching ar5iv HTML", "Planning 6 pages").

## 1. Resolve the topic

> **Topic-inference policy.** Call `list_topics`. If the caller passed an explicit
> `topic`, use it (override always wins). Otherwise infer: if the material clearly
> matches one existing topic, auto-place there; if it is ambiguous across topics or
> warrants a new topic, ask the user, and on confirmation call `create_topic`. Always
> pass the resolved topic explicitly to every tool — the server holds no active-topic
> state.

## 2. Read the effective schema

Read the resource `knotica://schema/resolved/{topic}` (substitute the resolved topic)
using your client's resource-read mechanism — an `@`-mention of the URI or the client's
built-in resource reader. Resources are not auto-loaded; you must fetch this yourself.
It is the merged root constitution ⊕ topic overlay: allowed entity types, the page
template, required frontmatter fields, and the topic's ingest rule. Everything you write
below must conform to it.

## 3. Fetch and convert the source — completely

Retrieve the **full text**, not a summary, abstract, or excerpt. For arXiv, fetch the HTML
full text (`ar5iv.org/abs/<id>` or the paper's `/html/` view) or the PDF — never the `/abs/`
landing page, which is only the abstract. Convert to clean markdown yourself (no tool fetches
for you) and transcribe faithfully: preserve every section, table, and key result; do **not**
paraphrase, compress, or summarize — the stored source is the evidence of record that pages
cite. Strip only navigation and boilerplate (nav bars, cookie banners, footers), never
substantive content. Note the origin URL and the original format (`html`, `pdf`, `markdown`,
or `text`).

**Before storing, verify completeness.** Does the markdown carry all of the document's major
sections end to end, not just the opening? Does its length look like a full document rather
than an abstract? If it looks truncated or summarized, re-fetch before continuing. If the full
text genuinely cannot be retrieved (paywall, fetch failure), tell the user and store what you
have **with an explicit note that it is partial** — never store a partial source as if complete.

## 4. Store the source immutably

Call `store_source` with the resolved `topic`, a `citation_key` (lowercase, no spaces —
e.g. `wang2024awm`), a human-readable `title` (it becomes the commit message and log
title), the converted markdown as `content`, the origin as `source_url`, and
`source_type`. Sources are immutable: `SOURCE_EXISTS` means the key already holds
different content — pick a new citation key rather than overwriting. If your conversion
required judgment calls (repairing renderer artifacts, dropping figures), report them to
the user before storing — immutability makes them permanent; a defective stored source
is corrected only by re-storing under a suffixed key (`<key>-v2`).

**Store enough that every claim stays verifiable — for a long paper, store it section by
section.** Your pages may cite only what the vault actually holds, so store the full text of
every section your pages will draw claims from — not just the abstract, the table of contents,
and the conclusion. If the source is short, store it whole. If it is long and clearly sectioned
(a survey, a book, a long paper) and will not fit one `store_source` call, store it in
**sections**:

- a **spine** under the base `citation_key` (e.g. `hu2025memory`) — the bibliographic header,
  the abstract, and a **section map** that lists each section as a wikilink to its chunk
  (`- §N Title — [[<key>]]`, a clickable table of contents; the spine and its chunks share one
  directory, so a bare `[[<key>]]` resolves);
- **each major section you will cite** as its own chunk under a section-suffixed key
  (e.g. `hu2025memory-s3-forms`), holding that section's faithful **verbatim** text, never a
  summary, **as clean Markdown** — render the section heading as a Markdown header
  (`## <n> <Title>`) and repair remaining extraction artifacts (runs of spaces, subsection
  titles run into body text). For `source_type: pdf`, `store_source` also applies a
  deterministic reflow that joins hard-wrapped column lines before persistence. One `store_source` per section — each chunk is small, immutable, and a durable
  checkpoint, so a long source is **resumable**: re-run and store only the sections still
  missing (identical content is a safe no-op).

The invariant that ties storage to the pages: **never write a page that cites a `§N` whose text
is not stored in a chunk.** If a section is genuinely unavailable, say so and make no claims
about it.

## 5. Plan the pages, then write them in dependency order

Distill the source into schema-conformant pages. The ordering below is what keeps a **long
or interrupted ingest** safe — a large paper may not fit in one turn, and that must be fine.

**Plan first.** Before writing anything, list the pages this ingest will create: a main page
for the source plus one page per method, concept, system, or benchmark the schema's ingest
rule warrants. Keep the set focused — create a page for each entity that other pages will
link to or that stands on its own; fold minor points into prose rather than spawning thin
pages. For a dense survey, a handful of well-scoped pages beats many shallow ones.

**Write leaf pages before the pages that link to them — the source's main page last.** A page
may only link to pages that already exist, so write the concept/method pages first and the
main source page (which links out to all of them) last. This keeps the vault lint-clean at
*every* step: if you stop early you have real pages with no dangling wikilinks, never a
committed page pointing at a missing target.

**Write one page per step; if you must stop, resume — never restart.** Each `write_page` is
one atomic commit (secret-scrub, write, one git commit, a `log.md` append — **never write
`log.md` yourself**), so a long ingest is checkpointed page by page. Because `write_page` is
idempotent (re-sending an identical page is a safe no-op, `changed: false`), you resume simply
by re-running the ingest and writing only the pages not yet present — skip the ones already
committed; never duplicate a page or start over.

Each `write_page` call takes the resolved `topic`, the `page` name, the full markdown
`content` **including YAML frontmatter** conforming to the resolved schema. Required OKF
fields: `type` (e.g. `concept`, `paper`, `source`), `title`, and `timestamp` (RFC 3339 UTC,
e.g. `2026-07-08T15:30:00Z`). Knotica extensions: `topic`, `created`, `updated`,
`confidence`, `sources`, `status`, `tags`. Cite the citation key from step 4 in
`sources`. Use **wikilinks** for internal links (`[[page]]`, `[[topic/page|alias]]`) — preferred native authoring syntax.
distillations** (Summary, cited Key claims, Relations, Open questions) — the source's full
text lives in the stored source; do not copy it into pages. Write the body as clean Markdown:
**one line per paragraph or list item — do not hard-wrap prose mid-line** (the editor
soft-wraps; hard line breaks render as broken text).

**Cite only what the vault holds, and make citations clickable.** In each page's `sources:`
frontmatter list the specific source keys the page used — for a section-chunked paper, the
section-chunk keys, plus the spine key for whole-paper claims. Write each inline citation as a
**wikilink to the cited chunk**, using its full vault path with a readable alias:
`[[sources/<topic>/<key>|<key> §N]]` (e.g.
`[[sources/agentic-systems/hu2025memory-s3-form|hu2025memory §3]]`) — so the reader can click
straight to the evidence. Every citation must resolve to a stored chunk; a page must never cite
a section the vault does not contain.

## 6. The index maintains itself — through your `index_entry`

Root `index.md` is never a `write_page` target (reserved name — the call would fail with
`RESERVED_NAME`). Instead, the `index_entry` you pass in step 5 is upserted as the
page's catalog line (full-path wikilink + your one-line description) **in the same
commit** that writes the page. You cannot write a page and forget the index; never read
or rewrite `index.md` yourself.

## Ingesting an approved suggestion

When the user asks to ingest an approved gap-fill suggestion, or you discover one via
`suggestions_read(status="approved")`, the ingest follows the same protocol above but
on an isolated candidate context — the loop will gate the result before it touches
the wiki.

**1. Open the candidate.** Call `source_ingest_open` with the resolved `topic` and
the suggestion's `suggestion_id`. Save the returned `candidate` handle (an opaque string)
and the `provenance` block. If the response carries `state: "resumed"`, the ingest is
resuming an earlier partial session — read `resume.pages_present` and write only the
pages not yet written; never restart. Idempotent: opening twice returns the same handle
and current state.

**2. Fetch and store the source.** Follow steps 3–4 of the main protocol exactly as
normal, except: pass the `candidate` handle to every `store_source` call (it scopes the
write to the suggestion's candidate context instead of the default branch). Use
`provenance.source_url` as `source_url` and `provenance.citation_hint` as the
`citation_key` — these are pre-derived from the gap's origin.

**3. Distil pages — with provenance.** Follow step 5 of the main protocol, writing each
`write_page` with the same `candidate` handle. In every page's frontmatter, add a
`provenance` block:

```yaml
provenance:
  suggestion_id: <from provenance.suggestion_id>
  gap_id: <from provenance.gap_id>
  qa_id: <from provenance.qa_id>
```

(The `query_text` is not copied per-page — it lives on the suggestion record.) The
`index_entry` upserts via `write_page` exactly as step 6 describes; the index updates
on the candidate context.

**4. Stage held-out golden candidates (contamination guard).** Before or interleaved
with page-writing, call `golden_review_save` to stage client-authored held-out
candidates derived from the source text — examples the wiki should answer after the
ingest. Pass them as `accepted_json` — a JSON array of candidate objects
(`question`, `reference_answer`, `citations`, `pages_used`; optional `support`).
These stage disjoint from `qa.jsonl` and will reach the frozen set only
through the `golden_review_load` → accept → `golden.freeze` flow below (never a direct
freeze call). The contamination guard is the protocol ordering: stage the held-out
candidates *before* the gate evaluates the ingest.

**5. Review and freeze held-out golden candidates.** Any human freeze of the staged
candidates must route through `golden_review_load` (reads the current frozen set),
followed by acceptance and `golden.freeze` (atomically replaces the frozen set). This
read-merge-freeze pattern is load-bearing — never call freeze without reading first,
or prior golden entries will be lost.

**6. Submit to the gate.** Call `source_ingest_submit` with the `topic` and
`suggestion_id`. Mode defaults to `"dry-run"`, which checks lint-clean, source present,
≥1 page written, and gate eligibility (topic has a frozen baseline) without making
changes. Then call it again with `mode: "apply"` to finalize and hand the candidate to
the loop's gate — it evaluates the wiki WITH the new source and either merges it
(closes the gap without regressing others) or refuses it (quarantined, reworkable).

**7. Report the verdict.** The `source_ingest_submit` apply response carries the
verdict:

- **merged** → the source closed the gap without regression; the suggestion is now
  `ingested` automatically. Report success to the user.
- **refused** → the source made other answers worse. Show the user the `diff_summary`
  and top regressed questions. The suggestion stays `approved` and is resumable — you
  or the user can improve the distillation and re-run steps 1–6 (open/fetch/distil/
  review/submit) to try again.

**Never write `suggestions.jsonl`, `log.md`, or `index.md` yourself; never pass
`candidate` on a normal (non-suggestion) ingest.**

## If a tool returns an error

Errors arrive inside the tool result as `{"error": {"code", "message", "fix",
"retryable"}}` — read `fix` and do what it says. Two codes need special handling:

- `LOCK_BUSY` (retryable): another operation holds the vault lock. Retry the same call
  once after a brief pause; if it is still busy, report it to the user and stop.
- `NOT_CONFIGURED`: no configured vault. Surface the error's `fix` text to the user
  verbatim and stop — no other step can proceed.

A successful write may carry a `warnings` list. `SECRET_SCRUBBED` means secret-like
spans were redacted before commit — show the reported spans to the user before relying
on the page.

## 7. Finish — complete ingest, then offer to curate

Report what was ingested: the stored source, each page written (path + commit sha), and
any scrub warnings. Call `ingest_progress(stage=complete)` so the Ingest rail closes.
Then **always end by offering, in one keystroke, to save this ingest as a curated
example** — the wiki's operations improve only from curated examples. If the user
accepts, call `curate_example` with the resolved `topic`, the ingest request as
`query`, the pages you wrote as `pages_used`, your ingest summary as `answer`, and the
user's `verdict` (`good` or `bad`). That opens a separate Curate workflow on the
dashboard; do not keep the ingest run open for it.
