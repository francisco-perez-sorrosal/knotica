---
id: dec-draft-90eda308
title: Notes reach the eval corpus through curate_example into the trainset only; gaps reuse the reported origin and are never auto-filed
status: proposed
category: architectural
date: 2026-07-29
summary: A note-derived question is promoted by a human-confirmed curate_example() append to qa.jsonl with pages_used naming the anchored KB page; golden promotion is deferred; dispute/gap/question notes file gaps through the existing reported origin with a note pointer in reported_reason; an orphaned note never auto-files anything.
tags: [notes, evals, trainset, golden, curate-example, gapfill, gap-origin, human-gate, d-merit, contamination]
made_by: agent
agent_type: systems-architect
branch: main
pipeline_tier: standard
affected_files:
  - src/knotica/core/operations/curate_example.py
  - src/knotica/core/gapfill.py
  - src/knotica/core/records.py
affected_reqs: [REQ-11]
dissent: Routing note questions to the trainset makes them permanently ineligible for the golden set (freeze() enforces disjointness), so a deliberately narrow v1 choice silently forecloses the higher-value destination for every question it touches.
---

# Notes reach the eval corpus through `curate_example` into the trainset only; gaps reuse the `reported` origin and are never auto-filed

## Context

Locked decision D3 requires note-derived questions to reach eval datasets through a human review gate. The eval-gap research concluded that the cheapest path is the existing `pages=` restriction on `evals.golden.bootstrap()` / `evals.train_bootstrap.bootstrap_trainset()`, with `curate_example()` as an even cheaper one-shot, and that the `SuggestionRecord` queue is a structural mismatch (its terminal shape is a `SourceCandidate`, its join key is a DOI/URL normaliser, and there is no code path from an `ingested` suggestion to a dataset append).

**Verification correction.** The `pages=` route does not work. Both `golden.bootstrap` (`golden.py:559-561`) and `bootstrap_trainset` (`train_bootstrap.py:93`) compute `entity_pages(store, topic)` first and then *intersect* it with `pages`. `entity_pages` walks `iter_page_paths(store, topic)`; a note at `notes/<topic>/x.md` is not in that set, so `pages=[note_path]` selects **zero** pages and returns empty. The parameter can narrow the KB page set; it cannot introduce a path.

Two further facts constrain the design. `freeze()` runs `verify_disjoint_from_trainset` and raises `GoldenSetContaminationError`, so trainset and golden are mutually exclusive per question. And D-MERIT (arXiv:2406.16048) shows that evaluation sets built from incomplete, biased relevance annotation lead to false conclusions about retrievers — notes are exactly such a sample, since users annotate what surprised or annoyed them.

The gap pipeline has three origins (`measured`, `reported`, `retracted`) sharing one `GapRecord` shape; a fourth would need a `GAP_ORIGINS` member, a `_ORIGIN_QA_ID_PREFIX` entry, a new writer, and updates to every origin-switching consumer.

## Decision

**The bridge is `core.operations.curate_example.curate_example(query, pages_used, answer, verdict, notes)`**, called after explicit human confirmation.

- `query` is the note-derived question; the client-as-brain supplies `answer` from the KB, so no synthesis call is needed at all.
- **`pages_used` names the anchored KB page(s), not the note path.** An eval question must be answerable from the KB corpus, not from a personal note. `pages_used` is already unvalidated free-form strings, so no schema change is required.
- Terminal shape is a `QARecord` with `source: curate_example` — one `VaultTransaction`, one commit, idempotent by `(query, answer, verdict)` fingerprint.

