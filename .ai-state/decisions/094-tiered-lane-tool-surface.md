---
id: dec-094
title: The lane rename targets a tiered surface — flat cross-lane primitives plus six lane dispatchers
status: accepted
category: architectural
date: 2026-08-10
summary: Locked decision 4's rename is executed as a two-tier surface — roughly nine cross-lane conversational primitives kept flat and unrenamed, plus six lane dispatchers carrying the operator surface — because the eleven code-grounded multi-lane verbs make a flat lane-prefixed rename either double the schema weight or assign a lane arbitrarily, while a dispatcher action alias costs nothing.
tags:
  - mcp
  - tool-surface
  - swimlanes
  - agentic-interface
  - rename
  - progressive-disclosure
made_by: agent
agent_type: interface-designer
branch: worktree-process-swimlanes
pipeline_tier: full
# Reciprocal of dec-098's `re_affirms`. Written in draft form on
# purpose: finalize rewrites both halves to their stable ids atomically, and the
# health check exempts drafts from reciprocity but enforces it once promoted.
re_affirmed_by:
  - dec-098
dissent: Collapsing roughly 27 tools into six dispatchers concentrates routing into six long descriptions; `improve` alone would carry around twenty actions, and a fat description is a mini-prompt whose quality is unmeasured and whose failure mode (the model picks the right lane and the wrong action) is quieter than picking the wrong tool.
affected_files:
  - src/knotica/mcp_server/server.py
  - src/knotica/mcp_server/tools_dispatch_loop.py
  - src/knotica/mcp_server/tools_dispatch_arena.py
  - src/knotica/mcp_server/tools_dispatch_compile.py
  - src/knotica/mcp_server/tools_dispatch_branches.py
  - src/knotica/mcp_server/tools_dispatch_datasets.py
  - src/knotica/mcp_server/tools_dispatch_notes.py
  - src/knotica/mcp_server/tools_dispatch_vault_health.py
  - dashboard/src/toolClient.ts
  - docs/reference.md
---

## Context

Locked decision 4 renames the published surface so every entry point speaks in lanes. Read literally,
"MCP tool names … regrouped by lane" means lane-prefixed flat tool names (`learn_curate`,
`fill_gap_report`, …). The user considered and rejected the alternative of projecting lanes over the
existing verbs; *that* the rename happens is settled.

Research then established two facts that decide the **shape** the rename must take:

1. **A lane cannot be a partition of verbs.** Eleven code-grounded multi-lane verbs were found —
   `curate_example` (Learn terminal, Answer reaction, Improve precondition), `gap_report`,
   `source_ingest_submit`, `write_page`/`store_source`, `datasets freeze`, `guillotine --apply`,
   `wiki_status`, `ingest_progress`, `note_capture`, `query`, and `notes action=promote`, which routes
   into **four** lanes on one action string depending on `target`. The locked design rule — "shared
   objects are projected per-lane, never duplicated" — is itself the definition of a facet.
2. **Descriptions, not registrations, are the surface area.** ~200 tool-name cross-references live inside
   description strings across 23 `mcp_server/` files, against 35 registrations. A model re-reads the
   description on every call; it is the executable interface.

Two further facts frame the opportunity: the live surface is **35 tools**, already past the ~20–25
threshold at which model tool-selection quality measurably degrades; and `dec-045` already established a
tiered tool-surface topology, with nine dispatchers shipping today.

## Decision

**Execute the rename as a two-tier surface.**

- **Tier 1 — cross-lane conversational primitives, flat and unrenamed (~9):** `search`, `read_page`,
  `list_topics`, `list_links`, `read_protocol`, `write_page`, `store_source`, `query`, `wiki_status`.
  These belong to no lane, are the highest-frequency calls the client LLM makes, and already carry the
  best available names. Renaming a cross-lane primitive *into* a lane is a category error — `search` is a
  better name than `learn(action="search")`.
- **Tier 2 — the operator surface, six lane dispatchers:** `learn`, `answer`, `improve`, `fill`,
  `tend`, `home` — plus the unlaned `vault` (config-level per `dec-076`) and `open_dashboard`.
- **Net: 35 → ~17 tools.** Below the degradation threshold, and therefore **no meta-tool or lazy-schema
  layer is needed** (which would be required above ~20). The project already practises progressive
  disclosure one level up: `_INSTRUCTIONS` tells the model to call `read_protocol(operation, topic)` for
  the steps rather than inlining them. A lane dispatcher's action table is the same move for the
  operator surface.
- **Multi-lane verbs become action aliases**, not duplicate registrations.
- **Description standard.** A description may name **at most two sibling tools**, and only as a
  "not this, that" disambiguator. Routing lives in exactly two places — `_INSTRUCTIONS` (one copy) and
  each lane's action table (one copy per lane) — never scattered across 23 files. Every tool and action
  description carries four parts: *what it does · Does NOT · Requires · Returns (what the payload means)*.
  Billed two-phase actions are named as billed in the description.
