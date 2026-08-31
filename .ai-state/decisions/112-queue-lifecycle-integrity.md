---
id: dec-112
title: A cascade closure is the gap speaking, and a published candidate branch outranks both queue writers
status: accepted
category: behavioral
date: 2026-08-30
summary: The dismiss cascade marks its closures with a `gap dismissed:` reason prefix that the drain's dedup set excludes, so a reopened gap can be re-sourced while a human's own reject still dedups; and both queue writers skip an approved record whose source-candidate branch is already published, leaving that record for the gate to disposition
tags: [gapfill, fill, suggestions, gap-lifecycle, source-gate, queue]
made_by: agent
agent_type: implementer
branch: main
pipeline_tier: standard
dissent: A reason-string prefix is a stringly-typed marker on a JSON record that anything can hand-edit; a boolean `cascaded` field would be checkable, and skipping a published-branch record rather than refusing the dismissal leaves the operator with a gap they believe is dismissed and a suggestion that is still approved.
affected_files:
  - src/knotica/core/gapfill.py
  - src/knotica/core/source_gate.py
---

# A cascade closure is the gap speaking, and a published candidate branch outranks both queue writers

## Context

Two writers were added to the suggestion queue without a rule for how they
compose with the queue's existing readers, and both produced a strand a field
report would eventually find.

**The reopen contract was false.** `_plan_dismiss_cascade`'s docstring, the
`review_gap` tool description, and `docs/gap-fill.md` all promised that
reopening a dismissed gap and re-draining re-proposes its sources. The drain's
dedup set was built from *every* record at *every* status, so a cascade-rejected
record deduped a re-drain exactly as a human rejection did. A gap dismissed by
mistake and reopened became permanently sourceless: only a merged source
resolves a gap, the gate needs an `approved` suggestion, and discovery could
never stage one again — while still billing a search call per drain.

**Two writers could un-approve a published candidate.** The dismiss cascade
(`_CASCADE_SOURCES` includes `approved`) and the automatic queue heal
(`_HEALABLE` includes `approved`) both move records out of `approved` with no
reference to in-flight ingests. `handle_source_pass` fast-forwards the candidate
branch onto the default branch and *then* calls `apply_gate_outcome`, whose
status check refuses — after the merge. End state: the source is in the vault,
the suggestion is `rejected`, the gap is unclosed, and the trainset grower never
runs. Precisely the "merged-but-unstamped source" `source_gate`'s own comment
says cannot happen.

## Decision

**A cascade closure is marked, and the mark excludes it from dedup.** The
cascade writes `decided_reason` under one declared constant,
`_CASCADE_REASON_PREFIX = "gap dismissed: "`, and the drain's dedup set skips
records carrying it (`_is_cascade_rejection`). A re-staged record keeps its
deterministic `suggestion_id`, so it *replaces* the closed line rather than
appending a second record under the same id. A human `reject` is untouched and
still dedups.

**A published candidate branch is untouchable by either queue writer.** Both
`_plan_dismiss_cascade` and `_heal_queue` take a `protected` set of `id8`
branch infixes (`_published_source_id8s`, read from `loop/c/` and `loop/wip/`
tips) and skip an `approved` record in it. `handle_source_pass` additionally
re-checks `status == "approved"` *inside* its mutation span, before `_keep`, so
the merge can never precede the check.

## Considered Options

### Marker on `decided_reason` vs. a boolean field on the record

A `cascaded: bool` field would be checkable rather than stringly-typed. It was
rejected for two reasons: it is a schema addition for a distinction only one
reader makes, and records closed by earlier versions carry no such field, so the
old strand would persist on exactly the queues that already have it. The prefix
is derivable from data that already exists, and writer and reader share one
declaration, so it cannot drift.

### Reopen withdraws its cascade-rejected records back to `pending`

The cascade already records `cascaded_suggestion_ids`, so the reverse transition
is computable. Rejected because it resurrects a possibly-stale candidate set
(the ranking is months old, the URL may be dead) and re-introduces the strand
the cascade exists to remove if the reopen is itself a mistake. Re-draining
re-ranks against current providers, which is the better answer.

### Refuse the dismissal when a published candidate exists, rather than skipping

Rejected: it punishes an operator for an ingest they may not know exists, and a
refusal has no good fix text ("wait for a loop cycle you cannot see"). Skipping
leaves the record where the gate will disposition it within one cycle, and the
gate's own pre-merge check is what makes that safe.

## Consequences

**Positive.** The documented reopen contract is now true and pinned by a test
that actually re-drains. A dismissal can no longer strand a merge mid-flight.
The gate's pre-merge check makes the refusal cost nothing instead of costing a
vault mutation.

**Negative.** Both queue writers now read git branch tips, so `apply_gap_decision`
and the drain acquire a dependency on the vault being a git repo at that path —
previously they touched only the store. A hand-edited `decided_reason` beginning
with `gap dismissed: ` will be treated as a cascade closure. And a dismissed gap
can still show an `approved` suggestion until the next gate pass; the surfaces
say nothing about why.

## Disconfirmation

**Falsifier.** If operators routinely re-dismiss the same reopened gap because
the re-drain restages candidates they already judged bad, the marker is
excluding too much: the distinction that matters would be "did a human look at
this source", not "which writer closed it".

**Steelmanned runner-up.** Refusing the dismissal outright (option 3) is the
only option that never leaves the operator with a half-closed gap. If the
skipped-record window proves confusing in practice — a dismissed gap whose
suggestion is still approved, with nothing on any surface explaining it — the
honest fix is to refuse and say why, not to add a fourth status.

**Reversal trigger.** A second reader needing to distinguish cascade closures
from human rejections. One reader justifies a derived marker; two justify a
first-class field on the record.
