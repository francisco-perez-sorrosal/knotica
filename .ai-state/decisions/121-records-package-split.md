---
id: dec-121
title: core/records.py becomes the core/records/ package, split by record family
status: accepted
category: architectural
date: 2026-08-31
summary: The 994-line records module splits into seven modules behind a re-exporting __init__ — fields, qa, metrics, gaps, suggestions, op_lines, source — retiring its file-size-ratchet exemption with zero consumer import churn, and GapRecord gains the unknown-field round-trip carrier SuggestionRecord already had
tags: [refactoring, records, module-boundaries, file-size, tech-debt, schema-evolution]
made_by: agent
agent_type: implementer
branch: main
pipeline_tier: standard
dissent: "Seven modules replace one file's section banners; a reader who could grep one file for every frozen record shape must now know which module owns which family — and the two line grammars (log entry, commit subject) are a weaker family than the four JSONL records, so `op_lines.py` is the seam most likely to be wrong."
affected_files:
  - src/knotica/core/records/__init__.py
  - src/knotica/core/records/fields.py
  - src/knotica/core/records/qa.py
  - src/knotica/core/records/metrics.py
  - src/knotica/core/records/gaps.py
  - src/knotica/core/records/suggestions.py
  - src/knotica/core/records/op_lines.py
  - src/knotica/core/records/source.py
  - tests/test_file_size_ratchet.py
  - .ai-state/DESIGN.md
  - docs/architecture.md
  - .ai-state/TEST_TOPOLOGY.md
---

# `core/records.py` becomes the `core/records/` package, split by record family

## Context

`core/records.py` was the last ratchet exemption whose ledger row (td-009) already named its
own seam: "split by record family (eval / gap / suggestion / note)". At 994 lines it was 194
over the 800-line ceiling, and its baseline had been raised four times — each raise correctly
argued (`GapRecord.decided_reason`, `SuggestionRecord.extra`, `GapRecord.answered_in_vault_at`)
because a field added to a frozen record is genuinely inseparable from that record's own
`to_json_line`/`from_json_line` pair. The growth was structural, not sloppy: nothing inside a
record family can be extracted, so the file could only ever be cut *between* families.

Two independent facts made now the moment. First, `dec-115` had just proven the pattern on the
larger neighbour: `core/gapfill.py` (1536 lines) became a package with a verbatim re-exporting
`__init__` and zero consumer edits. Second, td-071 required editing `GapRecord` anyway — the
`extra` unknown-field carrier `SuggestionRecord` received in F-FL-08 — so the alternative was a
fifth baseline raise on a module whose fix was already written down.

The module also carries the strongest characterization net available: `tests/test_records.py`,
`test_records_gap.py` and `test_records_suggestion.py` are 123 tests over the frozen shapes, and
every consumer of the module runs inside `vault-semantics` (822 tests) and `gapfill-spine` (323).

## Decision

`core/records.py` becomes the `core/records/` package. Seven modules, each with one
responsibility stated in its own docstring; `__init__.py` re-exports the public surface verbatim
so `from knotica.core.records import X` keeps working for every consumer.

| Module | Lines | Responsibility |
|---|---|---|
| `fields.py` | 137 | The boundary parsing helpers every family's `from_json_line` parses through, plus `RecordParseError` — the error grammar declared once |
| `qa.py` | 117 | `qa.jsonl`, the curated example |
| `metrics.py` | 117 | `metrics.jsonl`, one eval-history record per scored generation |
| `gaps.py` | 268 | `gaps.jsonl`, one knowledge gap (+ the td-071 `extra` carrier) |
| `suggestions.py` | 200 | `suggestions.jsonl`, one gap × candidate join |
| `op_lines.py` | 126 | The two per-operation line grammars: the `log.md` entry and the commit subject |
| `source.py` | 146 | Source-provenance frontmatter and the body-only digest convention |
| `__init__.py` | 132 | The import surface + the package's own map |

The dependency graph is acyclic and one-directional: `fields` is the leaf; `qa`, `metrics`,
`gaps`, `suggestions` and `source` sit on it; `op_lines` depends on nothing local (its two
validators moved with it, because they validate *line slots*, not JSON fields).

