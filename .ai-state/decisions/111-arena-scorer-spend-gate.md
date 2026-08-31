---
id: dec-111
title: Switching the arena onto the eval-backed scorer is a two-phase confirm, switching back is not
status: accepted
category: behavioral
date: 2026-08-30
summary: loop action=cadence arena_scorer=eval routes through the same confirm-nonce envelope run_eval uses, because the switch commits to strictly more autonomous spend than the single eval that envelope already gates; arena_scorer=heuristic stays a one-call write
tags: [loop, arena, spend, confirm, mcp, cadence]
made_by: agent
agent_type: implementer
branch: main
pipeline_tier: standard
dissent: A config key that merely *enables* future spend is not itself a billed action, and gating it makes the dashboard's one-click scorer switch a two-round-trip flow for a setting the operator can already see.
affected_files:
  - src/knotica/mcp_server/tools_vault.py
  - src/knotica/mcp_server/tools_dispatch_loop.py
  - dashboard/src/lanes/improve/ArenaScorerSwitch.tsx
---

# Switching the arena onto the eval-backed scorer is a two-phase confirm, switching back is not

## Context

`loop_cadence_config` states the stakes plainly: the eval-backed arena scorer
bills a full golden-set eval **per variant** — a 4-variant race over a
21-question set is 84 worker+judge pairs — which is why it is a config choice
rather than a silent upgrade.

But the write path added with the config key took no `confirm` and minted no
nonce, while its sibling in the same dispatcher, `loop action=run_eval`, is a
two-phase confirm envelope for **one** billed eval. So the cheaper operation was
gated and the more expensive one was not: a single call to
`loop action=cadence arena_scorer=eval` commits every future gate-failure race —
races that fire autonomously from the loop daemon with no human present — to
N golden-set evals apiece.

A model driving the lane dispatcher can make that call in one turn.

## Decision

`arena_scorer="eval"` — **the value, not the parameter** — routes through the
same `confirm_nonce` seam `run_eval` uses, under its own nonce kind
(`arena-scorer`). Phase 1 returns a preview naming the per-race cost and writes
nothing; phase 2 (the matching, unexpired nonce) applies the write.

Three deliberate scope limits:

- **Switching back to `heuristic` needs no confirm.** The gate is on the spend,
  not on the parameter. Leaving the billed scorer is free and must stay one call.
- **The gate covers the whole write.** When `arena_scorer=eval` rides along with
  other `[loop]` keys, phase 1 writes none of them — one call is one operation.
- **Nothing else on the cadence rail is gated.** Interval, window, and thread
  count spend nothing by themselves.

## Considered Options

### Confirm envelope on the `eval` value (chosen)

Reuses the existing seam and matches the surface's established grammar for
billed decisions. Costs one extra round-trip on the rarer, more expensive
direction only.

### Leave it ungated, document the cost in the tool description

Cheapest, and the description does already carry the warning. But the surface's
own two-phase convention exists precisely because a description is not a gate,
and this operation authorizes more spend than the one already gated by it.

### Gate at race time instead of at switch time

Honest about *when* money is spent, but the race runs unattended — there is no
human in the loop to confirm, so this collapses into either blocking the
autonomous heal or not gating at all.

## Consequences

Positive: no unattended surprise bill; the surface's spend grammar becomes
consistent (the expensive switch is at least as gated as the cheap eval); the
preview names the concrete per-race cost.

Negative: the dashboard's `ArenaScorerSwitch` control now needs the two-phase
round-trip for the `eval` direction — a client sending one call gets a preview
envelope and no write. Any doc describing the switch as a single click is stale
for that direction.

## Disconfirmation

**Falsifier** — operators routinely toggling `heuristic` → `eval` → `heuristic`
during ordinary tuning, where the confirm reads as friction on a reversible
setting rather than as a spend gate.

**Steelmanned runner-up** — leaving it ungated: the key enables *future*
conditional spend rather than billing anything now, and the true spend event
(the race) is already visible in metrics and heartbeat. If races were rare and
always human-triggered, the description warning would suffice.

**Reversal trigger** — if the arena's races become explicitly human-triggered
(no autonomous firing from the daemon), the "no human present" premise is gone
and this gate should be revisited.
