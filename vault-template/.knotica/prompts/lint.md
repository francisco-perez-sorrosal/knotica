# Lint — operation protocol

You are performing a knotica **lint**: check the vault (or one topic) for violations —
mechanical first, semantic second. The `lint_check` tool finds only what is mechanically
detectable; the semantic pass (contradictions, staleness, schema-spirit violations) is
your job, guided by the schemas.

**Arguments**: `topic` — optional topic to scope the lint (may be empty).

## 1. Resolve the topic

> **Topic-inference policy.** Call `list_topics`. If the caller passed an explicit
> `topic`, use it (override always wins). Otherwise infer: if the material clearly
> matches one existing topic, auto-place there; if it is ambiguous across topics or
> warrants a new topic, ask the user, and on confirmation call `create_topic`. Always
> pass the resolved topic explicitly to every tool — the server holds no active-topic
> state.

Lint-specific note: an empty `topic` means "lint the whole vault" — that is a normal
choice here, not an ambiguity to resolve. Never create a topic during a lint.

## 2. Mechanical pass

Call `lint_check` (with the resolved `topic`, or empty for the whole vault). It returns
violations as **data** — frontmatter-schema nonconformance, unresolved wikilinks,
reserved-name collisions, mechanically detectable root/overlay contradictions, and
index/log inconsistencies. An empty list means mechanically clean; a non-empty list is
still a successful call, not an error.

## 3. Read the schemas

Read two resources using your client's resource-read mechanism (an `@`-mention of the
URI or the built-in resource reader — resources are not auto-loaded):

- `knotica://schema/root` — the constitution: the invariants every topic must obey.
- `knotica://schema/resolved/{topic}` — the merged effective schema for the topic under
  review (read it per topic when linting the whole vault).

These define what "correct" means for the semantic pass.

## 4. Semantic pass

Use `search`, `read_page`, and `list_links` to examine the pages in scope and look for
what mechanical checks cannot see:

- **Contradictions** — pages making incompatible claims about the same thing.
- **Staleness** — pages whose content is outdated or superseded but not marked
  (`status: stale`, missing `supersedes`/`superseded_by` frontmatter).
- **Schema-spirit violations** — pages that pass field checks but ignore the topic's
  page template or entity-type intent.
- **Missing connections** — clearly related pages with no wikilink between them; pages
  absent from `index.md`.

## 5. Report findings

Report all findings grouped by severity (violations that break conventions first, then
quality concerns), each with the page path, what is wrong, why it matters, and a
concrete fix.

## 6. Optionally fix — only with confirmation

Do not change anything unprompted. If the user confirms specific fixes, apply them with
`write_page` (full corrected content, one-line `summary` per page). Each call commits
and appends to `log.md` automatically — never write `log.md` yourself. If a fix changes
what the catalog should list, update `index.md` with a further `write_page` call.

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
