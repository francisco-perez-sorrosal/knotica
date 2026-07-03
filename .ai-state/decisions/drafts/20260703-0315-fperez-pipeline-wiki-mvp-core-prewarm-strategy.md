---
id: dec-draft-75ee2605
title: uvx cold-start pre-warm strategy
status: proposed
category: configuration
date: 2026-07-03
summary: Keep both pre-warm points but replace the SessionStart cold-cache detector with an unconditional backgrounded, idempotent pre-warm; setup foreground-pre-warms; never alwaysLoad.
tags: [cold-start, uvx, prewarm, plugin, sessionstart-hook, phase-1]
made_by: agent
agent_type: systems-architect
branch: pipeline-wiki-mvp-core
pipeline_tier: standard
affected_files: [hooks/hooks.json, commands/setup.md, .mcp.json]
affected_reqs: [REQ-PLUGIN-01, REQ-PLUGIN-02, REQ-PLUGIN-03]
---

## Context

RESEARCH_FINDINGS Q2 measured the first `uvx --from ${CLAUDE_PLUGIN_ROOT}` env resolution at **24.4 s**
(network-bound), warm repeats at **0.04–0.2 s**. A cold resolution can exceed Claude Code's MCP startup
window on the very first launch after install/update, showing the server as failed until reconnect. Each
plugin update changes `${CLAUDE_PLUGIN_ROOT}` and re-pays one cold resolution; caches also evict after
~7 days. PRE_PLAN calls for pre-warming from **both** `/knotica:setup` and a SessionStart
"cold-cache check" — the orchestrator asked the architect to confirm both or simplify with reasons.

## Decision

Keep **both** pre-warm points, but **simplify the hook**: replace the "cold-cache check" with an
**unconditional, backgrounded, idempotent** pre-warm.

- **SessionStart hook:** if `uvx` is present, fire `uvx --from ${CLAUDE_PLUGIN_ROOT} knotica --version` in
  the **background** (non-blocking, fire-and-forget) every session. Warm cost is 0.04–0.2 s (negligible);
  cold cost is paid off the critical path, outside the MCP handshake. If `uvx` is absent, print uv-install
  guidance instead (the hard-prerequisite nudge). No cold-cache detector is written.
- **`/knotica:setup`:** foreground-pre-warm as part of its `uvx`-presence validation, with a visible
  progress note (the user is actively waiting during first setup).
- **`.mcp.json`:** never set `alwaysLoad` on the knotica server (its 5 s connect cap would guarantee a
  first-launch failure).

## Considered Options

### Option A — setup foreground + hook unconditional background pre-warm (chosen)
- **Pros:** covers every cold trigger (first install, update, eviction) with one trivial idempotent
  command; no fragile detector; hook stays non-blocking.
- **Cons:** a redundant fast probe each warm session (negligible).

### Option B — setup only
- **Cons:** misses plugin updates and cache eviction — the user won't re-run setup after an update, so the
  post-update cold resolution still trips the startup window.

### Option C — hook with an explicit cold-cache detector
- **Cons:** detecting "cold" reliably means either probing (which pays the cost anyway) or inspecting uv
  cache internals (brittle, uv-version-coupled). More code, more failure modes, no benefit over A.

## Consequences

- **Positive:** the top operational risk is mitigated on all cold paths with minimal, robust code;
  SessionStart never blocks; `alwaysLoad` avoided.
- **Negative:** an inexpensive extra background invocation on warm sessions; a genuinely cold session still
  pays ~24 s once, but off the handshake and usually before the user needs the server.

## Disconfirmation

- **Falsifier:** if the cold-start drill shows the backgrounded pre-warm does not complete before the user
  first invokes a tool (so the tool call itself still eats the cold resolution), the background timing
  assumption was wrong and a blocking-with-progress setup step would be needed.
- **Steelmanned runner-up (Option B/setup-only):** if plugin updates are rare and users reliably re-run
  setup, the hook's every-session probe is pure overhead and setup-only is simpler.
- **Reversal trigger:** if the drill shows the tool call still hits a cold env despite the background
  pre-warm, escalate to a foreground pre-warm gate (or a documented "first call may take ~25 s" note).
