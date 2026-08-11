---
id: dec-draft-a2d98267
title: The handoff stage is built on observation, with dispatch as progressive enhancement
status: proposed
category: architectural
date: 2026-08-10
summary: A lane stage the dashboard structurally cannot execute renders as a first-class handoff built on a read-only session projection that is derivable from git on both mounts; the conversation-dispatch affordance is capability-tiered progressive enhancement down to copy-the-text, and the slash command is a hint rather than the mechanism.
tags:
  - dashboard
  - swimlanes
  - handoff
  - mcp-apps
  - client-as-brain
  - interface-design
made_by: agent
agent_type: interface-designer
branch: worktree-process-swimlanes
pipeline_tier: full
dissent: Building the stage on a new read-only projection front-loads real server work for a capability (`ui/message`) that may in fact be present on the only host that matters, in which case a dispatch-first stage would have shipped sooner and simpler.
affected_files:
  - dashboard/src/App.tsx
  - dashboard/src/toolClient.ts
  - src/knotica/mcp_server/tools_source_ingest.py
  - src/knotica/core/source_ingest.py
  - src/knotica/core/candidate_gate.py
---

## Context

Client-as-brain is a project invariant: the MCP server exposes deterministic tools and the client's LLM
does the cognitive work. Several lane stages are therefore steps the **dashboard structurally cannot
execute** — most sharply, Fill's `ingest`, where `source_ingest_open` opens a candidate session that
the *client* writes into via the additive `candidate=` argument on `store_source`/`write_page`.

The locked decision is that such a step renders as a first-class handoff stage rather than a hole in the
lane. Research established the constraint precisely:

- **SEP-1865 has no slash-command primitive at all.** Whether a host interprets a leading `/` as a
  command is host-specific and unspecified.
- **Bridge mount**: `ui/message`, `ui/update-model-context` and `sampling/createMessage` exist and are
  capability-gated. The dashboard uses **none** of them and has **no capability plumbing** — the `mount`
  string at `App.tsx:98` feeds a status label only. Claude Desktop's advertised `hostCapabilities` are
  **unverified**.
- **HTTP mount**: no host, no bridge, and therefore **no programmatic path to the conversation
  whatsoever**. This is structural, not a gap to be closed.
- **Observation, by contrast, is fully solvable on both mounts.** Branch existence (`loop/wip/*`,
  `loop/c/*`, `loop/x/*`) plus `preview_resume`'s `source_present`/`pages_present` plus the suggestion
  record's `gate_outcome` yield all seven session states without the client reporting in. The three-way
  WIP/quarantine/all-gated distinction is *already computed* by `core/candidate_gate.py::_idle_reason`
  (`:149-181`) — and is reachable only as a prose message from the billed `poll_once` path.

A lane that silently dead-ends is the failure mode to design against.

## Decision

**A lane dead-ends when it can neither dispatch the work nor observe its completion. Dispatch is
unavailable on one mount and unverified on the other; observation works on both. Therefore the handoff
stage is built on observation, and dispatch is progressive enhancement.**

1. **Watch (load-bearing).** A read-only session projection — exposed as an action on the Fill lane
   surface, not as a new flat tool — returns
   `{state, source_present, pages_present[], index_synced, gate_eligible, gate_eligible_reason,
   restored_from, gate_outcome, next:{actor, do}}` over the nine states
   `not_started | waiting_on_client | client_wrote | rework_in_flight | submitted | merged | refused |
   blocked | swept`. **`next.actor` ∈ `you | claude | system | none` is the anti-dead-end guarantee**: every
   state names who acts next, as a contract field rather than a UI inference.
2. **Cost discipline.** 2–3 git subprocesses per suggestion, so: called only for the **expanded/active**
   item, at **3 s**, and only while that item's stage is active (reusing `CompilePanel`'s existing
   conditional-poll pattern). The Fill queue and the Home inbox **never** call it — queue rows render
   from the free `suggestions.jsonl` fields already in `wiki_status`.
3. **Dispatch, four tiers, honest labels.** A: bridge + `hostCapabilities.message` → `ui/message`,
   labelled **"Send to Claude"** (a turn happens). B: bridge + `updateModelContext` only →
   labelled **"Queue for Claude"** + "this does not start a turn" (a button labelled "Send" that only
   queues context is a lie). C: bridge, neither capability → **"Copy the instruction"**. D: HTTP mount →
   identical to C, by necessity. `sampling/createMessage` is rejected for handoff: it returns a completion
   to the view rather than putting work into the user's conversation.
