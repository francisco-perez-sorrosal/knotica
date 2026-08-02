---
id: dec-draft-40a49c61
title: Close the block-ID spikes and the native-citation spike — measurement and specification, not deferral
status: re-affirmation
category: architectural
date: 2026-08-01
summary: Measured on real vault rewrites, ordinary knowledge rewrites orphan 7.7% of anchors — below the 8-20% band dec-058 accepted — and 85% of observed orphaning comes from a wholesale supersession that block IDs could not survive; separately, MCP's tool-result content union contains no search_result type, so the native-citation spike is unachievable rather than pending.
tags: [notes, anchoring, block-ids, citations, mcp, measurement, phase-3-gate, spike-closure]
made_by: user
agent_type: orchestrator
branch: worktree-notes-overlay-phase3
pipeline_tier: standard
re_affirms: dec-058
dissent: Both closures rest on a single vault with seven ordinary rewrite events and one topic; a corpus with a different rewrite mix — or a future loop tuned toward heavier paraphrase — could push the residual rate back above the accepted band, and closing the spikes discards a pre-registered experiment that was cheap to keep open.
affected_files:
  - docs/designs/notes-overlay/STEP1_ORPHAN_RATE.md
  - docs/designs/notes-overlay/SPIKE_2_CITATIONS.md
  - scripts/measure_orphan_rate.py
  - scripts/measure_rewrite_severity.py
  - scripts/measure_real_rewrites.py
---

# Close the block-ID spikes and the native-citation spike — measurement and specification, not deferral

## Context

`dec-058` chose the bi-partite anchor model and declined block-ID injection, accepting an
**estimated 8–20% residual hard-orphan rate** (versus 2–6% with IDs) as the price. That number was
imported from prior-art research and had never been measured on this corpus. It made the
declination falsifiable: reversal trigger **(a)** was "Spike 3a and 3b both pass their gates", and
trigger **(b)** was "the fuzzy-band review queue is measured to exceed roughly one item per rewrite
event per active topic".

`SYSTEMS_PLAN` § Sequencing carried three Phase 3 spikes: **3a** (block-ID preservation ≥95%),
**3b** (block-ID eval delta), and **Spike 2** (native `search_result` citations for capture
precision). 3a and 3b are the only billed items in the phase. The superseded draft that `dec-062`
replaced recommended deferring 3a/3b; `dec-062` kept that recommendation while discarding its
reasoning. The deferral has therefore been standing on an *unmeasured* premise ever since.

Two free measurements were run before spending anything.

## Decision

**Close Spikes 3a, 3b, and 2 as "not needed" / "not achievable" respectively.** They are removed
from Phase 3, not carried forward as deferred or blocked. `dec-058`'s declination of block IDs is
re-affirmed on measured evidence rather than on estimate.

### Ground 1 — the residual rate is below the band `dec-058` accepted

Measured on a read-only clone of the live vault, resolving anchors sampled from real pre-rewrite
page text against the real post-rewrite text with the shipped resolver — no synthetic perturbation,
no interpolation. The 55 modified-content-page events in history are not one population:

| class | events | anchors | hard-orphan |
|---|---|---|---|
| **ordinary knowledge rewrites** | 7 | 209 | **7.7%** |
| wholesale supersession | 1 | 90 | 100% |
| OKF repair migration | 40 | 1236 | 0.5% |
| hard-wrap reflow migration | 7 | 221 | **0.0%** |

7.7% sits below the 8–20% band. Reversal trigger (b) is **not met**: at that rate a topic needs
~13 anchors on one rewritten page to generate a single review item.

### Ground 2 — block IDs cannot address the dominant orphan source

**85% of all measured orphaning comes from one event**: a page superseded wholesale when its slug
changed (page similarity 0.161, every heading replaced). An injected `^id` would have been
destroyed along with the text it marked. The class that orphans heavily is *immune to the
intervention 3a/3b would buy*, while still costing the score-integrity inversion `dec-058` declined
it for. Both bulk migrations — including the hard-wrap reflow, which should be the worst case for
verbatim quote matching — are effectively harmless.

Trigger (a) remains formally unanswered, but answering it is now pointless: a passing result would
buy almost nothing measurable on this corpus.

### Ground 3 — Spike 2 is barred by the protocol, not pending a vendor

MCP's tool-result content union is exactly `text`, `image`, `audio`, `resource_link`, `resource`,
plus a separate `structuredContent` field — in the current specification revision (**2026-07-28**)
and in the prior one (`2025-06-18`) alike. **`search_result` is a Messages API block type with no
MCP equivalent**, so a spec-compliant server cannot emit one; Anthropic's search-results
documentation never mentions MCP. Corroborating, `claude-agent-sdk-python` issue #574 reports
`search_result` blocks from MCP tools being silently dropped at the bridge (closed, no maintainer
response).

Part two fails independently: citations attach as `search_result_location` objects to the
assistant's text blocks in the **host's** conversation state, and an MCP server sees only tool-call
arguments. Citation metadata reaches `note_capture` only if the model copies it into an argument —
the hand-copying the spike existed to eliminate. `cited_text` is also block-granular, not
span-granular.

