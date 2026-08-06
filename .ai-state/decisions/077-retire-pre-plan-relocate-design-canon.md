---
id: dec-077
title: Retire PRE_PLAN as canon and move the design canon to DESIGN.md
status: accepted
category: behavioral
date: 2026-08-05
summary: "docs/PRE_PLAN.md is archived to .ai-state/design-history/; .ai-state/DESIGN.md becomes the design canon and the single site of the stateless-server scope paragraph that dec-074 placed in PRE_PLAN."
tags: [documentation, single-source-of-truth, design-canon, invariants, stateless-server, archival]
made_by: agent
agent_type: orchestrator
branch: main
pipeline_tier: standard
supersedes: dec-074
affected_files:
  - .ai-state/design-history/PRE_PLAN.md
  - .ai-state/DESIGN.md
  - docs/architecture.md
  - CLAUDE.md
  - README.md
---

## Context

`docs/PRE_PLAN.md` was the project's seed document and was declared authoritative by `CLAUDE.md`,
`README.md`, and `docs/architecture.md`. It has not been authoritative in practice for some time:

- Its repo-layout tree names an `mcp/` package (renamed `mcp_server/` by `dec-009`) and an `agent/`
  package that was never built, and omits `discovery/`, `guillotine/`, `okf/`, `service/` and
  `dashboard/` — five packages that ship.
- Its implementation-status note says the tool consolidation left "seven action-parameterized
  dispatchers". There are nine; `notes` and `vault` arrived later.
- The work it describes as Phases 0–5 is complete through Phase 4, so most of the document is a plan
  for work already done, written before the code existed to contradict it.

Meanwhile two documents grew into the roles it claimed: `.ai-state/DESIGN.md` carries the
design target, invariants and rationale, and `docs/architecture.md` carries the code-verified
developer guide — the latter gated by `scripts/check_architecture_coverage.py`, which `PRE_PLAN.md`
never was. A user-facing documentation pass found `PRE_PLAN.md` reachable from the README as
"canonical" while pointing readers at a stale package layout.

`dec-074` is the complication. It placed the stateless-server scope paragraph **once**, in
`PRE_PLAN.md`, precisely to stop that claim drifting across four sites — and had three other
documents point at it. Removing `PRE_PLAN.md` without a replacement site would re-open the exact
defect `dec-074` closed.

## Decision

`PRE_PLAN.md` is **archived, not deleted**: it moves to `.ai-state/design-history/PRE_PLAN.md` with a
banner stating that it is a historical record, naming its known-stale sections rather than correcting
them, and routing readers to the live documents.

`.ai-state/DESIGN.md` becomes the **design canon**, and its § 7 Constraints becomes the single site of
the stateless-server scope paragraph. `CLAUDE.md` and `docs/architecture.md` keep the invariant in
their own words and point there — the same pointer-not-copy shape `dec-074` chose, with a different
destination.

Citations are re-aimed by kind, not in bulk:

| Reference kind | Action | Reason |
|---|---|---|
| Markdown links (7) | Re-aimed | They break; a dangling link is a defect |
| `affected_files:` frontmatter (3 ADRs) | Re-aimed | Machine-readable; consumed by the discovery protocol |
| Body prose in finalized ADRs (~20) | **Left verbatim** | Accurate statements about where the document lived when the decision was argued; rewriting them would falsify the record |
| Frozen sentinel reports and specs | **Left verbatim** | Archives; not editable artifacts |

## Considered Options

### Delete `PRE_PLAN.md` outright

Rejected. Roughly twenty finalized ADRs argue against it *by name* — `dec-007` on which FastMCP,
`dec-016` on the eval scalar, `dec-021` on the `wiki_query` tool name, `dec-044` on the daemon stance.
Those arguments are unreadable without the text they rebut. Deleting it would make the ADR corpus
cite a document nobody can retrieve, which costs more than the file does.

### Keep it in `docs/`, demoted to a historical record

Rejected, though it was close. It leaves a 220-line document that is wrong in its particulars sitting
in the user-facing documentation tree, where the next reader must first learn that the canonical-looking
file is not canon. `.ai-state/` already means "committed project intelligence, not documentation", which
is exactly what this is.

### Correct `PRE_PLAN.md` instead of archiving it

Rejected. Corrected, it becomes a third description of the current system alongside `DESIGN.md` and
`architecture.md` — reproducing the drift this decision removes, and adding an ungated document to a
pair that `check_architecture_coverage.py` gates.

## Consequences

**Positive.** One design canon instead of two-and-a-half. The stateless-server scope keeps a single
site, so `dec-074`'s mechanism survives its host document. `docs/` holds only documentation. The
archived text stays retrievable for the ADRs that argue with it.

**Negative.** Roughly twenty finalized ADRs now name a path that no longer exists in their prose. This
is accepted deliberately: the alternative is editing finalized records to make historical statements
read as present-tense ones. The archive banner and this decision are the bridge. A reader who greps
`docs/PRE_PLAN.md` in an old ADR finds nothing on disk and must know to look in
`.ai-state/design-history/` — mitigated only by this record.

## Prior Decision

`dec-074` decided that the stateless-server scope paragraph lives once, in `PRE_PLAN.md`, with three
sites pointing at it. Its **reasoning is re-affirmed in full** — a claim published four times is a
claim that drifts three ways, and the markers are outside the invariant's scope rather than an
exception to it. Only the **location** changes, because the host document is being retired for reasons
unrelated to the invariant. The paragraph moved verbatim except for its closing sentence, which named
its own pointers.

What would reverse this: `.ai-state/DESIGN.md` growing a second statement of the scope, or the
pointers in `CLAUDE.md` / `docs/architecture.md` being replaced by restatements. Either would mean the
single-site property was lost in the move, and the right response would be to re-consolidate rather
than to restore `PRE_PLAN.md`.
