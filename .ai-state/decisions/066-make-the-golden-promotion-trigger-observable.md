---
id: dec-066
title: Keep deferring golden promotion, and make its reversal trigger observable
status: re-affirmation
category: architectural
date: 2026-08-02
summary: Measured across both configured vaults, zero note-derived questions exist and zero have been routed to the trainset, so dec-059's deferral costs nothing today and its two grounds are untested rather than wrong; but its reversal trigger counts a quantity nothing recorded, so the note now carries a promoted scalar and the trigger is restated in terms that can actually be evaluated.
tags: [notes, eval-bridge, golden-promotion, measurement, observability, phase-4-gate, one-way-door]
made_by: user
agent_type: orchestrator
branch: worktree-notes-overlay-phase4
pipeline_tier: standard
re_affirms: dec-059
dissent: Deferring again on "zero adoption" risks a self-fulfilling loop — notes are unused partly because the overlay has never been exercised end to end, so waiting for organic note-derived questions may wait forever, and the runner-up's objection (a closed loop grading itself) stays live the entire time.
affected_files:
  - src/knotica/core/notes/anchor.py
  - src/knotica/core/page.py
  - src/knotica/core/operations/curate_example.py
  - src/knotica/core/operations/promote_note.py
  - .ai-state/measurements/STEP3_GOLDEN_PROMOTION.md
---

# Keep deferring golden promotion, and make its reversal trigger observable

## Context

`dec-059` routes note-derived questions to the trainset and defers golden-set promotion. The
deferral is a **one-way door per question**: `freeze()`'s `verify_disjoint_from_trainset` makes
trainset and golden mutually exclusive, so every question already routed to `qa.jsonl` is
permanently ineligible for the held-out set.

The Phase 4 brief named three inputs as required before revisiting, and flagged the third as
unmeasured — *"that last number is the cost of continued deferral, and nobody has measured it."*

All three were measured (`.ai-state/measurements/STEP3_GOLDEN_PROMOTION.md`), read-only, on
clones of **both** configured vaults — `main` and the active `decision-making`.

## Decision

**Keep deferring golden promotion, and fix the trigger.** Two parts, and the second is the
substance:

1. `dec-059`'s deferral stands. The measured cost of continued deferral is **zero**.
2. `dec-059`'s reversal trigger is restated in terms that can be evaluated, and the mechanism to
   evaluate it is implemented.

### Ground 1 — the cost of deferral is exactly zero

| input | measured |
|---|---|
| note-derived questions that exist | **0** |
| fraction `dispute`/`gap`/`question` intent | undefined (n=0) |
| already routed to the trainset, permanently ineligible | **0** |

`notes/` does not exist at either vault root, and `git log --all -- notes` returns zero commits in
both histories. The one-way door has not been walked through once. `dec-059`'s two grounds are
therefore **untested, not wrong** — and its steelmanned runner-up, that the design routes the
system's only real human questions away from the set deciding whether the KB improves, is a strong
argument about a phenomenon that has not yet occurred.

Deciding a one-way door before a single instance of the thing it governs exists would commit the
design on argument alone, which is what this project's gate-first charter exists to prevent.

### Ground 2 — the trigger could never have fired

`dec-059` says: *"Revisit when the trainset holds ≥10 note-derived questions and a compile/eval
cycle has run over them."* Nothing recorded that a trainset question came from a note:

- `promote_note(target="trainset")` delegated to `curate_example` passing **no source override**.
- `curate_example` always stamps `source="curate_example"`; `QA_SOURCES` has no note-derived member.
- The commit and `log.md` title derive from the **query**, not the note.
- `NoteActionResult.promoted_to` is a response-payload field that persists nowhere.
- The `promoted:` frontmatter scalar specified as the note-side audit trail was unimplemented
  (`td-024`).

A note-promoted trainset question was byte-indistinguishable from a hand-curated one. **A deferral
conditioned on an unobservable trigger is not deferred — it is permanent, silently.** Nobody is
ever prompted to notice, which is the failure mode that makes this worth an ADR rather than a
ledger row.

### Ground 3 — the note is the right place to record it, not `qa.jsonl`

Two mechanisms could make the count possible. The note-side one was chosen:

- **Rejected:** a note-derived member in `QA_SOURCES`. Counting becomes a `grep`, but it puts note
  provenance on a **scored surface**, requiring the contamination rulings and three shipped tests
  to be re-litigated, and it forces a fresh decision about where note-derived sits in compile demo
  selection (curated currently displaces `seed_train`). Three new questions to save a little work.
- **Chosen:** `promoted: none | gap:<id> | eval:<id>` on the note — `td-024`'s own preferred
  option. `qa.jsonl` is untouched, the contamination boundary is not approached, and the same
  change closes the audit-trail gap `td-024` was filed for.