## Considered Options

### A — Close all three spikes (chosen)

Removes two billed experiments and one vendor-watch item from the phase on evidence. Costs the
option value of a pre-registered experiment.

### B — Run 3a/3b anyway, since they were pre-registered

**Rejected.** Pre-registration is a guard against motivated reasoning about *whether* to run an
experiment, not a commitment to spend once the experiment's premise has been measured away. The
gate `dec-058` wrote was explicitly conditional; honouring the condition means honouring its
negative outcome too. Running them would spend billed eval runs to answer a question whose answer
cannot change the design.

### C — Leave them deferred rather than closed

**Rejected**, and the brief pre-empted it: *"If it is not [the binding constraint], 3a/3b have lost
their justification and should be closed as 'not needed', not left open."* An indefinitely deferred
spike is a standing invitation to re-litigate on the same absent evidence, which is how the
unmeasured 8–20% estimate survived three phases.

### D — Keep Spike 2 open pending a future MCP revision

**Rejected.** Adding `search_result` to MCP's content union would be a protocol change by a
standards body, not a vendor rollout on a schedule. "Watch indefinitely" is not a plan; if the
union ever gains a citation type, that is itself the trigger to revisit.

## Consequences

**Positive.** Phase 3 resolves with zero billed spend. The bi-partite model's central bet is now
backed by measurement on this corpus rather than by an imported estimate, and the measurement is
reproducible (`scripts/measure_real_rewrites.py` against a clone). Two recurring re-litigation
surfaces are removed.

**Negative.** The residual-rate figure rests on **seven ordinary rewrite events in one topic of one
vault** — a small sample whose anchors are also not independent (209 anchors across 7 events). The
anchors were synthesised from page text rather than drawn from real user notes, so a real
population that clusters on volatile claim bullets could score worse. Closing the spikes means a
future reversal has to re-derive the case from scratch rather than resume a standing plan.

**Newly surfaced, not addressed here.** Wholesale **supersession** is an unmodelled event class: it
orphans every anchor into a page, correctly, but is indistinguishable to a reviewer from "your
passage was reworded". The anchor of record already carries what a pointer to the replacing page
would need. Recorded as follow-up work, not decided.

## Disconfirmation

**Falsifier.** Either would make this decision wrong:

1. The residual hard-orphan rate measured on a **larger and more diverse** corpus — several topics,
   several vaults, real user notes rather than synthesised anchors — lands materially above 20%
   for *ordinary* rewrites (supersessions and bulk migrations excluded). The 7.7% figure would then
   be an artifact of a thin sample, and trigger (b) would be live after all.
2. The loop's rewrite behaviour shifts toward heavier paraphrase — page similarity routinely below
   ~0.90 — while leaving block structure intact. That is the one regime where an `^id` survives and
   the quote does not, i.e. exactly where block IDs would pay, and it is the regime this corpus
   happens not to contain.

**Steelmanned runner-up (Option B).** The strongest case for running 3a/3b anyway: the experiment
was pre-registered precisely so the decision would not be made on argument, and this ADR makes it
on a sample of seven events from one topic — a sample that would not survive review in any other
context. `dec-058` set the 8–20% band as an *estimate* it expected to be wrong; measuring 7.7% just
below the low end of a made-up band is weak evidence for a structural claim, and one differently
distributed vault could invert it. Meanwhile the cost of the spikes was bounded and known, and the
information would have been permanent. If a later corpus measures 25% and the review queue becomes
the thing users complain about, this decision will look like it closed a cheap experiment to avoid
paying for an answer it had already guessed.

**Reversal trigger.** Revisit when either holds: (a) a measurement on a materially larger corpus —
multiple topics or vaults, with real captured notes — puts ordinary-rewrite hard-orphaning above
20%; or (b) the loop's measured page-similarity distribution shifts so that a majority of rewrites
land below 0.90 while heading structure survives. For Spike 2 specifically: revisit if an MCP
specification revision adds a citation-bearing content type to the tool-result union.

## Prior Decision

Re-affirms `dec-058` (bi-partite immutable anchor of record plus derived live projection) **without
superseding it**. Nothing in `dec-058`'s model changes: the anchor record, the resolution ladder,
the thresholds, and the append-only correction rule all stand. What changes is the status of its
own falsification path — reversal trigger (b) is measured and not met, and trigger (a) is closed as
unable to change the outcome.

Note that the "3a/3b deferral" this supersedes in substance was never itself an ADR: it was a
standing position carried inside `dec-058`'s reversal trigger (a) and restated in `dec-062`'s
Prior Decision section. There is accordingly no ADR to mark `superseded`; this record re-affirms
`dec-058` and closes the deferral it carried.

**Evidence a future supersession would require:** not argument, but a residual hard-orphan
measurement for *ordinary* knowledge rewrites, on a corpus of materially more than seven events
spanning more than one topic, using real captured note anchors — landing above the 8–20% band. The
instruments to produce it are committed (`scripts/measure_real_rewrites.py`,
`scripts/measure_rewrite_severity.py`); the missing input is a richer vault, not new code.
