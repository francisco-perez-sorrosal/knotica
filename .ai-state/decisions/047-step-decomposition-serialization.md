---
id: dec-047
title: Step-decomposition serialization — dispatcher create-then-wire split, server.py edit ordering, SessionStart hook consolidation
status: accepted
category: implementation
date: 2026-07-21
summary: Three step-ordering/module-structure decisions made while decomposing SYSTEMS_PLAN.md into IMPLEMENTATION_PLAN.md — split dispatcher-module creation from server.py registration wiring, serialize the two SYSTEMS_PLAN-claimed-disjoint groups' server.py edits, and merge the two hooks/session_start.sh enrichments (P-C topic-seed, P-D attention-nudge) into one step.
tags: [planning, step-ordering, module-structure, mcp-server, session-start, parallel-safety]
made_by: agent
agent_type: implementation-planner
branch: worktree-loop-consolidation
pipeline_tier: full
affected_files:
  - src/knotica/mcp_server/server.py
  - src/knotica/mcp_server/tools_dispatch_loop.py
  - src/knotica/mcp_server/tools_dispatch_branches.py
  - src/knotica/mcp_server/tools_dispatch_compile.py
  - src/knotica/mcp_server/tools_dispatch_datasets.py
  - src/knotica/mcp_server/tools_dispatch_arena.py
  - src/knotica/mcp_server/tools_dispatch_golden.py
  - src/knotica/mcp_server/tools_dispatch_vault_health.py
  - hooks/session_start.sh
affected_reqs: [REQ-04]
dissent: "Serializing the two server.py edit points (dispatcher wiring, _INSTRUCTIONS slimming) costs one explicit depends-on edge and slightly delays P-C's start relative to a fully-parallel P-B/P-C; the alternative (let both land independently and merge-conflict-resolve later) trades a small ordering cost for a real risk of one agent clobbering the other's edit to the same file."
---

## Context

`SYSTEMS_PLAN.md` states P-B (tool-surface), P-C (transparency/routing), and P-D
(loop lifecycle) are "largely independent in file scope (`core/` internals vs
`mcp_server/` surface vs plugin layer vs dashboard)." Verifying this during step
decomposition surfaced two concrete violations of that claim, both real file-level
overlaps invisible at the phase-group granularity:

1. **`server.py` is touched by both P-B (wiring 7 new dispatcher `register_*`
   calls) and P-C (slimming the `_INSTRUCTIONS` constant).** Different regions of
   the same file, edited by different steps — a genuine two-writer conflict if
   scheduled as concurrent parallel-group members.
2. **`hooks/session_start.sh` is the target of two separate SYSTEMS_PLAN bullets**:
   P-C's "SessionStart topic-awareness seed" and P-D's "what needs my attention"
   aggregation nudge. Both are small, additive enrichments to the same cold-path
   script with the same author concern (proactive session-start surfacing).
3. Registering 7 new dispatcher tools into `server.py`'s single `build_server()`
   function is a classic sequential tool-registration hazard (see
   `tool-registration-sequential-pattern` prior-feature learning) — 7 concurrent
   agents each appending a `register_dispatch_X(mcp)` call and import line to the
   same function body is a guaranteed merge collision even though the 7 *new
   module files* are themselves fully disjoint.

## Decision

1. **Split dispatcher work into create (parallel) then wire (sequential).** The 7
   dispatcher modules are created as 7 fully parallel implementer+test-engineer
   pairs (disjoint new files, each independently unit-testable by passing a bare
   `FastMCP()` instance) — no `server.py` edits in this batch. A single sequential
   step then wires all 7 `register_dispatch_*(mcp)` calls into `server.py`,
   `depends-on` all 7 creation steps.
2. **Serialize the two `server.py` writers across groups.** P-C's `_INSTRUCTIONS`-
   slimming step carries `depends-on` the P-B wiring step, even though the two
   groups are otherwise schedulable in parallel. This is the only cross-group file
   dependency in the plan; every other P-B/P-C/P-D step pair is file-disjoint.
3. **Merge the two SessionStart enrichments into one step.** The topic-awareness
   seed (P-C) and the attention-nudge aggregation (P-D) land as one implementer +
   test-engineer pair in P-C's group, satisfying both architecture bullets from a
   single edit to `hooks/session_start.sh`. P-D's step list cross-references this
   step rather than duplicating it.

## Considered Options

### Option A — Trust the architect's disjointness claim, schedule all groups fully parallel (rejected)
Simplest to state, but two concrete file-overlaps (`server.py`, `session_start.sh`)
would produce real merge conflicts between concurrently-spawned agents — the
exact failure mode the parallel-group file-disjointness rule exists to prevent.

### Option B — Chosen: verify-then-serialize only the overlapping edit points
Keep every other aspect of the architect's parallelization intact; add the
minimum number of `depends-on` edges and step merges needed to make the file-sets
actually disjoint within each parallel group, as required by the planning skill's
parallel-step validation criterion.

### Option C — Give P-B and P-C their own copies of server.py sections via a plugin/hook mechanism (rejected)
Over-engineering for a one-time, one-file overlap; would add indirection with no
reuse benefit and contradicts Simplicity First.

## Consequences

**Positive:** every parallel group in `IMPLEMENTATION_PLAN.md` is now genuinely
file-disjoint (the actual criterion, not the phase-group label); the dispatcher
create/wire split also gives each of the 7 dispatchers an isolated, mockable unit
test surface before touching the live server; the SessionStart merge avoids a
churny two-PR dance over one 95-line script.

**Negative:** P-C's `_INSTRUCTIONS` step cannot start until P-B's wiring step
lands, narrowing the practical parallelism between the two groups by one step
each; the merged SessionStart step slightly widens that one step's scope (two
architecture bullets satisfied by one commit) versus the plan's usual one-bullet-
per-step granularity.

## Disconfirmation

- **Falsifier:** if a future re-read of `server.py` shows the `_INSTRUCTIONS`
  constant and the tool-registration list are in genuinely separate files (e.g.
  after a future split), this serialization decision should be dropped and P-B/P-C
  freed to run fully parallel again.
- **Steelmanned runner-up:** Option A, if the two edits are small enough that a
  human merge is faster than modeling the dependency — true for a single edit pair
  but does not scale as more phase-groups accrete `server.py` edits over time.
- **Reversal trigger:** `server.py` growing a second always-loaded constant block
  or a third phase-group needing a `server.py` edit should prompt splitting
  `server.py` itself (registration list vs. instructions vs. app wiring) rather
  than continuing to serialize steps around one large file.
