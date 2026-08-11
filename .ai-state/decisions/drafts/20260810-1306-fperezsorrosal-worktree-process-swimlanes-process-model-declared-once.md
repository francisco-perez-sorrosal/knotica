---
id: dec-draft-2779ae2a
title: The process model is declared once in core/ — served to the client, mirrored at build time, gated by make verify
status: proposed
category: architectural
date: 2026-08-10
summary: "Six lanes, their ordered stages, each stage's state predicate and advancing action are declared once in a core/ module that also generates the lane dispatchers' action tables; the server ships the live declaration on an existing tool call, the dashboard bundles a generated fallback that make verify holds byte-honest, and stage state is derived server-side so rail semantics have one implementation."
tags: [process-model, swimlanes, dashboard, mcp, cli, single-declaration, generated-artifact]
made_by: agent
agent_type: systems-architect
branch: worktree-process-swimlanes
pipeline_tier: full
affected_files:
  - src/knotica/core/
  - src/knotica/core/ingest_activity.py
  - src/knotica/core/branch_namespaces.py
  - src/knotica/mcp_server/
  - dashboard/src/
  - .ai-state/DESIGN.md
  - Makefile
dissent: "Shipping both a served declaration and a generated bundled fallback means two copies exist at runtime and only one of them is gated — `make verify` proves the fallback matches the Python declaration in *this* build, and nothing proves the *connected server's* declaration matches the fallback the *installed* dashboard carries, which is precisely the version-skew case the fallback was added to survive."
---

## Context

Knotica's process knowledge is real but scattered. `LoopPane.tsx:842-960` builds its four-step rail as a
plain array literal on every render, with no ordering invariant (Observe can be `current` while Heal is
`ready`) and no `blocked` state — a stuck step's reason lives only in a `title` tooltip.
`core/ingest_activity.py:48-68, :254-314` declares two ordered stage tuples properly, with a monotonic
watermark and terminal detection, and is the best implementation in the repo. `compileStages.ts` holds a
third. `VaultPane.tsx:46-51` holds a fourth as a tab tuple. `LoopPane.tsx:28-36` holds a *fifth* — a
second rail on the same pane telling an overlapping story in a different vocabulary.

The swimlane redesign asks for six lanes, each with a rail, projected into four entry points (dashboard,
MCP, CLI, `/knotica:*`) — and, under `dec-draft-36f3ddc2`, the MCP surface itself is re-cut into six lane
dispatchers whose action tables are the same lane/verb mapping. Building that on the current shape means
six more inline literals in TypeScript, invisible to Python, ungated, and unable to answer "which stage
is this vault in?" from anywhere but a browser.

This repo already has the answer pattern twice: `core/branch_namespaces.py`, whose docstring names the
four modules that used to hold copies of the same literals, and `cli/__init__.py::COMMAND_NAMES`.

## Decision

**One declaration: `core/process_model.py`.** It owns the six lanes (`home`, `learn`, `answer`,
`improve`, `fill`, `tend`), each lane's ordered stages, each stage's state predicate, its advancing
surface reference, and a `handoff` flag marking stages the dashboard structurally cannot execute. It
**references** `core/ingest_activity.py`'s existing `INGEST_STAGES` / `CURATE_STAGES` rather than copying
them, so no second source of truth for a stage order is created. Lane *membership* is a set, per
`dec-draft-1d7f84bb`, and it is what generates each lane dispatcher's action table — the tool surface and
the rails are projections of one declaration, not two.

Four rulings follow:

1. **Stage state is derived server-side.** The declaration exports pure predicates; the MCP surface
   returns *derived* per-stage state inside payloads a lane already fetches. The dashboard renders state
   it is given. Rail semantics have exactly one implementation, in Python, unit-testable against a
   fixture vault. This holds `dec-draft-58b8a899`'s rail contract to one producer of truth even where it
   permits two derivations of shape.

2. **The client reads the *served* declaration and falls back to a *generated* bundle.** The live
   declaration ships as data on an existing tool call — never as a new tool, and never as an MCP
   resource, since a sandboxed-bridge resource read is unverified here while `app.callServerTool` is the
   path the dashboard already uses. The dashboard prefers what the connected server sent and falls back
   to `dashboard/src/processModel.ts` when it is absent. This extends `IngestPane`'s existing
   server-declared-with-bundled-fallback precedent, and it makes the **server** authoritative for what it
   can serve, so a skewed client can never offer a lane the connected server does not have.