4. **The slash command is a hint, not the mechanism.** The dispatched text is prose-first, naming the
   topic, the object and the session id, with `/knotica:<cmd>` as a trailing line. A host that interprets
   the slash routes on the slash; a host that does not routes on the prose. The literal text is shown at
   **every** tier, including A and B, so a user whose host silently drops the request is never stranded.
5. **Capability plumbing.** `ToolClient` gains a `hostCapabilities` object (bridge: `getHostContext()`;
   HTTP: `{}`) and capability-guarded `sendMessage()` / `updateModelContext()`. The handoff stage becomes a
   pure function of capabilities — no `mount === "bridge"` string checks inside any lane.

## Considered Options

### A. Dispatch-first: build on `ui/message`, degrade to prose

Pros: the shortest path to the intended experience; no new server surface; matches how the feature was
described. Cons: the stage's viability depends on an **unverified** host capability and is structurally
absent on the HTTP mount, so the lane dead-ends on one of two mounts by construction, and the failure is
silent — the stage cannot tell whether the client did the work. Rejected.

### B. Observation-first, dispatch as enhancement (**chosen**)

Pros: identical behaviour on both mounts; the stage self-advances at every tier including copy-paste;
`_idle_reason`'s knowledge becomes reachable without billing; `next.actor` makes a dead end a contract
violation rather than a UI oversight. Cons: requires real new server surface before the stage can ship.

### C. Render the handoff as instructional prose only (today's pattern, in five places)

Pros: zero cost; already how `LoopPane`, `SourcesPane`, `NotesPane`, `IngestPane` and `VaultPane` do it.
Cons: it is an instruction card, not a stage — it cannot advance, cannot report, and cannot terminate.
That is the exact "hole in the lane" the locked decision exists to eliminate.

### D. Expose the state via the existing `source_ingest_submit(mode="dry-run")`

Pros: it already returns `source_present`/`pages_present`/`gate_eligible` and ships today. Cons: it is a
*mutating tool's mode*, it runs `lint_vault` over the candidate worktree on every call, and modelling a
read as a mode of a write is exactly the kind of misuse-prone shape a read-only projection avoids.

## Consequences

**Positive**

- The handoff works identically on the bridge and HTTP mounts; the mount only changes the dispatch
  affordance, never whether the stage functions.
- `next.actor` gives the model and the human the same answer to "whose turn is it", from one field.
- The same capability plumbing fixes an adjacent pre-existing defect: `obsidian://` links render as bare
  `<a href>` unconditionally (`obsidianLinks.ts:106-130`) and are inert under the bridge's sandbox; the
  spec's path is `ui/open-link` gated on `hostCapabilities.openLinks`.
- The design survives Claude Desktop advertising no capabilities at all.

**Negative**

- New read-only server surface is a prerequisite; the stage cannot ship as a UI-only change.
- Nine states is a large contract for one stage, and each predicate costs git reads — the conditional
  polling rule is load-bearing and easy to violate later by adding the projection to a queue view.
- **Fill has no `/knotica:*` command to hand off to.** `/knotica:ingest` is Learn's protocol; Fill's
  ingest is candidate-session-scoped. Either that command grows a session mode or a Fill command is
  added — the hardest external dependency in the lane redesign.

## Disconfirmation

**Falsifier.** An empirical probe showing Claude Desktop advertises `hostCapabilities.message`, combined
with the HTTP mount turning out not to matter in practice (nobody runs `knotica mcp --http`). If both
hold, the observation layer is expensive scaffolding around a dispatch that simply works, and option A
was right.

**Steelmanned runner-up.** Option A ships in a fraction of the effort, requires no server change at all,
and the "silent dead end" objection is partly answerable in the UI: after dispatching, the stage could
simply poll `wiki_status`'s existing `approved_awaiting_ingest` / `refused_awaiting_rework` counts and
infer progress coarsely. That is a worse readout, but it is free, and coarse-but-free may be the right
trade for a single-user tool — especially since the expensive per-suggestion projection is the one part
of this design with an ongoing runtime cost.

**Reversal trigger.** Revisit if (a) an empirical probe confirms `ui/message` on every host in use *and*
the HTTP mount is retired, (b) the 3 s conditional poll shows up as a real cost on a normal-sized vault,
or (c) a second lane needs a handoff whose state is *not* derivable from git — which would mean the
observation-first premise does not generalize and each handoff needs its own answer.
