---
id: dec-draft-c5032c8e
title: Conversational routing & transparency — per-client reliability tiers, invariants-only server instructions, proactive topic-awareness seed
status: proposed
category: architectural
date: 2026-07-21
summary: Architect ruling on Challenge 2 — accept per-client routing-reliability tiers as an inherent bounded constraint; slim server _INSTRUCTIONS to detection heuristics + stable invariant guards + a read_protocol pointer (no enumerated evolvable steps), killing the drifted duplicate at the root; add a SessionStart topic-awareness seed as the proactive half of detection; keep the read/offer over-routing guard load-bearing.
tags: [routing, transparency, client-as-brain, mcp-instructions, sessionstart, evolvability, single-source-of-truth]
made_by: agent
agent_type: systems-architect
branch: worktree-loop-consolidation
pipeline_tier: full
affected_files:
  - src/knotica/mcp_server/server.py
  - skills/wiki-maintenance/SKILL.md
  - hooks/session_start.sh
  - src/knotica/mcp_server/tools_status.py
dissent: "A SessionStart topic-seed and broadened skill triggers only reliably help skill-aware clients (Claude Code); on Desktop the north star still rests on a static instructions paragraph, so the transparency goal is structurally uneven and may over-promise on the client the guidance most wants to serve."
---

## Context

The transparency north star: the user converses naturally ("what do we know about X?",
"I just read this paper…", "the wiki was wrong about Y") *without naming knotica*, and
the client autonomously routes into query / ingest / gap-report / suggestion-review.
Detection must stay client-side — the stateless server hosts no cognition (locked
invariant).

The interface-designer raised **Architecture Challenge 2**: the *primary* detection
artifact is the skill `description`, which fires only in skill-aware clients (Claude
Code). Claude Desktop is an explicit target (`docs/CLAUDE_DESKTOP.md`), has **no skills**,
and does not surface MCP prompts — so on Desktop, detection rests entirely on MCP
`instructions` + tool descriptions. The interface-designer proposed enriching
`instructions` with detection heuristics + a cheap `wiki_status(view=scope)` scope-check,
and documenting per-client reliability tiers as an accepted constraint
(`dec-draft-d6edd5ef`). Research additionally found `server.py` `_INSTRUCTIONS` has
**empirically drifted** from the vault `ingest.md` (the P4 71-line suggestion-ingest
addition never propagated), and that detection is *circular* — the client must already
suspect knotica is relevant to think to call the tool that would confirm it.

## Decision

**Adopt the interface-designer's routing separation (`dec-draft-d6edd5ef`)**, with these
architect rulings:

1. **Accept per-client routing-reliability tiers as an inherent, bounded constraint.**
   Tier-1 (Claude Code, skill-aware + plugin hooks): best routing. Tier-2 (Desktop,
   skill-less, no hooks, no MCP-prompt UI): degraded, instruction-only routing. The
   tension cannot be closed at the interface layer because the invariant forbids
   server-side detection — so it is acknowledged and bounded, not pretended away.
2. **Slim `server.py` `_INSTRUCTIONS` to invariants + detection + a pointer — no
   enumerated protocol steps.** It carries (a) the detection heuristic ("when the
   conversation concerns a covered topic / a shared source / a reported wiki error, route
   to knotica; call `wiki_status(view=scope)` to learn coverage"), (b) the stable
   invariant guards that must survive even when the client never calls `read_protocol`
   (store the source's FULL text faithfully; topic is always explicit; the tools are
   deterministic, *you* do the cognition), and (c) a pointer to `read_protocol` for the
   step sequences. It **removes** the enumerated ingest sequence. Test for what stays:
   *stable invariant* stays; *evolvable protocol step* leaves. This is a root-cause fix —
   the drift-prone content leaves entirely rather than being kept in sync — and it
   preserves the no-vault-read-at-boot property (pointer-only, **not**
   generate-from-vault). It also enforces the routing/protocol boundary
   (`dec-draft-d6edd5ef`): routing artifacts speak *when/whether*, prompts own *how*.
3. **Add a SessionStart topic-awareness seed (Tier-1, in scope).** Extend
   `hooks/session_start.sh` to seed "this vault covers topics [X, Y, Z]" into session
   context (mirroring the existing config nudge). This is the **proactive** half of
   detection that the interface-designer's *reactive* scope-check does not cover — it
   breaks the circular discoverability. The SERVER stays stateless (the hook is a
   client-side plugin mechanism reading via a deterministic tool/CLI, resolved per call).
   Claude-Code-only (Desktop runs no plugin hooks) → this reinforces, does not close, the
   tier split — consistent with (1).
4. **Adopt `wiki_status(view=scope)`** — the cheapest new view `{schema_version,
   vault_name, topics[], totals}` — folding the routing scope-check into an existing tool
   (no new tool), deterministic and stateless; the model remains the classifier over a
   cheap lookup (client-as-brain preserved).
5. **The read/offer over-routing guard is load-bearing.** Detection routes to
   *read/offer* only — a detected question runs `query` (read-only) and, on miss, *offers*
   `gap_report`; a detected source *offers* ingest. No detection path mutates silently;
   every commit stays user-gated. This is the trust-preservation mechanism for the
   over-routing risk (the dominant failure mode).
6. **No separate Desktop onboarding artifact.** The enriched `instructions` ARE the
   Desktop routing surface; a paste-able Desktop system-prompt naming topics would drift
   at config time and is redundant with the generic heuristic + the live scope-check.

## Considered Options

### Option A — Tiers + invariants-only instructions + proactive seed (chosen)
Fixes drift at the root, adds the proactive detection half for Tier-1, degrades Desktop
gracefully, keeps prompts the single evolvable source of truth.

### Option B — Generate the ingest reminder from `core.prompts.get_prompt("ingest")` at boot
Rejected: reintroduces a vault read at server construction (currently explicitly avoided)
and keeps enumerated protocol text on the routing surface — treating the symptom (sync the
duplicate) instead of the cause (there should be no duplicate).

### Option C — Keep instructions as-is, rely on the skill
Rejected: the skill fires only in skill-aware clients; Desktop loses routing entirely and
the drift persists.

## Consequences

**Positive:** the north star gets a concrete, testable routing path; the drift surface is
eliminated (no protocol steps left to drift); the evolvable substrate is protected by the
routing/protocol boundary; Desktop degrades gracefully; the proactive seed makes
Tier-1 detection non-circular.

**Negative:** transparency is structurally uneven across clients (Tier-2 weaker) — an
inherent constraint, not a defect to fix here; broadened detection triggers risk
over-routing (mitigated by the read/offer guard). The `_INSTRUCTIONS` slim removes the
always-on enumerated ingest reminder for clients that never call `read_protocol` — only
the *steps* leave; the load-bearing *invariants* stay.

## Disconfirmation

- **Falsifier:** if users report the assistant hijacking ordinary conversation into wiki
  operations, the detection triggers are too broad and must tighten; if Tier-2 (Desktop)
  routing measurably fails to fire on in-scope conversation, instructions-only detection
  is insufficient and a Desktop-specific mechanism must be reconsidered.
- **Steelmanned runner-up:** keep the skill narrow (fire only when the user names wiki
  work) and leave `_INSTRUCTIONS` verbose — zero over-routing risk and a self-contained
  skill-less client, at the cost of the hands-free north star and with the drift accepted.
- **Reversal trigger:** if evolvability pressure ever forces protocol detail back into
  the skill/instructions, or a Desktop client capability (skills / hooks / prompt UI)
  lands, revisit the tier split and the instructions scope.
