# Query — operation protocol

You are performing a knotica **query**: answer a question from this wiki's pages, with
citations. Prefer the one-shot path below; explore with search/read only when needed.

**Arguments**: `question` — the user's question; `topic` — optional explicit topic (may
be empty).

## Prefer the `query` tool for one-shot answers

Call the MCP tool `query` with the resolved `topic` and the user's `question`. It returns
`answer`, `citations`, and `pages_used` — the single wiki-answer API (dashboard Ask and
headless scoring use the same tool). Do **not** look for a second answer tool.

Use the exploratory steps below only when the user wants to browse, compare pages, or
you need to investigate before answering.

## 1. Resolve the topic

> **Topic-inference policy.** Call `list_topics`. If the caller passed an explicit
> `topic`, use it (override always wins). Otherwise infer: if the material clearly
> matches one existing topic, auto-place there; if it is ambiguous across topics or
> warrants a new topic, ask the user, and on confirmation call `create_topic`. Always
> pass the resolved topic explicitly to every tool — the server holds no active-topic
> state.

Query-specific note: when the question spans topics or matches none clearly, you may
search all topics instead of asking — pass an empty `topic` to `search`. Do not create a
topic just to answer a question.

## 2. Read the effective schema (exploratory path)

Read the resource `knotica://schema/resolved/{topic}` (substitute the resolved topic)
using your client's resource-read mechanism — an `@`-mention of the URI or the client's
built-in resource reader. Resources are not auto-loaded. It tells you the topic's entity
types and what the frontmatter fields (`confidence`, `status`, `sources`) mean, so you
can weigh and cite pages correctly.

## 3. Search, then read (exploratory path)

- Call `search` with the `question`'s key terms as `query` (scope with `topic`, or empty
  for all topics). Results are pointers — path, snippet, score — not page bodies. If
  `has_more` is true and the results so far are insufficient, pass `next_cursor` back as
  `cursor` for the next page.
- Call `read_page` for each pointer you judge relevant — spend reads only on pages you
  actually need.
- Optionally call `list_links` on a promising page (`direction: "both"`) to discover
  related or superseding pages worth reading.

## 4. Synthesize (exploratory path) — with citations

Answer from the pages you read, not from memory. **Citation discipline is mandatory**:
every substantive claim cites the vault page(s) it came from, as full-path wikilinks
(`[[<topic>/<page>]]`). Flag anything drawn from a page whose frontmatter says
`confidence: low` or `status: stale`. If the wiki cannot answer, say so plainly — do not
fill gaps silently with outside knowledge; if you add outside context, label it as not
from the wiki.

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

## 5. Finish — answer, then offer to curate

Give the answer with its citations. Then **always end by asking, in one keystroke,
whether the answer was good** — curated examples are what make future answers better. On
a yes or no, call `curate_example` with the resolved `topic`, the `question` as `query`,
the cited pages as `pages_used`, your `answer`, and `verdict` (`good` or `bad`); if the
user corrected the answer, record the correction in `notes`.
