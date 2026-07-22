---
id: dec-041
title: Tiered MCP tool decomposition — thin conversational core, consolidated operator dispatchers
status: accepted
category: architectural
date: 2026-07-21
summary: Split the 48-tool flat surface into a thin conversational core (verb-noun tools) and an operator long-tail collapsed into action-parameterized domain dispatchers; refines dec-003.
tags: [mcp, tool-surface, progressive-disclosure, agentic-interface, consolidation]
made_by: agent
agent_type: interface-designer
branch: worktree-loop-consolidation
pipeline_tier: full
re_affirms: dec-003
affected_files:
  - src/knotica/mcp_server/server.py
  - src/knotica/mcp_server/tools_vault.py
  - src/knotica/mcp_server/tools_scoreboard.py
  - src/knotica/mcp_server/tools_compile.py
  - src/knotica/mcp_server/tools_datasets.py
  - src/knotica/mcp_server/tools_arena.py
  - src/knotica/mcp_server/tools_golden.py
dissent: "Action-parameterized dispatchers reintroduce the god-endpoint smell dec-003 rejected; a model must reason over an action enum plus per-action arg validity, which is exactly the tool-selection ambiguity thin tools were meant to remove."
---

## Context

The knotica MCP server registers **48 tools unconditionally** on one FastMCP
instance (`server.py`). The client-as-brain invariant requires the client's
model to select among all registered tools on every turn. Documented LLM
tool-selection quality degrades measurably past ~20–25 tools presented at once;
48 is roughly 2×. This directly threatens the transparency north star, which
depends on the model *reliably* routing natural conversation into the right
operation without the user naming machinery.

The 48 tools are not one audience. A minority are the **conversational core**
the client-as-brain reaches from natural language (`query`, `gap_report`,
`suggestions_read`/`suggestions_review`, `source_ingest_open`/`_submit`, the
ingest/read primitives, `read_protocol`, `ingest_progress`). The majority are
**operator / dashboard / loop-maintenance** tools invoked deliberately (CLI,
dashboard buttons, explicit "run the loop" asks): the `loop_*` (4), `branch_*`
+ `loop_promote` (4), `compile_*` (3), `datasets_*` (5), `arena_*` (2),
`golden_review_*` (2), `doctor_*`/`okf_*`/`vault_*` (6), `prompt_diff` (1),
`metrics_read`/`baseline_probe`.

dec-003 established thin, deterministic, single-purpose tools. That decision is
correct for the conversational core — param clarity is what lets the model pick
the right tool from an ambiguous utterance. But applied *universally* it
produces the 48-tool flat surface that now works against routing reliability.

## Decision

Adopt a **two-tier tool surface**:

1. **Conversational core (~17 tools) — stay thin (dec-003 re-affirmed).**
   Verb-noun names, one purpose each, minimal args: `query`, `search`,
   `read_page`, `list_topics`, `list_links`, `lint_check`, `read_protocol`,
   `store_source`, `write_page`, `create_topic`, `curate_example`,
   `ingest_progress`, `gap_report`, `suggestions_read`, `suggestions_review`,
   `source_ingest_open`, `source_ingest_submit`, `wiki_status`. These are what
   the model reasons over from natural conversation; thinness is load-bearing
   for selection accuracy.

2. **Operator long-tail — collapse into action-parameterized domain
   dispatchers.** One dispatcher per domain, each with an `action` enum and the
   existing `mode=dry-run|apply` split for mutations:
   - `loop(action=run_once|set_baseline|baseline_policy|rebaseline)`
   - `branches(action=scoreboard|promote_loop|promote|delete)`
   - `compile(action=run|status|promote)`
   - `datasets(action=inventory|records|bootstrap|bootstrap_train|freeze)`
   - `arena(action=status|history)`
   - `golden(action=load|save)`
   - `vault_health(action=doctor|repair|okf_check|okf_repair|lint|metadata_tree)`

   ~26 operator tools → 7 dispatchers (net −19). Total surface ≈ 29, with the
   ~17-tool conversational core clearly separable and every operator domain
   reachable via one predictable entry.

Naming convention becomes a rule (see the routing/growth ADRs): core tools are
bare `verb`/`verb_noun`; operator families are a single `<domain>` dispatcher
with an `action` enum. New operator operations land as new `action` values on an
existing dispatcher — additive, no new top-level tool, no consumer breakage.

## Considered Options

### Option A — Action-parameterized operator dispatchers (chosen)
Robust across every client (no dynamic-tool-loading dependency; works on Claude
Desktop). Preserves all dec-001..038 semantics — dispatchers are pure routing
over the existing thin implementations. Precedent already in-tree:
`suggestions_review` is action-parameterized (approve/reject/defer/mark_ingested)
and `source_ingest_submit` is mode-parameterized. Cost: the model reasons over an
action enum for operator calls — acceptable because these are rarely conversational.

### Option B — Progressive disclosure via a catalog meta-tool
A `knotica_tools(domain)` that lazily serves operator schemas on demand
(85–100× token reduction in the literature). Rejected as the primary mechanism:
depends on client support for dynamic/lazy tool loading, which Claude Desktop —
an explicit target (`docs/CLAUDE_DESKTOP.md`) — does not provide. Viable later as
an *additive* optimization on top of Option A, not a replacement.

### Option C — Second MCP server for operator tools
Split loop/dataset/compile maintenance into a separate server the dashboard/CLI
mounts. Rejected for now: heavier operational topology, and it fragments the
stateless-server story. Revisit only if the operator surface itself grows past a
dispatcher-per-domain being legible.

### Option D — Status quo (48 flat tools) + rely on the skill to steer
Rejected: the skill only fires in skill-aware clients; the token + selection cost
is paid on every turn regardless; the north star's routing reliability stays
degraded on Desktop.

## Consequences

**Positive:** surface roughly halved toward the selection-quality threshold;
conversational core isolated and legible; operator growth is additive-by-action;
loop-family naming (currently split across `tools_vault.py` and
`tools_scoreboard.py`) unified under one `loop`/`branches` dispatcher.

**Negative:** dispatchers concentrate several operations behind one schema — the
per-action arg matrix must be documented crisply or the model mis-fills args
(mitigated by `mode=dry-run` previews and per-action `INVALID_ARGUMENT` errors,
see the error-grammar ADR). Reintroduces a mild god-endpoint shape for operators,
in tension with dec-003's letter (accepted: dec-003's *intent* — selection
clarity where it matters — is served by keeping the core thin).

## Disconfirmation

- **Falsifier:** if instrumentation shows the model mis-selecting or mis-arg-ing
  the operator dispatchers more often than it mis-selected the equivalent thin
  tools, the consolidation hurt rather than helped and should be reverted for the
  affected domain.
- **Steelmanned runner-up:** Option B (lazy catalog meta-tool) is strictly better
  on token economy and keeps every tool thin; if the target-client set drops
  Desktop or Desktop gains dynamic tool loading, B dominates A.
- **Reversal trigger:** a client-capability shift (Desktop supports lazy tool
  loading) or the operator surface growing past one-dispatcher-per-domain
  legibility should reopen this in favor of B or C.

## Prior Decision

Re-affirms **dec-003** (thin deterministic tools) for the conversational core —
thinness there is exactly what makes natural-language routing reliable. Extends
it: the universal application of thin-tools produced the 48-tool flat surface that
works *against* routing reliability, so the operator long-tail is consolidated
into action dispatchers. dec-003 stays `accepted`; this ADR narrows its scope of
universality rather than reversing its principle. The architect should confirm
whether this is best recorded as a re-affirmation-with-carve-out (as drafted) or a
partial supersession.
