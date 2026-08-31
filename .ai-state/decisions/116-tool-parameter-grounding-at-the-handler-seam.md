---
id: dec-116
title: Tool parameters are grounded at the handler seam, with advisory enums read from the validating constant
status: accepted
category: implementation
date: 2026-08-31
summary: A new mcp_server/tool_params.py declares one Annotated alias per parameter meaning; handlers annotate their own parameters with it, the lane union inherits the metadata when the contributing verbs agree and degrades to the plain type when they disagree
tags: [mcp, schema, agentic-interface, tool-descriptions, enums]
made_by: agent
agent_type: implementer
branch: main
pipeline_tier: standard
affected_files:
  - src/knotica/mcp_server/tool_params.py
  - src/knotica/mcp_server/tools_dispatch_lane_common.py
  - tests/test_mcp_schema_grounding.py
---

## Context

The pre-release review's F-MS-06 measured the published surface: **236 parameters, 0 schema
descriptions, 1 advisory enum**. Every property was `{"title": "Suggestion Id", "type": "string"}` —
a title auto-derived from the parameter name, carrying nothing the name did not already carry. The
legal values of `decision`, `mode`, `status`, `target` and `verdict` lived only in tool-description
prose, up to 2.2 KB away from the field they constrain.

A model re-reads the schema on every call and cannot accumulate domain knowledge across sessions, so
per-parameter grounding is the highest-leverage lever this surface has. The constraint is that the
six lane dispatchers do not *own* their parameters: `_lane_signature` builds each lane's call shape
as the **union** of its member verbs' own handler signatures. Any grounding declared at the lane
would be a second declaration, free to drift from the handler it describes.

## Decision

Annotate at the **handler seam**. `mcp_server/tool_params.py` declares one `Annotated` alias per
parameter *meaning* (`Topic`, `Vault`, `Limit`, `Cursor`, `SuggestionId`, …); handlers annotate
their own parameters with those aliases, and `_lane_signature`'s union inherits the metadata with no
second declaration in play. Parameters local to one module are annotated beside their handler with
the same helper.

Three sub-rules make it hold:

1. **Advisory, never `Literal`.** Every enum rides as `json_schema_extra`. A `Literal` would have
   pydantic reject an unknown value with a raw validation string, replacing the typed
   `{code, message, fix, retryable}` envelope and destroying the `record_rejected_action` signal.
2. **The vocabulary is referenced.** Each enum reads the constant the handler's own validation uses
   (`_ACTIONS`, `_MODES`, `_STATUS_FILTERS`, `ARENA_SCORERS`, `VALID_STATUS_VIEWS`, …). Where none
   existed (`_GAP_DECISIONS`, `_ROLES`, `_DIRECTIONS`, `_SOURCE_TYPES`, `_BASELINE_POLICIES`,
   `_DIFF_MODES`, `_PROGRESS_STATUSES`) one was created next to the handler and wired into both the
   enum and the validation's `fix` text.
3. **Disagreement degrades, it does not guess.** `_optional` preserves the union's metadata only
   when every contributing verb annotated the name identically; when two verbs mean different things
   by one name (`mode` is `dry-run|apply`, `best|latest` *and* `compile|loop`), it falls back to the
   plain type. Seven lane parameters are undescribed by design, named one by one in the census test.

`grounded()` returns the `Field` — the metadata — rather than the finished alias, so every site
reads `Annotated[X, grounded(...)]`: a subscript expression mypy accepts as a type alias.

Result: **229/236 described (97%), 50 enums**, up from 0 and 1.

## Considered Options

### Annotate at the lane dispatcher

Rejected. `_lane_signature` would need a per-lane parameter table beside `_PURPOSE_DESCRIPTION` —
exactly the second declaration this module exists to avoid, and one that drifts silently because
nothing mechanically ties it to the handler signature it describes.

### `Literal[*lane_actions(lane)]` on the selector (the review's own suggestion (1))

Rejected on the batch-C evidence already recorded in `_selector_annotation`: pydantic enforcement
replaces the structured envelope with a raw validation string and removes the only instrument that
sees which action a client *meant*. Advisory in the schema, enforced at `_reject`, keeps all three.

### Publish a union description for a disagreeing name

Rejected. A `mode` description enumerating "dry-run|apply for mutating verbs, best|latest for
rebaseline, compile|loop for prompt_diff" invites `fill action=gapfill_discover mode=best`. Saying
nothing is honest; saying something partially wrong on a billed surface is not.

## Consequences

**Positive.** Per-argument grounding on 97% of the surface; a misspelled action is now visible to
the host before the round trip; the enum and the enforced set cannot diverge, because they read one
constant; seven new named vocabulary constants replace prose-only value lists.

**Negative.** One new module and ~70 alias definitions to keep in prose-quality repair. A parameter
added to a handler without an alias silently joins the undescribed tail — mitigated by the census
test, which pins the exceptions by name rather than by count.