- **Deprecation differs by who reads it.** Tool names: **remove, no alias** (every alias is a schema the
  model pays for every turn; `dec-050` established aliases never converge; the failure is loud and
  in-turn recoverable). Dispatcher actions: **alias in `_ACTIONS` + a `deprecation` note in the
  envelope** — zero schema weight. CLI: `argparse` alias + `Console.warn()` to stderr. Slash commands:
  **tombstone file** — the only channel that reaches the human, and a vanished command is otherwise a
  silent break. Dashboard `?pane=`: alias map.
- **Prerequisite: instrument before renaming.** Dispatch telemetry covers 9 of 35 tools; the 25 flat
  tools emit nothing, and lines are untimestamped and unpersisted. Adding `record_dispatch` to the flat
  tools and a timestamped JSONL sink is a precondition for any claim that the rename did not degrade
  routing.

## Considered Options

### A. Flat lane-prefixed rename (the literal reading of decision 4)

Pros: the most direct reading; every tool name states its lane; no dispatcher indirection.
Cons: **falsified by the eleven multi-lane verbs.** A facet must either register under several
lane-prefixed names — a full extra schema per alias that the model reads every turn, which is `dec-050`'s
exact rejected option at 35→70 scale — or assign a lane arbitrarily to a verb that genuinely serves four.
It also keeps 35 tools, forfeiting the only chance to get under the selection-degradation threshold.
Rejected.

### B. Tiered: flat primitives + six lane dispatchers (**chosen**)

Pros: the facet problem that is fatal for A is **free** here — an action alias is one tuple entry plus
one mapping line with zero added schema weight, because the action is a free-form `str`, not a schema
enum; research independently named dispatcher actions the cheapest deprecation surface in the system.
35 → ~17. Extends `dec-045` rather than inventing a topology. The action-rejection error contract
(`INVALID_ARGUMENT` + `fix=` listing valid actions) is self-correcting and now covers more of the
surface. Cons: six long descriptions; `improve` is the largest.

### C. Presentation-only grouping — keep every name, group by lane in docs and `--help`

Pros: zero blast radius; preserves the client LLM's learned routing entirely; delivers real value at the
CLI for free. Cons: the published *tool* surface stays tool-shaped, which is the thing decision 4
exists to change. Rejected as the whole answer — but adopted as a **component** of B: lane-grouped
`--help` ships regardless, at no cost.

### D. Six lane dispatchers absorbing the primitives too

Pros: the smallest possible surface (~8 tools).
Cons: burying `search`, `read_page` and `read_protocol` inside a lane makes the highest-frequency reads
harder to route to, and they are genuinely lane-less. Minimal surface area is a means, not the end.

## Consequences

**Positive**

- The published surface speaks in lanes at every entry point — decision 4's stated goal, fully met.
- ~200 scattered cross-references collapse into six action tables plus `_INSTRUCTIONS`; the two-sibling
  cap becomes enforceable rather than aspirational.
- Tool count drops below the threshold where selection quality degrades, with no lazy-schema machinery.
- Multi-lane verbs are expressible without duplication or arbitrary assignment.
- More of the surface is covered by the system's best-behaved error contract.

**Negative**

- Each lane dispatcher's description becomes a mini-prompt; `improve` carries ~20 actions.
- A wrong-but-valid action inside the right lane is a quieter failure than a wrong tool name, and the
  current telemetry cannot see it.
- Tests: 68 files carry tool-name literals across 291 `call_tool(` sites; many become `action=` literals.
- `major_version_zero = true`, so a breaking rename bumps 0.1.0 → 0.2.0 with **no semver channel to
  signal breakage**; the changelog (`feat!:` + a `BREAKING CHANGE:` footer carrying the full old→new
  table) is the only channel, and the marketplace bump is a manual, ungated step.
- The dashboard's built artifact is committed and CI-gated; a rebuild is mandatory, not optional.

## Disconfirmation

**Falsifier.** A routing eval over a fixed set of user utterances, run against the old and new name sets,
showing the lane-dispatcher surface routes *worse* than 35 flat tools — for example the model reliably
picking the right lane and the wrong action, at a higher rate than it currently picks the wrong tool.
The eval-harness scaffolding exists (`cli/eval.py`, `evals/`), though it is aimed at answer quality.

**Steelmanned runner-up.** Option C is stronger than its rejection suggests. The client LLM's routing is
*learned against the current names*, and the entire measurable benefit of a rename is a hypothesis;
`dec-050` already removed one alias layer as dead weight, and the plugin's only confirmed consumers are
the author's own clients. Lane-grouped `--help`, lane-grouped docs, a lane-shaped dashboard and a
lane-aware `open_dashboard` deliver most of the user-visible value at **zero** routing risk, zero test
churn and zero release-skew hazard. If the rename is later found to have degraded routing, C is where it
lands anyway — and it could have been the starting point.

**Reversal trigger.** Revisit if (a) a routing eval or the fixed telemetry shows post-rename degradation,
(b) `improve`'s description exceeds ~1,500 tokens — in which case split it by sub-stage
(`improve`, `improve_data`), **never** back to topical tools, or (c) `dec-050`'s own reversal trigger is
formally accepted (an external consumer via the bit-agora marketplace), which would reopen aliasing and
change the deprecation half of this decision — though not the tiered shape.
