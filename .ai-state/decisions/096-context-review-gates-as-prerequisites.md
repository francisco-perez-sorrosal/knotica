---
id: dec-096
title: Normalize and gate the description corpus before renaming it, and amend the adapter seam to cover prose
status: accepted
category: implementation
date: 2026-08-10
summary: "The context review's audit never reached the architecture, so two of its recommendations are folded into the plan as prerequisites — a backtick-normalization pass that makes ten ordinary-English tool names mechanically findable, and a referential-integrity gate over description and fix= prose — and the architecture's non-goal that the rename stops at the adapter layer is amended to hold for identifiers but not for prose, because ten of the twelve files carrying stale-able fix= strings are under core/."
tags: [swimlanes, rename, gates, make-verify, context-artifacts, description-prose, prerequisites]
made_by: agent
agent_type: implementation-planner
branch: worktree-process-swimlanes
pipeline_tier: full
affected_files:
  - scripts/check_surface_consistency.py
  - Makefile
  - docs/reference.md
  - commands/setup.md
  - hooks/session_start.sh
  - skills/wiki-maintenance/SKILL.md
  - src/knotica/core/
affected_reqs: [REQ-10, REQ-10b, REQ-25, REQ-26]
dissent: "Two new gate checks and a corpus-wide normalization pass are three extra prerequisites bolted onto a milestone that already carries six, on the strength of one three-week-old residue in one command file — and a gate that fires on backticked identifiers inside prose is exactly the kind of check that gets disabled the first time it blocks a legitimate sentence, at which point the project has paid for it twice and holds neither the gate nor the discipline."
---

## Context

`CONTEXT_REVIEW.md` was written in architecture-stage shadow mode, in parallel with the
systems-architect. `SYSTEMS_PLAN.md` cites the interface-designer throughout and the context review
nowhere; the two never reconciled. That gap already produced one defect that reached the
recommended design — the `Repair` lane name colliding with the published `vault_health
action=repair`, caught only at the orchestrator's checkpoint. Its remaining findings arrive at
planning as un-triaged input.

Three of them bear on how the rename can be executed at all:

1. **Ten of thirty-five tool names are ordinary English words** — `arena`, `branches`, `compile`,
   `datasets`, `golden`, `loop`, `notes`, `query`, `search`, `vault`. Across the doc corpus the
   raw-to-backticked ratio is about 2:1, so roughly half of all apparent references are prose and
   no regex separates them. `docs/configuration.md` is the extreme: 91 raw, 40 backticked, all 40
   ambiguous.
2. **The last rename of exactly this kind already produced exactly this defect class.**
   `commands/setup.md:33` instructs the user to use MCP `compile_run`, a tool consolidated away by
   `dec-045`/`dec-050` in July. It has been wrong for about three weeks and nothing surfaced it.
   Verified live in this pass.
3. **The adapter seam leaks.** Seventeen `fix=` remediation strings name a published tool name,
   and ten of the twelve files carrying them are under `core/`. `fix=` is model-facing by design —
   it is the envelope's remediation field, written to be acted on — so `core/` emits
   published-surface vocabulary directly to the client LLM.

The rename ahead is roughly ten times larger than the one whose residue is still in the tree.

## Decision

Three amendments to the architecture's prerequisites, all landing before any name changes:

- **P7 — backtick-normalize first.** A one-time, semantics-free pass putting every tool, CLI and
  slash name mention inside a backtick span across `docs/`, the seven-file `CLAUDE.md` tree,
  `skills/wiki-maintenance/SKILL.md` and `commands/` bodies. This is already the house style in
  `src/knotica/mcp_server/CLAUDE.md` (22 of 27 backticked, and correspondingly the most tractable
  artifact in the tree). It converts the ambiguous ten from un-greppable to mechanically findable,
  permanently, and it is the precondition that makes the gate below meaningful.
- **P8 — a referential-integrity gate.** Every backticked identifier of tool-name shape inside an
  MCP `description=` or `fix=` literal must resolve to a live registration. Widened beyond the
  review's recommendation to cover the two silent-failure surfaces that would otherwise still be
  ungated: `hooks/session_start.sh`'s CLI and slash references, and the tool names in
  `skills/wiki-maintenance/SKILL.md`'s always-loaded `description` frontmatter, which is an
  independently-maintained second copy of `server.py::_INSTRUCTIONS`'s routing contract.
- **The adapter-seam non-goal is amended.** `SYSTEMS_PLAN.md`'s "the rename question stops at the
  adapter layer in both directions" holds for **identifiers** — no `core/` function is renamed —
  and does **not** hold for **prose**. `fix=` and description text are swept across `core/` and
  `evals/` as part of the description-corpus step.

The same-shaped surface-consistency gate the architect already specified (P5) lands first, on the
current names, forcing `docs/reference.md`'s four stale integers and the `compile_run` residue to
be fixed as a small isolated change — so the rename lands against a green baseline rather than
trying to establish one mid-flight.

## Considered Options

### Sweep by hand during the rename

Rejected. It is an unbounded manual pass over 1 251 raw references, half of which cannot be found
by regex, with a three-week-old uncaught residue as the base rate.

### Gate without normalizing first

Rejected. A gate over backticked identifiers is only as complete as the backticking. Landing it
before P7 gates the mechanically-findable half and silently blesses the other half.

### Normalize without gating

Rejected. Normalization is a one-time state; without a gate it decays exactly as the four summary
integers in `docs/reference.md` decayed — the tables are edited by whoever adds a tool, the
summaries by whoever remembers.

### Keep the adapter-seam non-goal as written

Rejected on evidence. Ten of the twelve `fix=` files are under `core/`, so a plan scoped to
`mcp_server/` omits them silently — and a stale `fix=` is the worst failure shape available: the
repair instruction itself is wrong, so the model retries into the same wall.

## Consequences

**Positive.** The rename becomes a mostly-mechanical, gate-verified transformation rather than a
manual sweep. The gate catches C4-class defects permanently, not just during this rename, and it
is the same shape and the same fail-closed contract as `check_architecture_coverage.py` — an
argument this repository has already accepted in that script's own docstring. The two silent
surfaces (`session_start.sh`, the skill description) gain coverage they have never had.

**Negative.** Three extra prerequisites on a milestone that already carries six, and P7 produces a
large, boring diff across 36 artifacts that must be reviewed as backticks-only. The gate can be
over-reached into brittleness; the scope discipline is explicit — gate the identifiers, never the
prose quality — borrowed from the architecture-coverage script's own self-limiting paragraph.

**Neutral.** The context review's R2 (mutation gating on `create`/`migrate`/`setup`) is
deliberately excluded: pre-existing, not rename-caused, and it should not inflate this diff
silently. It is recommended for a tech-debt ledger row instead.

## Disconfirmation

**Falsifier.** If P7's normalization pass turns out to require judgement on a material fraction of
its 1 251 references — that is, if "is this the identifier or the word" is genuinely ambiguous
often enough that the diff cannot be reviewed as mechanical — then the pass is not
semantics-free, and it is a rename in disguise that should be merged into the rename itself.

**Steelmanned runner-up.** Landing only P5 and sweeping the prose by hand keeps the prerequisite
count at six and avoids a corpus-wide diff on the strength of a single observed residue. The
counter is that one observed residue is a base rate of one per rename, and this rename is ten
times larger.

**Reversal trigger.** If the referential-integrity gate blocks a legitimate sentence more than once
in normal work, narrow it to `fix=` strings only — where the failure is silently wrong rather than
merely stale — before anyone reaches for `# noqa`.