`promoted` is a **first-class `NoteDocument` field**, not a loose frontmatter key, because
`serialize_note` emits a fixed field set: an unmodelled key would be silently dropped the first
time `reanchor` or `detach` round-tripped the note, erasing the audit trail exactly when the note
is corrected. It is written through a byte-preserving frontmatter splice
(`page.set_frontmatter_scalar`) so this new write path does not repeat `td-027`'s normalization of
hand-authored formatting. The stamp rides inside `curate_example`'s own transaction via a new
`extra_writes` hook, so a crossing remains exactly one commit.

## Considered Options

### A — Defer, and make the trigger observable (chosen)

Costs one small, additive change. Leaves the design question open at zero measured cost, and
guarantees the question can be re-opened on evidence rather than on someone remembering.

### B — Decide golden promotion now, while it is free

**Rejected, but it is the strongest rejected option.** With zero questions routed, the one-way door
costs nothing to open *today* — and it will never again be this cheap. The counter is that there is
also nothing to decide *from*: no note-derived question has ever existed, so any judgement about
their discriminative value against synthesised ones would be pure speculation dressed as
architecture.

### C — Defer without fixing the trigger

**Rejected.** This is the status quo, and it is the failure this ADR exists to name: an
unobservable trigger converts "deferred pending evidence" into "permanent by accident."

### D — Add a note-derived `QA_SOURCES` member

**Rejected** — see Ground 3. Cheaper to implement, but touches a scored surface and leaves `td-024`
open.

## Consequences

**Positive.** The deferral now has an exit. `td-024`'s trainset-side audit gap closes as a
side-effect, and the `extra_writes` hook is a general seam any future caller can use to record its
own side of a crossing without a second commit. `qa.jsonl` and the contamination rulings are
untouched.

**Negative.** `promote_note` now **mutates the note file**, where before it only read — a genuine
contract widening, though still one commit inside `curate_example`'s existing transaction. Two
gaps remain by choice: a **gap-target promotion is not stamped** (threading `extra_writes` through
`report_gap` → `write_gap_records` would touch a path other callers share, and `td-024` scopes
itself to the trainset side because gaps already carry `reported_reason = note:<path>#0`), so a
gap-promoted note reads `promoted: none`; and a **duplicate promotion is not stamped**, since
`extra_writes` runs only on a real append — the count of note-derived *questions* stays correct,
but a second note promoting an identical question leaves no trace.

**Unchanged.** Zero notes exist, so this ADR moves no data and migrates nothing. Every note written
before `promoted` existed parses as `none`.

## Disconfirmation

**Falsifier.** Either would make this decision wrong:

1. Note-derived questions accumulate to the restated trigger and, when measured, show *higher*
   discriminative value than `seed_train` questions — meaning the deferral spent that whole period
   routing the best available eval signal into the wrong dataset, irreversibly, one question at a
   time.
2. Notes see real use while `promoted` stays uncounted in practice — nobody runs the count, no
   surface reports it — in which case the trigger is technically observable and practically as dead
   as before, and the fix should have been a reporting surface (`wiki_status`) rather than a field.

**Steelmanned runner-up (Option B).** The strongest case for deciding now: this is the *only*
moment when the one-way door is free, and every question promoted from here makes it more
expensive, permanently and irreversibly. `dec-059`'s runner-up already argues the golden set is a
closed loop grading itself — an LLM synthesising questions from the very pages it will then be
graded on — and that objection does not weaken while we wait; it compounds, because every synthetic
question added to golden further entrenches the loop. Meanwhile "zero notes exist" may be evidence
about the *feature's* adoption rather than about the design question, and adoption is partly
downstream of the project's own choice not to exercise the overlay. Deferring on zero adoption
risks a self-fulfilling loop: notes stay unused, so the trigger never fires, so the mechanism never
ships, so notes stay unused. If in a year the golden set is still synthetic and the trainset holds
forty note-derived questions nobody counted, this ADR will read as the moment the project chose a
field over a decision.

**Reversal trigger.** Restated so it can actually be evaluated — this replaces `dec-059`'s
unobservable formulation:

> Revisit when a `grep` for `promoted: eval:` across `notes/<topic>/` returns **≥10** notes **and**
> a compile/eval cycle has run over the questions they produced. If those questions show higher
> discriminative value than `seed_train` questions, add the `golden.staging.jsonl` writer and make
> destination a first-class choice at the `golden_review` gate.

Independently, revisit if the count reaches ≥10 but nothing surfaces it to a human — that is
falsifier 2, and the remedy is a `wiki_status` field, not another ADR.

## Prior Decision

Re-affirms `dec-059` (note-derived questions route to the trainset; golden promotion deferred)
**without superseding it**. The routing, the human gate, and both deferral grounds stand unchanged.
What changes is that the decision's own escape hatch now works: `dec-059`'s reversal trigger named
a quantity the system did not record, and this ADR makes it recordable and restates the trigger
against the recorded form.

**Evidence a future supersession would require:** not argument, but ≥10 notes stamped
`promoted: eval:<id>` plus a compile/eval cycle over the questions they produced, showing
discriminative value against `seed_train` questions. The mechanism to produce that evidence now
exists; the missing input is note adoption, not code.