**Golden (`held_out`) promotion is deferred out of v1.** When wanted, the smallest correct path is a writer that stages a candidate into `golden.staging.jsonl` (reusing `_write_staging`'s shape) and lets the existing `golden_review` load/save + `freeze()` human gate do the rest.

**Gaps from `intent ∈ {dispute, gap, question}` notes reuse `origin: reported`.** Provenance is carried in the existing free-text `GapRecord.reported_reason` as `note:<notes/topic/file.md>#<anchor-id>`. No fourth origin.

**An orphaned note never auto-files a gap.** It becomes a review-queue item. The review surface offers "file as gap" as a one-click **human** action routed through the existing `report_gap` path, offered only for the three opted-in intents and never for `reflection`.

## Considered Options

### Option A — `curate_example` into the trainset, golden deferred (chosen)

- **Pro** — zero new schema, zero new queue, zero new JSONL file; the terminal record shape is already exactly right.
- **Pro** — avoids putting a biased, sparse sample into the frozen benchmark, which is D-MERIT's precise warning.
- **Pro** — the human gate is the confirmation before the call; nothing automatic can reach a dataset.
- **Con** — `freeze()`'s disjointness guard means every question routed here is permanently ineligible for golden.
- **Con** — no batch path; promotion is one question at a time.

### Option B — `bootstrap(pages=[note_path])` / `bootstrap_trainset(pages=[note_path])`

- **Con** — verified non-functional: `pages` is intersected with `entity_pages(store, topic)`, which never contains a note path. Selects zero pages.
- **Con** — even if made to work, it would synthesise questions *from the note's own prose*, producing eval questions the KB may not be able to answer.

### Option C — `bootstrap(pages=[<anchored KB page>])` steered by the note

- **Pro** — uses `pages=` exactly as designed and grounds the question in the KB.
- **Con** — `bootstrap` synthesises the question itself and accepts no seed; steering it needs a new parameter and an LLM call to reproduce a question the user already wrote.

### Option D — Reuse the `SuggestionRecord` queue

- **Con** — `candidate` is contractually a `SourceCandidate.to_record()` payload; the queue's terminal action is a `loop/c/*` source ingest with no path to a dataset append; the `(gap_id, source_key)` join key is a DOI/URL normaliser meaningless for a note-derived question. Repurposing it breaks the opaque-candidate contract the entire P3/P4 pipeline rests on.

### Option E — A fourth `note` gap origin

- **Pro** — cheap and additive by construction; makes note-sourced gaps trivially filterable.
- **Con** — a note with `intent: gap` *is* a user-reported gap; only the capture surface differs, and a capture surface is not a taxonomy. `reported_reason` already carries the pointer. Reversible later if note-origin gaps prove behaviourally distinct.

### Option F — Auto-file a gap when a note orphans

- **Pro** — the research identifies an orphaned note as a natural regression probe, and automation captures every one.
- **Con** — lets the personal layer write into the KB gap pipeline unattended, breaching D2's opt-in-per-note boundary.
- **Con** — creates a feedback loop where every rewrite generates gaps proportional to note density on the touched page, making notes a rate-limiter on the loop's own healing.

## Consequences

**Positive**

- The eval bridge adds no schema, no record type, no queue and no sixth JSONL file.
- The frozen benchmark is protected from a known bias mode.
- `intent` (D2) does real, visible work: it decides whether "file as gap" is even offered.
- Gap provenance survives without touching `GAP_ORIGINS`.

**Negative**

- Trainset routing forecloses golden for that question. The human gate must therefore present the destination as a **deliberate one-way choice**, not a default — a UX obligation this decision creates for the interface layer.
- Promotion is one-at-a-time; a user with many good notes has no batch path in v1.
- Regression-probe signal from orphaned notes is captured only when a human acts, so some is lost.

## Disconfirmation

**Falsifier.** This is wrong if either holds: (1) note-derived questions measurably behave like the *best* eval questions — high discriminative power, low redundancy against synthesised ones — in which case keeping them out of golden wastes the highest-value signal the system produces and D-MERIT's bias warning is being over-applied to a regime it was not measured in; (2) the volume of orphaned `dispute`/`gap`/`question` notes turns out to be low single digits per month, in which case the feedback-loop objection to auto-filing has no force and the automation is free signal.

**Steelmanned runner-up (Option F plus golden promotion in v1).** The strongest case: the whole point of the notes layer is that it produces *real human questions*, which MS MARCO-class evidence says beat synthesised questions for evaluation. This design takes that asset and routes it exclusively into the trainset — the dataset that shapes prompts — while the golden set, the thing that actually decides whether the KB is getting better, continues to be populated by an LLM synthesising questions from the very pages it will then be graded on. That is a closed loop grading itself. A handful of genuine reader questions in the held-out set would be worth more than twenty synthetic ones, and D-MERIT's warning is about *incomplete relevance annotation in retrieval benchmarks*, not about question provenance — it is being stretched. Likewise, requiring a human click for every orphan guarantees that most regression probes are simply never filed, because the queue will be reviewed sporadically at best; the automation objection ("notes become a rate-limiter") assumes a note density the system will not see for years.

**Reversal trigger.** Revisit when the trainset holds ≥10 note-derived questions and a compile/eval cycle has run over them: if they show higher discriminative value than `seed_train` questions, add the golden staging writer in Phase 4 and make destination a first-class choice at the gate. Independently, revisit auto-filing if the orphan queue is measured to be reviewed less often than orphans are produced — at that point the choice is between automation and losing the signal entirely.
