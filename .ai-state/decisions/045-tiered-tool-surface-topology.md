---
id: dec-045
title: Tiered MCP tool-surface topology — one server, action dispatchers now, meta-tool later, second server rejected
status: superseded
category: architectural
date: 2026-07-21
summary: Architect ruling on Challenge 1 — adopt the interface-designer's two-tier surface (thin core + 7 operator dispatchers) on a single server (Option A) now; reserve the lazy catalog meta-tool (Option B) as the preferred future evolution gated on client dynamic-tool-loading; reject a second MCP server (Option C); record as a re-affirmation-with-narrowed-universality of dec-003, with an additive-alias non-breaking migration.
tags: [mcp, tool-surface, server-topology, progressive-disclosure, consolidation, routing]
made_by: agent
agent_type: systems-architect
branch: worktree-loop-consolidation
pipeline_tier: full
re_affirms: dec-003
superseded_by: dec-draft-30f2f8ba
affected_files:
  - src/knotica/mcp_server/server.py
  - src/knotica/mcp_server/tools_vault.py
  - src/knotica/mcp_server/tools_scoreboard.py
  - src/knotica/mcp_server/tools_compile.py
  - src/knotica/mcp_server/tools_datasets.py
  - src/knotica/mcp_server/tools_arena.py
  - src/knotica/mcp_server/tools_golden.py
  - dashboard/src/toolClient.ts
dissent: "Action-parameterized dispatchers reintroduce the god-endpoint shape dec-003 rejected; if operators ever become conversationally selected, the model must reason over an action enum plus per-action arg validity — the exact selection ambiguity thin tools remove."
---

## Context

The knotica MCP server registers **49 tools** unconditionally on one FastMCP instance
(grep-verified `@mcp.tool(` = 49; the interface-designer census said 48 — it omitted
`open_dashboard` from `app_ui.py`). The client-as-brain invariant requires the client's
model to select among all registered tools every turn. Documented LLM tool-selection
quality degrades past ~20–25 tools; 49 is ~2×. This directly threatens the transparency
north star, whose whole point is *reliable* autonomous routing of natural conversation.

dec-003 fixed "~10 thin deterministic tools, no progressive disclosure" and named its
own reversal trigger: *"when the tool count approaches ~20 (Phase 3+ adds loop-trigger
and query-program tools), re-evaluate for progressive disclosure."* That trigger has
fired — decisively.

The interface-designer raised this as **Architecture Challenge 1** and proposed a
two-tier surface (`dec-041`): a thin conversational core (~18 tools) plus the
operator long-tail collapsed into 7 action-parameterized domain dispatchers
(`loop`, `branches`, `compile`, `datasets`, `arena`, `golden`, `vault_health`),
~49 → ~29 top-level tools, dispatchers being pure routing over the existing thin
implementations (zero dec-001..038 semantic change). The challenge explicitly deferred
to the architect two questions: (a) the dec-003 supersession *form*; (b) the server-
topology choice among Option A (dispatchers), B (lazy catalog meta-tool), C (second
server).

## Decision

**Adopt the two-tier surface (Challenge 1: adopt-with-modification).** Architect
rulings on the deferred questions:

1. **Server topology: Option A now, one server.** Consolidate the operator long-tail
   into 7 action dispatchers on the single existing FastMCP instance. Rationale: A is
   robust on *every* client (no dynamic-tool-loading dependency), preserves the
   stateless-single-server story, and its cost (a model reasoning over an action enum
   for operator calls) is **low-frequency** because operators are invoked by the
   dashboard / CLI / explicit "run the loop" asks, not conversational selection — while
   the benefit (halving the always-loaded schema the model sifts on *every* turn,
   including all conversational turns) is paid continuously. That asymmetry is the crux.
2. **Option B (lazy catalog meta-tool) is the preferred *future* evolution**, gated on
   dynamic/lazy tool-loading being reliable across the target client set **including
   Claude Desktop** (an explicit target that lacks it today). B is strictly better on
   token economy and keeps every tool thin; revisit when the client capability lands.
3. **Option C (second MCP server) is rejected** — it fragments the stateless-server
   story and adds operational topology for no proportional benefit at this scale.
