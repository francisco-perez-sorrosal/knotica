---
id: dec-050
title: Remove the 26 deprecated flat-tool aliases — the migration-window premise never held
status: accepted
category: architectural
date: 2026-07-22
summary: Partial supersession of dec-045 — the two-tier dispatcher topology (Option A, one server, 7 dispatchers) stands unchanged; only the "additive-alias, non-breaking migration for one release cycle" clause is superseded, because knotica has zero external MCP consumers and the gradual-migration premise never applied.
tags: [mcp, tool-surface, server-topology, consolidation, deprecation]
made_by: agent
agent_type: implementer
branch: worktree-eval-cadence-model-config
pipeline_tier: standard
supersedes: dec-045
affected_files:
  - src/knotica/mcp_server/server.py
  - src/knotica/mcp_server/tools_vault.py
  - src/knotica/mcp_server/tools_scoreboard.py
  - src/knotica/mcp_server/tools_compile.py
  - src/knotica/mcp_server/tools_datasets.py
  - src/knotica/mcp_server/tools_arena.py
  - src/knotica/mcp_server/tools_golden.py
  - src/knotica/mcp_server/dispatch_telemetry.py
  - docs/architecture.md
  - CLAUDE.md
dissent: "Deleting the aliases outright forecloses cheap reversal if a future external consumer does appear; a slower deprecation cycle (warn-then-remove) would have cost nothing extra for a single-consumer project and preserved that option."
---

## Context

`dec-045` adopted the two-tier MCP tool-surface topology (7 action dispatchers +
18-core conversational tools + 4 stragglers + `open_dashboard`, one server) and, as
its fifth ruling, mandated an **additive-alias, non-breaking migration**: each of the
26 consolidated operator tools stayed reachable under both its old flat name
(deprecated, aliased, logged via `dispatch_telemetry.DEPRECATED_ALIASES`) and its new
dispatcher call shape, "for one release cycle" — explicitly to protect gradual
migration for external clients that might still be calling the old names.

That premise does not hold. knotica has exactly two MCP consumers, Claude Code and
Claude Desktop, both configured and operated by the project's single user
(`fperezsorrosal`). There is no external client population to migrate gradually —
every caller of the MCP server is under direct control and can be repointed at the
dispatcher call shape in the same change that removes the alias. Carrying 26 extra
tool registrations (and their per-call `deprecation_suffix`/`record_deprecated_alias`
plumbing) purely to protect a migration window that serves no actual consumer is
dead weight against the exact routing-reliability concern `dec-045` and its parent
`dec-003` were both written to protect: every additional registered tool schema is
reasoned over by the model on every conversational turn, regardless of whether
anything still calls it by its old name.

This ADR was raised mid-pipeline, after `dec-045`'s originating pipeline had already
landed and a later, unrelated task (`eval-cadence-model-config`) was in flight — the
user inserted this as a deliberate scope addition once the "who actually needs the
aliases" question was asked directly and answered "no one."

## Decision

**Remove all 26 deprecated flat-tool aliases now, in both code and docs.** The
two-tier dispatcher topology itself is unchanged and re-affirmed: one server, 7
action dispatchers (`loop`, `branches`, `compile`, `datasets`, `arena`, `golden`,
`vault_health`), 18-core conversational tools, 4 stragglers, `open_dashboard`. Only
`dec-045`'s fifth ruling — "migration is additive-alias and non-breaking" — is
superseded; every other ruling in `dec-045` (server topology Option A, Option B
deferred, Option C rejected, ADR-form-as-re-affirmation-of-dec-003) stands as
written.

Concretely: the `@mcp.tool(name="...")` registrations for the 26 aliases are deleted
from `tools_vault.py`, `tools_scoreboard.py`, `tools_compile.py`, `tools_datasets.py`,
`tools_arena.py`, and `tools_golden.py`; those modules now hold only the
`_*_payload`/helper functions the dispatchers import directly. `server.py` no longer
calls the six now-empty `register_*_tools` functions (removed alongside their
callers, since a registration function with nothing left to register is dead code,
not a preserved seam). `dispatch_telemetry.py` drops `DEPRECATED_ALIASES`,
`deprecation_suffix`, and `record_deprecated_alias` — the two remaining telemetry
signals (`record_dispatch`, `record_rejected_action`) are unaffected, since they
never depended on the alias map. Tool count: 56 → 30 (18 core + 4 stragglers +
`open_dashboard` + 7 dispatchers — exactly `dec-045`'s "projected post-alias-removal"
figure).

## Considered Options

### Option 1 — Remove all 26 aliases immediately (chosen)
Matches the actual consumer population (zero external clients) exactly. Zero added
process for a project where the deprecation window was never protecting anyone.
Cost: forecloses free reversal if an external consumer later appears (see
Disconfirmation).

### Option 2 — Keep the migration window, let it lapse "naturally" at the next release
Preserves `dec-045`'s literal one-release-cycle promise. Rejected: knotica has no
release-cycle cadence that means anything to an external consumer that doesn't
exist; waiting adds schema weight with no offsetting benefit, for however long
"the next release" nominally takes.

### Option 3 — Warn instead of remove (mark aliases deprecated in logs only, keep working)
Splits the difference: no reversal cost, but the routing-reliability tax the
consolidation was designed to remove — the always-loaded schema size the model
reasons over every turn — keeps being paid indefinitely with no clear end
condition. Rejected: with zero real consumers, "warn but keep working" never
converges to "removed."

## Consequences

**Positive:** tool count drops from 56 to 30, matching `dec-045`'s original
consolidation target rather than the inflated migration-window count; the
`deprecation_suffix`/`record_deprecated_alias` telemetry plumbing (built solely to
support the migration window) is deleted as dead code, not left as unused scaffolding;
`docs/architecture.md` and `CLAUDE.md` no longer describe a migration window that was
never load-bearing for this project's actual consumer population.

**Negative:** if a future external MCP client ever needs to call knotica (a
deliberate change from today's "self-operated, two Claude-family clients only"
assumption), it will hit a breaking change at the old tool names with no
deprecation runway — a fresh migration would have to be designed from scratch rather
than falling out of an already-built alias layer.

## Disconfirmation

- **Falsifier:** if knotica ever gains a third-party MCP consumer that was calling
  one of the 26 removed alias names, this decision was premature — the alias layer
  should have been kept (or its removal timed to that consumer's migration, not the
  project's own convenience).
- **Steelmanned runner-up:** Option 3 (warn-only, indefinite) costs nothing today and
  preserves optionality; it loses only because knotica's consumer population is known
  and closed right now, not merely assumed closed.
- **Reversal trigger:** knotica gaining any MCP consumer outside `fperezsorrosal`'s
  own Claude Code / Claude Desktop configuration reopens this decision — at that
  point, a new alias layer (or `dec-045`'s deferred Option B lazy-catalog meta-tool)
  should be designed against that consumer's actual needs rather than reflexively
  restoring the removed 26.

## Prior Decision

Partially supersedes **dec-045**. `dec-045`'s server-topology ruling (Option A, one
server, 7 dispatchers), its deferral of Option B, its rejection of Option C, and its
ADR-form ruling (re-affirmation-with-narrowed-universality of `dec-003`) all remain
correct and unchanged — this ADR does not revisit them. Only `dec-045`'s fifth
ruling, "migration is additive-alias and non-breaking… for one release cycle," is
superseded: its premise (external clients requiring gradual migration) never applied
to a single-consumer, self-operated project, so the aliases it introduced are removed
outright rather than left to lapse.