Alongside the split, `GapRecord` gains the same unknown-field contract `SuggestionRecord`
carries: an `extra` mapping partitioned on parse against a dataclass-derived known-field set, and
merged after the schema-ordered block on emit.

## Considered Options

### One package, seven modules, re-exporting `__init__` (chosen)

- Zero import churn: no consumer, in `src/` or `tests/`, needed an edit — grep-verified.
- Every module lands between 117 and 268 lines; the ratchet needs no successor exemption, so the
  entry is *deleted* rather than lowered.
- The families are genuinely independent: no record type imports another, so no seam had to cut
  through a `to_json_line`/`from_json_line` pair.

### The ledger row's literal four-way cut (eval / gap / suggestion / note)

Rejected as written, because it does not describe this module. There is no note record here
(notes live in `core/notes/`), and "eval" conflates two independently versioned families
(`qa.jsonl` and `metrics.jsonl`). Worse, it strands the three non-JSONL shapes — the log entry,
the commit subject, and the provenance frontmatter — which together are 272 lines with no home in
that taxonomy. The row named the right *axis* (record family) and the wrong *partition*.

### Split only the shared helpers out, keep one records module

Sheds ~137 lines and leaves the module at ~857 — still over the ceiling, still requiring a
baseline entry, and cutting on the one axis (helpers vs. records) that does not follow how the
file actually changes: every raise in its history came from a record family growing a field.

### Accept with rationale, raise the baseline a fifth time

The option td-009 explicitly left open. Rejected on the same grounds `dec-115` rejected it: a
ceiling that everything eventually excuses itself from is not a ceiling, and this module's own
baseline comment had begun citing the split as the real fix.

## Consequences

**Positive.** The exemption is retired outright, the second paid by splitting rather than
shrinking. A future field lands in a 117–268 line module beside only the record it belongs to.
`td-023`'s historical note — that the shared log-entry fix "needs the `records.py` budget" — is
now moot: `op_lines.py` has room.

**Positive (td-071).** `GapRecord` now carries the unknown-field round-trip contract, so the
package's "parsers tolerate unknown extra fields" claim is true *through mutations* for both
families a full-file rewriter touches. This mattered concretely: the td-070 drain stamp made the
drain a second whole-file rewriter of `gaps.jsonl` alongside the dismiss/reopen path, so a field
a newer writer added was being erased from every gap in the topic by a routine drain — silently,
with no decision taken and no error raised. Two tests pin it, and both were proven to fail
against the un-carried record before landing.

**Negative.** Seven import statements replace seven in-file section banners. The package-private
helpers in `fields.py` keep their leading underscores and are imported by name across module
boundaries — deliberate (renaming fifteen helpers would have added churn to a behavior-preserving
change) but it means "private" here reads as package-private, not module-private.

**Neutral.** Ten finalized ADRs cited `src/knotica/core/records.py` in `affected_files`; the ADR
health gate rejects a path that no longer resolves, so all ten were mechanically repointed at
`src/knotica/core/records/` — exactly what the `dec-115` split did for `gapfill/`.

## Disconfirmation

**Falsifier.** A record change that has to touch three or more of the seven modules at once.
That would mean the seams cut across a change axis rather than along one — most plausibly if a
future shared concern (a second `extra`-style carrier, a cross-family validator) starts being
declared per module instead of in `fields.py`.

**Steelmanned runner-up.** Keeping one module and raising the baseline a fifth time is the
option with the better track record here: four raises, each individually correct, each preserving
a single-file grep for every frozen record shape — which is a real property for a module whose
whole job is to encode a constitution the vault ships. The counter is that the constitution is
*already* seven separately versioned things, and a file that must be split eventually is cheapest
to split while a strong characterization net exists and while one of its records is being edited
anyway.

**Reversal trigger.** If `op_lines.py` proves to be the wrong seam — a log-entry change and a
commit-subject change stop arriving together, or the provenance/digest pair starts needing the
line validators — fold `op_lines.py` back into a `lines`-plus-`source` module rather than let a
third validation helper be declared in two places.
