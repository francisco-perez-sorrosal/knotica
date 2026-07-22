---
id: dec-039
title: Conversational-routing artifact separation — four layers, one job each, no protocol duplication
status: accepted
category: architectural
date: 2026-07-21
summary: Fix the client-side redirection layer (skill + MCP instructions + tool descriptions) as three routing artifacts that answer WHEN/WHETHER to enter an operation, kept strictly disjoint from the vault operation prompts that own HOW — preserving DSPy/SIA evolvability.
tags: [routing, transparency, client-as-brain, skill, mcp-instructions, evolvability]
made_by: agent
agent_type: interface-designer
branch: worktree-loop-consolidation
pipeline_tier: full
affected_files:
  - skills/wiki-maintenance/SKILL.md
  - src/knotica/mcp_server/server.py
  - vault-template/.knotica/prompts/
dissent: "Broadening the skill's detection triggers to fire on natural topic-relevant conversation risks over-routing — pulling ordinary chat into wiki operations the user did not want — which is more corrosive to trust than under-routing."
---

## Context

The transparency north star: the user converses naturally — "what do we know
about X?", "I just read this paper…", "the wiki was wrong about Y" — *without
naming knotica*, and the client autonomously routes into query / ingest / gap
report / suggestion review. Detection must stay client-side (client-as-brain
invariant; the stateless server hosts no cognition).

Four artifacts already touch routing, and their responsibilities overlap
loosely:

1. **Skill `wiki-maintenance`** — its `description:` is the primary router in
   skill-aware clients, but it triggers on "*working the knotica llm-wiki*",
   i.e. when the user already frames the task as wiki work. The north star needs
   it to fire on the *symptoms* of wiki-relevant conversation, not on the user
   naming the wiki.
2. **MCP server `instructions`** — the always-on nudge; the only router on
   clients without skills (Claude Desktop). Currently carries the multi-step
   protocol nudge but not detection heuristics.
3. **Per-tool `description`** — the local precondition surface (exemplary in
   `gap_report`: "call this ONLY when…").
4. **Vault operation prompts** (`.knotica/prompts/`) — the canonical HOW, and the
   **DSPy/SIA-evolvable substrate**. Any routing artifact that restates protocol
   steps creates a second source of truth that drifts and that the optimizers
   cannot see.

Without an explicit division, broadening detection risks the skill and
instructions absorbing protocol content (breaking evolvability) or contradicting
the prompts.

## Decision

Fix a **four-layer separation with one job each**, and one hard boundary:

| Layer | Job | Speaks about |
|-------|-----|--------------|
| Skill `wiki-maintenance` | Detection + judgment | WHEN/WHETHER + which operation + why the conventions exist |
| MCP `instructions` | Skill-less fallback router | WHEN/WHETHER (terse), for Desktop |
| Tool `description` | Local precondition | Whether *this* call is appropriate now |
| Vault prompts | Canonical protocol | HOW, step by step (evolvable) |

**Hard boundary:** routing artifacts (skill, instructions, tool descriptions)
speak only about *when/whether* to enter an operation; vault prompts speak only
about *how* to perform it. Routing artifacts **never** restate protocol steps —
they point at `read_protocol`/the vault prompt. This keeps the prompts the single
DSPy/SIA-optimizable source of truth.

**Detection mechanism (cheap scope-check, not server cognition):** the skill and
instructions teach a client-side heuristic — on a factual question or a shared
source whose topic is unclear, make the cheap read-only `wiki_status(view=scope)`
(or `list_topics`) call to learn which topics the vault covers, then decide
in-scope → route to `query`/ingest, out-of-scope → answer normally. Detection is
the client reasoning over a cheap deterministic lookup; the server stays dumb.

**Over-routing guard:** detection routes to *read/offer*, never to silent
mutation. A detected question → `query` (read-only) and, if it fails, *offer*
`gap_report`; a detected source → *offer* to ingest. The user's confirmation
gates every commit (consistent with the human-decision-ergonomics design).

## Considered Options

### Option A — Four-layer separation with the cheap scope-check (chosen)
Preserves evolvability (prompts untouched as substrate), works cross-client
(instructions cover Desktop), and grounds detection in a deterministic lookup
rather than the model guessing vault contents.

### Option B — Put detection in tool descriptions alone
Rejected: descriptions are per-tool and can't express "is this conversation
in-scope at all"; and the model only sees a tool's description once it's already
considering that tool.

### Option C — A dedicated `wiki_detect(utterance)` classifier tool
Rejected: pushes cognition toward a tool boundary and tempts a server-side
classifier — a client-as-brain violation. The model *is* the classifier; it only
needs cheap scope facts.

## Consequences

**Positive:** the north star gets a concrete, testable routing path; the
evolvable substrate is protected by contract; Desktop degrades gracefully via
instructions rather than losing routing entirely.

**Negative:** broadening detection triggers risks over-routing (mitigated by the
read/offer-only guard and confirmation gates). Routing reliability remains
client-dependent — skill-aware clients route best; Desktop relies on instructions
(surfaced as an architecture challenge for the systems-architect).

## Disconfirmation

- **Falsifier:** if users report the assistant hijacking ordinary conversation
  into wiki operations, the detection triggers are too broad and must tighten.
- **Steelmanned runner-up:** keep the skill narrow (fire only when the user names
  wiki work) — zero over-routing risk, at the cost of the hands-free north star.
- **Reversal trigger:** if evolvability pressure ever forces protocol detail into
  the skill/instructions, revisit whether the prompt/skill split still holds.
