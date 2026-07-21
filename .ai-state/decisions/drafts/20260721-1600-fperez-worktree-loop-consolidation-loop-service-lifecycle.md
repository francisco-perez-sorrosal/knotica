---
id: dec-draft-64a38a63
title: Loop becomes a lifecycle-managed service — supersede "no periodic daemon in MVP" under a one-click-install / zero-user-burden bar
status: proposed
category: architectural
date: 2026-07-21
summary: Per mid-flight user guidance, relax the locked "No periodic daemon in MVP" PRE_PLAN stance — the observe→gate→heal watcher becomes an automatically installed/spawned/supervised service (leading candidate an OS service manager) so the user starts no auxiliary process by hand; loop semantics, client-as-brain, and the stateless MCP server are all unchanged — only the loop's lifecycle management changes.
tags: [loop, daemon, service-lifecycle, one-click-install, pre-plan-supersession, autonomy, transparency]
made_by: agent
agent_type: systems-architect
branch: worktree-loop-consolidation
pipeline_tier: full
affected_files:
  - docs/PRE_PLAN.md
  - src/knotica/cli/loop.py
  - src/knotica/core/loop_heartbeat.py
  - hooks/session_start.sh
dissent: "A supervised background daemon is the single biggest departure from the local-first, inspect-everything MVP ethos; an always-running process that evaluates and gates the vault unattended is exactly the kind of autonomy the no-daemon stance was protecting the user from, and 'zero burden' is easy to claim and hard to guarantee across macOS/Linux service managers."
---

## Context

The transparency census found the largest remaining ceremony is that the user must
*remember to run* `knotica loop --topic <t>` — a foreground/backgrounded terminal watcher
with no daemon-on-session-start model. If the user doesn't run it, nothing observes;
"did anything happen overnight" is contingent on a terminal being left open. PRE_PLAN's
Safety-net clause locked "No periodic daemon in MVP," so the initial plan deferred
auto-start pending user sign-off.

**Mid-flight user guidance resolves this:** a daemon/service loop *is* acceptable, under a
strict bar — it must genuinely improve the loop, and it must move toward **one-click
installation with no auxiliary process the user starts manually**; the lifecycle must be
fully managed (installed / spawned / supervised automatically — e.g. by the plugin install
flow, the MCP server, or an OS service manager) with zero added operational burden. The
user asked that the supersession be captured as an ADR with the one-click-install rationale
and a disconfirmation section.

## Decision

**Supersede the "No periodic daemon in MVP" stance.** The existing `knotica loop --watch`
watcher becomes an **automatically installed / spawned / supervised service**; the user
never starts an auxiliary process by hand.

**Scope of the supersession — lifecycle only, not semantics.** This changes *who starts and
supervises* the loop. It does **not** change:
- Loop semantics — still observes the default branch, evaluates on a **clone never the live
  vault**, gates `loop/c/*`, heals prompt regressions in the arena, quarantines dilutive
  sources; **one commit per mutating op, flock-guarded**; heartbeat liveness under
  `.knotica/locks/` unchanged.
- **Client-as-brain** — the loop is a *headless* loop, for which PRE_PLAN already permits
  server-side cognition (dec-014's knotica-owned LLM trust boundary). The daemon is that
  headless loop; it does not add cognition to the *interactive* path.
- The **stateless MCP server** — the daemon is **not** the MCP server; the MCP server stays
  stateless and cold-started per session. Two distinct processes.

So the only thing superseded is the *operational* no-daemon stance.

**Lifecycle mechanism — leading candidate an OS service manager.** The implementation-
planner selects against the one-click bar; ranked:
1. **OS service manager** (launchd on macOS / systemd on Linux) registered by the plugin /
   install flow — truest to "no manual process," supports clean install/uninstall and
   supervision/restart. Leading candidate.
2. MCP-server-spawned child — weaker: the MCP server is short-lived and per-session, so a
   loop it spawns has no persistence guarantee.
3. SessionStart-spawned background — Claude-Code-only and session-bound; does not deliver
   "always observing."

**Constraints the mechanism must honor:** fully declarative install + **clean uninstall**
(no zombies — the zero-burden bar); supervision/restart on crash (the heartbeat already
models restart, softening autonomy gap #4 — the non-persisted quiet-window debounce);
watched-topic set resolved from config per supervision cycle (leading design: one supervised
process iterating all configured topics/vaults, vs one per topic — a planner sub-question);
liveness observable via the existing `wiki_status.loop.runner` heartbeat; opt-outable by not
registering the service.

**PRE_PLAN update required at implementation:** the Safety-net "No periodic daemon in MVP"
clause is rewritten to record the relaxation, the one-click-install rationale, and the
zero-burden + clean-uninstall bar.

## Considered Options

### Option A — OS-service-manager-supervised watcher, config-resolved topics (chosen)
Delivers always-observing autonomy with zero manual process and clean lifecycle; fits the
existing heartbeat/restart model and the loops-on-clone invariant unchanged.

### Option B — Keep the manual terminal watcher (status quo, prior stance)
Rejected per user guidance: the "remember to run it" ceremony is the biggest remaining
transparency gap and contradicts the one-click-install goal.

### Option C — MCP-server-spawned child loop
Rejected as primary: the stateless, per-session, cold-started MCP server is the wrong owner
for a persistent supervised process; no lifecycle guarantee and it muddies the
stateless-server story.

## Consequences

**Positive:** removes the largest manual seam; "did anything happen overnight" is always
answerable and feeds the SessionStart attention nudge (real state to surface); autonomy
gap #4 softens under supervision; advances one-click install.

**Negative:** an always-running unattended process that evaluates and gates the vault is the
biggest departure from the local-first MVP ethos (the dissent); raises vault-contention
stakes (daemon + synchronous MCP-gate + the user's Obsidian edits — the flock serializes all
three, loops run on a clone, so no *new* race, but the stakes rise); cross-platform service-
manager packaging is real work and the "zero burden" claim must be proven by a clean
install/uninstall path.

## Disconfirmation

- **Falsifier:** if the managed service proves hard to install/uninstall cleanly across
  macOS/Linux, or leaks/zombies, the "zero added operational burden" premise is false and
  the daemon should revert to an explicit opt-in start.
- **Steelmanned runner-up:** Option B — a local-first tool where the user *chooses* when the
  autonomous evaluator runs preserves inspect-everything trust and sidesteps all
  service-manager packaging risk; the ceremony it keeps is one command.
- **Reversal trigger:** unattended-gating incidents (the daemon merges/quarantines something
  the user would not have), or install-flow friction reports, should reopen this toward an
  explicit opt-in or a session-bound (non-persistent) model.

## Prior Decision

Supersedes the **PRE_PLAN "No periodic daemon in MVP"** Safety-net stance (a documented
design principle, not a `dec-NNN`). What changed: mid-flight user guidance set a
one-click-install / zero-user-burden goal that the manual-watcher model cannot meet; the
daemon is now sanctioned provided its lifecycle is fully managed and its loop semantics,
client-as-brain, and the stateless MCP server are all preserved. PRE_PLAN must be edited to
reflect this at implementation.
