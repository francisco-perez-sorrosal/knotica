---
id: dec-056
title: Notes overlay adds no fifth read_protocol operation
status: accepted
category: architectural
date: 2026-07-29
summary: Note capture is carried by the tool description plus wiki-maintenance routing judgment, not by a new vault-resident operation prompt.
tags: [protocols, prompts, notes, interface-design, vault-migration]
made_by: agent
agent_type: interface-designer
branch: main
pipeline_tier: standard
dissent: Operation prompts are the DSPy/SIA-evolvable substrate and the single source of truth for client-as-brain procedure; keeping note guidance out of the vault puts it in a skill and a description string that neither loop can evolve.
affected_files:
  - src/knotica/core/prompts.py
  - src/knotica/mcp_server/tools_guide.py
  - skills/wiki-maintenance/SKILL.md
  - vault-template/.knotica/prompts
---

## Context

`read_protocol(operation, topic)` serves exactly four operations -- `ingest`, `query`, `lint`,
`curate` -- whose step-by-step protocols live in the vault at `.knotica/prompts/<op>.md` (root
defaults, earned topic overrides). Those files are simultaneously the MCP-prompt UX surface and
the DSPy/SIA-evolvable substrate. The project's convention is that the protocol lives in the
vault and nothing restates its steps -- `server.py`'s instructions string deliberately points at
`read_protocol` rather than enumerating steps, to avoid a second, drift-prone copy.

Adding a fifth operation is a six-site coupled change: the `OPERATIONS` tuple in
`core/prompts.py`, the vault template's new root-default prompt file, the hand-mirrored `Literal`
on `read_protocol`, an `@mcp.prompt` registration, `wiki-maintenance`'s operation table, and the
`knotica prompt` CLI branch. It also carries a **vault migration**: every existing vault raises
`NOT_CONFIGURED` for the new operation until the prompt file exists.

The notes overlay is a plausible candidate for a fifth operation (`note` / `annotate`), since
capture is a client-as-brain act.

## Decision

**No fifth `read_protocol` operation.** Note capture is carried by:

1. the `note_capture` tool description -- which is the executable interface the model re-reads on
   every call, and
2. one new symptom bullet plus one new judgment section in `skills/wiki-maintenance/SKILL.md`.

`OPERATIONS` stays a 4-tuple. No vault migration. No new prompt file.

Where notes-related guidance belongs in the evolvable substrate, the recommended follow-on is an
additive paragraph inside the existing `query.md` prompt (offer to note a reflection at the end
of an answer) -- not a new operation.

## Considered Options

### A. No fifth operation; tool description + skill carry it (chosen)

- **Pro:** no vault migration; existing vaults keep working unchanged.
- **Pro:** no protocol round trip between the user's thought and the note, on the one act where
  friction determines adoption.
- **Pro:** avoids a prompt whose body would restate the tool description -- exactly the
  second-copy drift the codebase's instructions string is written to prevent.
- **Con:** notes guidance sits outside the artifact set DSPy and SIA evolve.

### B. Add a fifth `note` operation with a vault prompt

- **Pro:** consistent with "every client-as-brain capability is a protocol"; puts notes in the
  evolvable substrate; gives the MCP-prompt surface a notes entry.
- **Con:** a protocol load is a round trip inserted at the moment friction is fatal.
- **Con:** the prompt body would be a paraphrase of the tool description -- two sources of truth
  for one deterministic call.
- **Con:** vault migration for every existing vault, to enable a capability that needs no
  vault-resident text.
- **Con:** notes are never scored, so a `note` prompt sits in the optimizable substrate with no
  metric to optimize against -- permanently inert.

### C. Extend an existing operation prompt (`query.md`) with a notes paragraph

- **Pro:** additive, no migration, no new operation, and it lands the offer exactly where the
  flywheel offer already lives.
- **Pro:** keeps notes guidance inside the evolvable substrate against a metric that actually
  exists (query quality).
- **Con:** does not address capture that starts outside a query.
- **Status:** adopted as the recommended follow-on, not as the primary decision.

## Consequences

**Positive**

- Zero migration risk; the feature ships without touching any existing vault's `.knotica/`.
- Capture stays one call from thought to durable note.
- One source of truth for how to call `note_capture`: its description.
- The `OPERATIONS` tuple keeps meaning "multi-step protocol over the KB", a coherent category.

**Negative**

- Notes routing judgment lives in a skill file, so it evolves only when a human edits it -- SIA
  can revise the skill (the skill says so itself) but there is no metric driving that revision.
- If notes later grow a genuinely multi-step flow (a guided review sweep, say), this decision
  will need revisiting rather than extending.
- The `knotica prompt` CLI and the MCP-prompt surface have no notes entry, so a user browsing
  either will not discover the feature there.

## Disconfirmation

**Falsifier.** The client model calls `note_capture` incorrectly in ways a protocol would have
prevented -- paraphrasing the user's words, fabricating a `quote` it did not display, or claiming
`pages` it did not synthesize from. If description-level guidance measurably fails to hold those
three invariants, the substrate that can be optimized against a metric is the right home and this
decision was wrong.

**Steelmanned runner-up (option B, a fifth operation).** The vault-resident prompt is not just
documentation -- it is the project's entire thesis about where behavior lives and how it
improves. Every capability kept out of it is a capability the system can never learn to do
better. The migration cost is a one-time `knotica migrate` that already exists for exactly this
class of change, and the "round trip at capture time" objection assumes the protocol must be
loaded per capture -- a client that has already loaded it in-session pays nothing. Meanwhile the
"nothing to optimize" argument is weaker than it looks: notes are not scored, but note *quality*
is measurable through a proxy the feature already produces -- how many captured notes survive
human review at promotion. That is a real gradient, and option A forecloses ever using it.

**Reversal trigger.** Revisit if (a) note capture grows into a genuine multi-step flow, (b)
promotion-survival data becomes available and someone wants to optimize capture against it, or
(c) a fifth operation is added for an unrelated reason -- at which point the migration cost is
already being paid and notes should be re-evaluated as a cheap rider rather than a standalone
justification.
