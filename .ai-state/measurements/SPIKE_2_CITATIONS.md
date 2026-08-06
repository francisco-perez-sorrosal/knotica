# Phase 3 Spike 2 — native `search_result` citations: closed, not achievable

**Date:** 2026-07-31. **Cost:** zero — documentation and specification research only.
**Verdict:** **Closed as not achievable.** Blocked by the MCP specification itself, not by a
vendor rollout, so there is nothing to wait for.

---

## The question

`SYSTEMS_PLAN` § Sequencing states it as two parts:

> Can an MCP tool result carry `search_result` blocks to Claude Desktop, and can a later tool call
> read the citation metadata back? Settles P1-a vs P1-b for *capture precision only*.

The brief raised its priority, reasoning that native citations would let the client pass a
precise, verbatim span it did not hand-copy — upstream of the whole anchoring problem.

## Answer to part 1 — no, and a spec-compliant server cannot even emit one

**MCP's tool-result content union does not contain `search_result`.** The current specification
revision (**2026-07-28**) enumerates exactly five unstructured content types:

| type | |
|---|---|
| `text` | |
| `image` | |
| `audio` | |
| `resource_link` | |
| `resource` | embedded resource |

plus a separate `structuredContent` field. The prior revision (`2025-06-18`) enumerates the same
five — this is not a recent removal or a pending addition.

`search_result` is a **Messages API** content block, not an MCP one. Anthropic's own documentation
for it describes two delivery paths — "from tool calls" and "as top-level content" — where *"from
tool calls"* means the API caller constructs a `{"type": "tool_result", "content": [...]}` block in
its own request. **The search-results documentation does not mention MCP anywhere.** The block is
produced by the host application talking to the Messages API, not by an MCP server talking to a
host.

**Corroborating evidence from Anthropic's own bridge code.** `claude-agent-sdk-python` issue #574
reports `search_result` blocks returned by an MCP tool being *silently dropped* — its MCP
tool-result handler recognises only `.text` and `.data`+`.mimeType`, so anything else falls through
both branches and the tool result arrives as `{"content": []}`. The issue is **closed with no
maintainer response**, and the reporter's stated workaround is to bypass the Agent SDK and call
`anthropic.AsyncAnthropic` directly for any turn needing citations.

That issue is about the Agent SDK rather than Claude Desktop, so it is corroboration rather than
proof for Desktop specifically. It does not need to be proof: the specification answer already
settles it. A server that emitted a `search_result` block would be emitting a content type outside
the protocol's union, and the behaviour of any client receiving it is undefined by construction.

`structuredContent` is not an escape hatch either — Claude Desktop is separately reported to ignore
it and process only the `content` field.

## Answer to part 2 — no, not from the server side

Citations arrive as `search_result_location` objects attached to the assistant's **text blocks** in
the Messages API response:

```json
{ "type": "search_result_location", "cited_text": "…", "source": "…",
  "title": "…", "start_block_index": 0, "end_block_index": 1 }
```

These live in the host application's conversation state. **An MCP server sees only the arguments of
a tool call.** Citation metadata therefore reaches `note_capture` only if the model copies it into
an argument — which is precisely the hand-copying the spike existed to eliminate. There is no
protocol channel that carries it structurally.

Note also that `cited_text` is block-granular, not span-granular: *"Claude cites whole blocks, not
substrings within a block"*. Even in the Messages API path, a citation would return the whole
supplied block rather than the sub-span a reader highlighted.

## What this settles

- **P1-c stands as both floor and ceiling.** Server-side verbatim verification of a client-supplied
  quote is what ships, and it is what will keep shipping. The `provenance: verified` / `page` split
  already implemented is the design's honest answer to capture precision.
- **P1-a vs P1-b is resolved by elimination**, not by measurement. There is no vendor capability to
  wait on, so this item should not be carried forward as "blocked" or "deferred".
- **Nothing else in Phase 3 loses value.** The brief cautioned that settling Spike 2 early "may
  reduce the value of everything else in this phase". It does not — the capture-guidance lever from
  `dec-062` part 3 (prefer one complete sentence) remains the only means of influencing quote
  shape, and it stays exactly as valuable as it was.

## Why this matters less than the brief expected

The brief raised Spike 2's priority on the premise that **quote shape dominates recovery**. Step 1
measured that premise on real rewrites and it no longer holds: on KB content pages the hard-orphan
rate is essentially flat across quote shapes — whole-sentence 38.7%, sub-clause 37.6%, two-sentence
38.9% (see `STEP1_ORPHAN_RATE.md`). `dec-062`'s geometry fix already removed the shape sensitivity
that native citations were meant to sidestep.

So the two Phase 3 findings compose: **Step 1 devalued Spike 2 before Spike 2 turned out to be
impossible.** Even had MCP carried `search_result` blocks, the capture-precision gain would have
been buying down a failure mode that measurement shows is no longer load-bearing.

## Sources

- MCP specification, current revision — [Tools, § Tool Result](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)
- MCP specification — [Versioning](https://modelcontextprotocol.io/specification/versioning) (confirms `2026-07-28` is current)
- MCP specification, prior revision — [Tools (2025-06-18)](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)
- Anthropic — [Search results](https://platform.claude.com/docs/en/build-with-claude/search-results)
- [`anthropics/claude-agent-sdk-python` issue #574](https://github.com/anthropics/claude-agent-sdk-python/issues/574) — `search_result` blocks dropped by the MCP tool-result handler