3. **The bundled fallback is generated, not authored, and `make verify` holds it honest.** It is emitted
   from the Python declaration and re-checked with `git diff --exit-code` — the same instrument the
   committed dashboard artifact and `DESIGN.md` § 3's package inventory already use. This closes the
   drift gap `dec-draft-58b8a899` names in its own Consequences ("two producers must be kept honest
   against one contract; drift between them is a new failure mode with no existing gate"). The fallback
   carries presentation structure only — ids, titles, order, narration, handoff flags — never predicates.

4. **`handoff` is mechanically held, not conventional.** A fitness test asserts that a `handoff=True`
   stage has no dashboard-executable advancing action and that a `handoff=False` stage has one. The
   client-as-brain invariant becomes a checked property of the rail rather than a note in a document —
   which is the sense in which the redesign's locked decision 3 *strengthens* the invariant.

## Considered Options

### Option 1 — Served declaration + generated bundled fallback, both gated (chosen)

Takes the property a served declaration buys (a stage-label change reaches a running server's clients
without a rebuild; the server is authoritative under skew) and pays for the failure mode it introduces
(an ungated second copy) with a generator plus a `make verify` check. Cost: one generated file and one
gate.

### Option 2 — Generated build-time mirror only, no served declaration

Simplest, fully gated, no payload. Rejected: it discards the property `IngestPane` already demonstrates
in this codebase, and it makes the *client* the authority on what lanes exist — so a dashboard newer than
the server it is talking to offers lanes the server cannot serve. Under Option 1 that case degrades to
"the server's smaller lane set wins", which is the correct direction.

### Option 3 — Served declaration only, with a hand-authored bundled fallback

`dec-draft-58b8a899`'s point 5 as literally written. Rejected on the gap that decision itself names: two
producers, one contract, no gate. A hand-authored fallback is a second source of truth wearing a
fallback's clothes.

### Option 4 — Serve it as an MCP resource (`knotica://process-model`)

Semantically the cleanest home for read-only data. Rejected on transport risk: `BridgeToolClient` reaches
the server only through `app.callServerTool`, and whether a resource read works in the sandboxed bridge
mount was not verified. A tool-call payload works on both mounts today.

### Option 5 — Declare the model in TypeScript and expose it to Python

Rejected: the predicates must read vault state (git refs, JSONL records, loop-state files), and the lane
dispatchers' action tables are a Python surface. Putting the declaration on the side that can evaluate
neither inverts the dependency.

## Consequences

**Positive.** Six rails and six lane dispatchers cost one declaration instead of eleven scattered
literals plus six action tables. `LoopPane`'s two competing rails collapse into one. A stage's state
becomes assertable in a Python unit test rather than only in a browser. The CLI gains a lane projection
for free because the model is importable. A stage-label change reaches a running server's clients without
a rebuild. The `handoff` invariant becomes checkable.

**Negative.** Two representations exist at runtime, and only the build-time relationship is gated — the
served declaration and an *installed* dashboard's bundled fallback can still disagree across versions,
which is the case the fallback exists for and the case nothing checks. `src/knotica/core/` grows by one
module, so `DESIGN.md` § 3's inventory count must move 62 → 63 in the same commit or `make verify` fails.
A contributor who edits the generated file gets a diff failure rather than a helpful message.

## Disconfirmation

- **Falsifier:** if some rail state turns out to be knowable only in the browser — a pending local edit,
  an unsent form, a mount capability — then server-side derivation is wrong for that lane and the
  structure/predicate split is drawn in the wrong place. Concretely: any lane whose rail needs a state
  the server cannot observe falsifies ruling 1 for that lane.
- **Steelmanned runner-up:** Option 2. The served declaration buys a property this project has already
  spent — the dashboard's built artifact is committed and CI-diffed, so a rebuild is mandatory on any
  source change regardless; and the runtime skew Option 1 handles gracefully is a skew Option 2 turns
  into a *build* failure, which is strictly earlier and strictly louder. Option 1 wins on the running
  installed-user case; Option 2 wins on every case a developer sees.
- **Reversal trigger:** the served declaration and a bundled fallback disagreeing in a way a user
  notices, **or** the dashboard's built artifact ceasing to be committed and CI-diffed — the first shows
  Option 1's ungated seam biting, the second removes Option 2's main cost.