4. **ADR form: re-affirmation-with-narrowed-universality of dec-003, not supersession.**
   dec-003's decision remains literally true: the conversational core stays thin, and
   dispatchers are **not** progressive disclosure (Option B would be; it is deferred).
   Only the *universality* of "thin everywhere" narrows — thinness is kept exactly where
   selection matters (the core). The interface-designer's `re_affirms: dec-003` framing
   is therefore correct and confirmed.
5. **Migration is additive-alias and non-breaking.** Each consolidated operator tool is
   reachable by both its old name (deprecated, aliased) and the new dispatcher for one
   release cycle. Blast radius includes surfaces the interface-designer under-scoped:
   the dashboard `dashboard/src/toolClient.ts` call sites and the CLI-parallel naming —
   tracked as explicit migration items, not incidental.

`dec-041` (interface-designer) remains the companion decision owning the
dispatcher *shapes / naming / decomposition*; this ADR owns the *server-topology* axis
and the migration-safety + ADR-form rulings.

## Considered Options

### Option A — Action dispatchers on one server (chosen)
Works on every client incl. Desktop; pure routing, zero semantic change; in-tree
precedent (`suggestions_review`, `source_ingest_submit`). Cost: mild god-endpoint shape
for operators — accepted because operators are rarely conversationally selected.

### Option B — Lazy catalog meta-tool (deferred, future-preferred)
`knotica_tools(domain)` serving operator schemas on demand (large token reduction in the
literature); keeps every tool thin. Rejected as the *now* mechanism only because it
depends on client dynamic-tool-loading that Desktop lacks. Additive on top of A later.

### Option C — Second MCP server for operators (rejected)
Heavier operational topology; fragments the stateless-server story; no proportional win.

### Option D — Status quo, 49 flat tools + rely on the skill (rejected)
The skill fires only in skill-aware clients; the token + selection cost is paid every
turn regardless; routing reliability stays degraded on Desktop — the exact north-star
failure.

## Consequences

**Positive:** surface roughly halved toward the selection threshold; the conversational
core is isolated and legible; operator growth is additive-by-action; loop-family naming
(today split across `tools_vault.py` and `tools_scoreboard.py`) unifies under one
`loop`/`branches` dispatcher; the CLI's already-subcommand-structured surface aligns with
the tool surface.

**Negative:** dispatchers concentrate operations behind one schema — the per-action arg
matrix must be documented crisply or the model mis-fills args (mitigated by `mode=dry-run`
previews and per-action `INVALID_ARGUMENT`, `dec-040`); a mild god-endpoint
shape for operators, in tension with dec-003's letter (accepted — dec-003's *intent*,
selection clarity where it matters, is served by the thin core). Migration touches the
dashboard client and CLI naming.

## Disconfirmation

- **Falsifier:** if instrumentation shows the model mis-selecting or mis-arg-ing the
  operator dispatchers more often than it mis-selected the equivalent thin tools, the
  consolidation hurt and should be reverted for the affected domain.
- **Steelmanned runner-up:** Option B (lazy catalog meta-tool) is strictly better on
  token economy and keeps every tool thin; if the target-client set drops Desktop, or
  Desktop gains dynamic tool loading, B dominates A and should replace it.
- **Reversal trigger:** a client-capability shift (Desktop supports lazy tool loading),
  or the operator surface growing past one-dispatcher-per-domain legibility, reopens this
  in favor of B or C.

## Prior Decision

Re-affirms **dec-003** (thin deterministic tools, no progressive disclosure). dec-003
stays `accepted`: the conversational core stays thin (its principle), and no progressive
disclosure is added (dispatchers are consolidation, not disclosure). The universal
application of thin-everywhere produced the 49-tool flat surface that works *against*
routing reliability, so the operator long-tail is consolidated into action dispatchers —
a scope-narrowing this architect ADR owns, not a reversal of dec-003's principle.

## Superseded (Partial) Addendum — 2026-07-22

`dec-draft-30f2f8ba` supersedes only Decision ruling 5 above ("Migration is
additive-alias and non-breaking"): the 26 deprecated aliases it introduced were
removed outright once their migration-window premise (external clients requiring
gradual migration) was confirmed never to apply to this single-consumer,
self-operated project. Rulings 1-4 — server topology (Option A), Option B deferral,
Option C rejection, and the ADR-form ruling (re-affirmation-with-narrowed-
universality of dec-003) — are unaffected and remain the governing decision.
