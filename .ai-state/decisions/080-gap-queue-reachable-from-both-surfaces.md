---
id: dec-080
title: Gap queue reachable from both surfaces
status: accepted
category: architectural
date: 2026-08-06
summary: "`gaps_read` makes the P1 gap queue readable and `gapfill_discover` makes the P2 drain runnable from MCP, closing a write-only queue; the drain is the first billed *flat* tool, so the two-phase nonce mechanism is extracted from `tools_vault` into a shared `confirm_nonce` module."
tags: [gap-fill, mcp-tool-surface, dashboard, two-phase-confirm, billed-action, cold-start-boundary, discoverability]
made_by: agent
agent_type: orchestrator
branch: feat-gap-queue-visibility
pipeline_tier: standard
affected_files:
  - src/knotica/mcp_server/tools_gaps.py
  - src/knotica/mcp_server/tools_suggestions.py
  - src/knotica/mcp_server/confirm_nonce.py
  - src/knotica/mcp_server/tools_vault.py
  - dashboard/src/SourcesPane.tsx
  - tests/test_mcp_gaps_read.py
dissent: "Leaving discovery on the CLI would have kept every billed path off the web surface entirely, which is a defensible line to hold; a button that spends is a strictly larger blast radius than a command someone has to type."
---

## Context

`gap_report` wrote gaps that nothing could read back. No MCP tool exposed `gaps.jsonl`, and no
dashboard pane touched it, so a filed gap was observable only by opening the file by hand. The
Sources pane read `suggestions.jsonl` — a different queue, one stage later — and its empty state
told the reader to go freeze a golden question and regress, which is advice for someone with no
gaps, given to someone who had just filed one.

The step that moves a gap into that pane, discovery, was CLI-only. So the P1→P3 hop was unreachable
from the two surfaces a gap is actually filed and read on: a user could file a gap in conversation,
see nothing anywhere, and have no way to act on it without dropping to a terminal.

Found the way these things are usually found: by filing a real gap through Claude Desktop and then
failing to find it in the dashboard.

## Decision

Make the gap queue a first-class surface on both MCP and the dashboard.

- **`gaps_read`** — a flat, read-only tool paging `gaps.jsonl` the way `suggestions_read` pages its
  own queue. It deliberately does *not* reuse `gapfill._open_genuine_gaps`: that reader answers
  "what may a drain query for", so it drops `dilution` gaps and raises on a malformed line. A
  display surface wants every gap the queue holds, and a counted skip rather than a blackout.
- **`gapfill_discover`** — the drain, as a **billed two-phase tool**. Phase 1 previews (open gaps,
  what would drain, whether a provider is configured, the cost) and mints a single-use nonce while
  spending nothing; phase 2 consumes the nonce and runs the search. Same protocol as
  `loop action=run_eval`.
- **`confirm_nonce.py`** — the nonce mint/consume/TTL mechanism, extracted from `tools_vault`. It
  was already shared by `run_eval` and `run_once`; a third caller in a *different* module made
  reaching into another module's privates the wrong answer.
- **`tools_gaps.py`** — one module per queue. The three gap-queue tools moved out of
  `tools_suggestions` when the combined file crossed the 800-line ceiling; the ratchet forced the
  split and the honest boundary was already there.
- **The dashboard** renders open gaps above the suggestion cards, counts them in the Sources tab
  badge (from a `wiki_status` summary that already existed and nothing consumed), and replaces the
  misleading empty state.

`gapfill_discover` makes outbound **search** calls, not model calls. The client-as-brain invariant's
list of server-side-LLM callers is unchanged; what widens is the billed surface — this is the first
flat tool that spends.

## Considered Options

### Read-only visibility, leaving discovery on the CLI

Ships the whole fix for "I cannot see my gap" with no billed surface at all. Rejected as the final
state because it leaves the pipeline's first hop reachable only from a terminal, so the dashboard
can show you a problem it cannot help you act on. Kept as the *staged* first half, and shipped and
verified before the billed half was written.

### Discovery as an unconfirmed one-click action

Simplest UI. Rejected outright: it spends the user's money on a single click with no quoted cost,
and the repo already has a two-phase pattern precisely because that is unacceptable.

### A `gapfill` dispatcher instead of flat tools

Would match the operator-long-tail convention. Rejected: reading a queue and filing into it are
high-density conversational verbs, and `gap_report` and `suggestions_read` are already flat. A
dispatcher would split one queue's tools across two topologies.

### Importing the nonce helpers from `tools_vault`

No new module, and it passes the import-direction test (thin→thin is allowed). Rejected because it
imports privates across modules and quietly makes `tools_vault` a utility library for its siblings.
The extraction costs one architecture-record update, which the coverage gate demands anyway.

## Consequences

**Positive**

- A filed gap is visible from the landing pane, in conversation, and in the pane that acts on it.
- The two-phase seam is now named and shared rather than duplicated per billed action.
- `gaps_read` is deliberately more permissive than the drain's own reader, so `dilution` gaps stop
  being invisible.

**Negative**

- A button that spends money now exists in a web UI. Gated, previewed and nonce-confirmed, but the
  blast radius is larger than a CLI command.
- Phase 1 constructs the discovery service to report `provider_configured` honestly, so a preview
  pays an import it would not otherwise need.
- **A test that passes a valid `confirm` reaches the billing boundary.** `resolve_api_key` falls
  back to `./.env`, so a search key *does* resolve under pytest on a maintainer's machine — measured,
  not assumed. Tests that confirm must stub the service; this is now written into
  `mcp_server/CLAUDE.md` because the failure is silent and expensive.

## Disconfirmation

**Falsifier.** A drain that runs without a preview the user saw — an unconfirmed phase 2, a nonce
that survives one use, or a `run-eval` nonce confirming a drain — would show the gate is decorative.
All three are pinned by tests, and the third exists because both actions mint the same token shape
into the same directory.

**Steelmanned runner-up.** Read-only visibility is genuinely enough for the reported problem, and
holding the line at "no billed path on the web surface" is a coherent policy that needs no nonce
protocol, no preview accuracy, and no test discipline about stubbing. It loses only because the
dashboard would then surface a problem it cannot act on, and the user asked for the acting.

**Reversal trigger.** If a preview's `estimated_cost` is ever wrong in the expensive direction — a
drain that bills more than it quoted — the flat-tool billing surface should be withdrawn to the CLI
until the quote can be trusted, because an inaccurate quote makes the confirmation meaningless.
