# Ingest — operation protocol

You are performing a knotica **ingest**: persist a source into this wiki and distill it
into schema-conformant entity pages. The tools are deterministic — you do all the
cognitive work (fetching, converting, reading, writing prose). Follow the steps in order.

**Arguments**: `source` — URL or citation of the material to ingest; `topic` — optional
explicit topic (may be empty).

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

## 3. Fetch and convert the source

Fetch `source` yourself and convert it to clean markdown — no tool fetches for you.
Preserve the content faithfully; strip navigation boilerplate. Note the origin URL and
the original format (`html`, `pdf`, `markdown`, or `text`).

## 4. Store the source immutably

Call `store_source` with the resolved `topic`, a `citation_key` (lowercase, no spaces —
e.g. `wang2024awm`), the converted markdown as `content`, the origin as `source_url`,
and `source_type`. Sources are immutable: `SOURCE_EXISTS` means the key already holds
different content — pick a new citation key rather than overwriting.

## 5. Write the entity pages

For each entity the schema's ingest rule calls for (typically a main page for the source
plus linked pages for the methods/concepts it introduces), call `write_page` with the
resolved `topic`, the `page` name, the full markdown `content` **including YAML
frontmatter** conforming to the resolved schema (`type`, `topic`, `created`, `updated`,
`confidence`, `sources` — cite the citation key from step 4 — `status`, `tags`), a
one-line `summary` (it becomes the commit message and log title), and a one-line
`index_entry` describing the page for the global catalog.

- Connect related pages with wikilinks; use full vault-path wikilinks
  (`[[<topic>/<page>]]`) across topics, bare `[[page]]` within one.
- Each `write_page` call is one atomic unit: secret-scrub, write, one git commit, and a
  `log.md` append. **Never write `log.md` yourself** — the tools maintain it.
- Re-sending identical content is a safe no-op (`changed: false`, no commit).

## 6. The index maintains itself — through your `index_entry`

Root `index.md` is never a `write_page` target (reserved name — the call would fail with
`RESERVED_NAME`). Instead, the `index_entry` you pass in step 5 is upserted as the
page's catalog line (full-path wikilink + your one-line description) **in the same
commit** that writes the page. You cannot write a page and forget the index; never read
or rewrite `index.md` yourself.

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

## 7. Finish — report, then offer to curate

Report what was ingested: the stored source, each page written (path + commit sha), and
any scrub warnings. Then **always end by offering, in one keystroke, to save this ingest
as a curated example** — the wiki's operations improve only from curated examples. If
the user accepts, call `curate_example` with the resolved `topic`, the ingest request as
`query`, the pages you wrote as `pages_used`, your ingest summary as `answer`, and the
user's `verdict` (`good` or `bad`).
