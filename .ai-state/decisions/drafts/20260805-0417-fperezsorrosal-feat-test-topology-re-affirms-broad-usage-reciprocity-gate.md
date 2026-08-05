---
id: dec-draft-df837e3b
title: "`re_affirms` carries a broad builds-on meaning here, and reciprocity is enforced mechanically"
status: proposed
category: behavioral
date: 2026-08-05
summary: "This project uses `re_affirms` as a general builds-on pointer rather than the narrow challenged-and-re-examined protocol, and closes the resulting one-directional-pointer failure with a check in `make verify` rather than with author discipline."
tags: [adr-conventions, metadata, decision-log, reciprocity, gate, sentinel]
made_by: agent
agent_type: orchestrator
branch: feat-test-topology
pipeline_tier: standard
affected_files:
  - .ai-state/decisions/
  - scripts/check_adr_health.py
  - Makefile
dissent: "Broadening a shared Praxion field in one project forks the vocabulary: a future Praxion-side consumer that assumes the narrow protocol will read 26 informal pointers here as formal re-affirmations, and this ADR does not make that visible to it."
---

## Context

`adr-conventions.md § Re-affirmation Protocol` reserves `re_affirms` for a narrow case: *"a prior
decision challenged, re-examined, and found still correct — not routine acknowledgment."* Formal
use also requires `status: re-affirmation` on the referring ADR.

This project does not use it that way. Of ~34 `re_affirms` pointers across 68 finalized ADRs, 26
sit on ADRs carrying `status: accepted` — they are ordinary "builds on / consistent with"
annotations, written while deciding something new, not the outcome of re-opening an old decision.

A sentinel audit surfaced the consequence: **8 pointers were one-directional** — the target's
`re_affirmed_by` did not list the referrer. So "what later decisions rest on this one?", the exact
query the back-reference exists to serve, returned nothing from the target's side.

The two facts are causally linked, and that link is what makes this a decision rather than a
cleanup. An author adding a lightweight "related to" pointer while writing a *different* decision
has no natural reason to go edit the target's frontmatter. The formal protocol's ceremony is what
normally prompts that second edit; informal use removes the prompt. Backfilling the 8 without
addressing the mechanism would leave the ninth to appear the next time anyone writes an ADR.

Sentinel also over-counted the asymmetry as 22 and cited `dec-025` as an example whose
`re_affirmed_by` was empty; it in fact lists both `dec-030` and `dec-032`. The measured figure is
8. The mechanism it identified was right even though its count was not.

## Decision

1. **Accept the broadened meaning for this project.** `re_affirms` means "this decision builds on,
   or is consistent with, that one". It does not imply the prior decision was challenged, and the
   referring ADR does not need `status: re-affirmation`.
2. **Require reciprocity.** Every `re_affirms` must have a matching `re_affirmed_by` on the target,
   and vice versa.
3. **Enforce it mechanically, not by convention.** `scripts/check_adr_health.py` runs in
   `make verify` and fails on a one-directional pointer, a dangling target, or frontmatter that
   `yaml.safe_load` rejects.

Requirement 3 is the load-bearing one. Requirements 1 and 2 are a convention, and this repository
has just demonstrated that a convention with no gate decays — the 8 pointers accumulated over ~60
ADRs without anyone noticing.

## Considered Options

### Accept the broad meaning and gate reciprocity (chosen)

- **Pro:** Matches what authors already do, so it does not depend on changing a habit that has held
  for 68 records.
- **Pro:** The gate makes the metadata trustworthy regardless of which meaning an author intended.
- **Con:** Diverges from `adr-conventions.md`'s documented semantics.

### Narrow `re_affirms` to the protocol and add a `relates_to` field

- **Pro:** Strictly correct against the shared convention; keeps `re_affirms` queryable as "these
  decisions were re-examined and held".
- **Con:** Migrates ~26 ADRs, and invents a field Praxion does not define — so it forks the schema
  in a *more* visible way than broadening a field's meaning does.
- **Con:** The distinction it preserves has no consumer in this project today.

### Backfill the 8 and change nothing else

- **Pro:** Smallest possible edit; closes the finding sentinel actually raised.
- **Con:** Treats the symptom. The mechanism that produced the 8 stays in place, so the count
  regrows silently — which is precisely how it reached 8 unobserved.

## Consequences

**Positive**

- "What rests on this decision?" is answerable from any ADR, in both directions.
- The frontmatter-validity half of the same gate closes a defect that was live and latent: two ADRs
  carried YAML that `yaml.safe_load` rejected, invisible because this repo's index generator uses a
  tolerant regex parser while every strict consumer would have failed.
- The gate is cheap and runs where a developer already looks.

**Negative**

- A Praxion-side consumer assuming the narrow protocol will misread this project's informal
  pointers. Nothing in the ADR files themselves signals the broadened meaning — only this record
  does.
- `make verify` gains a step that fails on a metadata problem unrelated to the code under change,
  which will occasionally interrupt work that did not cause it.
- Reciprocity is now mandatory, so adding a one-line pointer always means editing two files.

## Disconfirmation

**Falsifier.** If a Praxion-side tool ships that consumes `re_affirms` with the narrow semantics —
for instance reporting "decisions re-examined and upheld" — this project's 26 informal pointers
would make its output wrong here, and the divergence would stop being free.

**Steelmanned runner-up.** Narrowing the field and adding `relates_to` is the honest modelling
choice: the two relationships genuinely differ, and "was re-examined and upheld" carries evidential
weight that "builds on" does not. Collapsing them destroys information that no later pass can
recover, because nothing records which of the 34 pointers were which. That the distinction has no
consumer *today* is an argument about present convenience, not about whether the information is
worth keeping.

**Reversal trigger.** Revisit if a consumer of the narrow semantics appears, or if the count of
genuinely-re-examined decisions grows large enough to be worth querying separately — at which point
the migration is mechanical for new records and lossy only for the existing 34.
