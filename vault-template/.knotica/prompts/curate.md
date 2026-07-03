# Curate — operation protocol

You are performing a knotica **curate**: explicitly save the last interaction as a
curated example in the topic's flywheel dataset (`.knotica/datasets/qa.jsonl`). Curated
examples are the raw material the wiki's operations are later optimized from — this is
the manual path; ingest and query also solicit it automatically.

**Arguments**: `topic` — optional explicit topic (may be empty); `verdict` — optional
user judgment of the interaction (`good` or `bad`; may be empty).

## 1. Resolve the topic

Use the explicit `topic` argument if given. Otherwise take the topic of the interaction
being curated from the conversation context; if that is unclear, call `list_topics` and
ask the user which topic the example belongs to. Always pass the resolved topic
explicitly — the server holds no active-topic state.

## 2. Gather the example from context

From the interaction being curated (normally the most recent query or ingest in this
conversation), collect:

- `query` — the question or request the user made.
- `pages_used` — the vault page paths that grounded the answer (the pages cited or
  written).
- `answer` — the answer or summary that was given.
- `verdict` — from the `verdict` argument, or ask the user: was the answer `good` or
  `bad`? If the user corrected the answer, capture the correction in `notes`.

If any of these cannot be recovered from context, ask the user rather than inventing
them. If you need the topic's conventions to normalize page paths, read the resource
`knotica://schema/resolved/{topic}` via your client's resource-read mechanism.

## 3. Append the example

Call `curate_example` with `topic`, `query`, `pages_used`, `answer`, `verdict`, and
optional `notes`. The append is one atomic unit — it validates the record, commits once,
and writes the `log.md` entry itself; never write `log.md` or the dataset file yourself.
Re-submitting an identical example is a safe no-op (`appended: false`).

## 4. Report

Confirm what was saved and relay the returned `example_count` — e.g. "saved; the topic
now has N curated examples."

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
