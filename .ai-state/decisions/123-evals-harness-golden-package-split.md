---
id: dec-123
title: evals/harness.py and evals/golden.py become packages, after proving the instrument fingerprint is layout-independent
status: accepted
category: architectural
date: 2026-08-31
summary: The 1241-line harness and 975-line golden modules split into seven- and eight-module packages behind re-exporting __init__ files, retiring both file-size-ratchet exemptions with zero consumer import churn — unblocked by proving, in code and empirically, that harness_version() folds in only declared inputs and so cannot rotate when module layout changes
tags: [refactoring, evals, module-boundaries, file-size, tech-debt, reproducibility, harness-version]
made_by: agent
agent_type: implementer
branch: main
pipeline_tier: standard
dissent: "Fifteen modules replace two files a reader could grep whole. The `evals/harness/` seam most likely to be wrong is `artifacts.py`, which merges record assembly, manifest rendering, the cross-generation delta and the transaction write into 284 lines on the argument that they are 'what the run leaves behind' — a weaker cohesion claim than the other six, and the one place a future reader may find two concerns where this split asserted one."
affected_files:
  - src/knotica/evals/harness/
  - src/knotica/evals/golden/
  - tests/test_evals_config.py
  - tests/test_architecture_boundaries.py
  - pyproject.toml
  - .ai-state/DESIGN.md
  - docs/architecture.md
  - .ai-state/TEST_TOPOLOGY.md
  - tests/test_file_size_ratchet.py
---

# `evals/harness.py` and `evals/golden.py` become packages, after proving the instrument fingerprint is layout-independent

## Context

Both modules carried file-size-ratchet exemptions: `harness.py` at 1241 lines (441 over the
800-line ceiling) and `golden.py` at 975 (175 over). Their shared ledger row deferred the split
on a specific, load-bearing fear, recorded verbatim: *"`harness.py` is the eval instrument, and
changing it rotates `harness_version` and refreezes every topic's baseline — that cost belongs
to its own pass, not to a feature phase."*

If true, that fear made the split genuinely expensive: `harness_version` is stamped into every
`metrics.jsonl` record, and the loop's gate refuses to compare two scalars carrying different
fingerprints. A rotation would strand every topic's history and force a re-baseline.

**The fear was false, and this was established before any code moved** — the deferral had
never actually been tested, only repeated.

*Mechanism (read from the code).* `evals/config.py::harness_version` digests exactly five
declared values: `judge_prompt_hash` (supplied by the caller), `judge_snapshot`,
`worker_snapshot`, `scalar_formula_version`, and a `runner_config_hash` folding the installed
`dspy` version and `failure_score`. It reads no source text, no `__file__`, no module name, no
import path. Nothing in `evals/` calls `inspect.getsource`, and the one hash taken over program
text — `judge.JUDGE_PROMPT_HASH` — digests a *prompt string constant*, not a file. A module's
location is structurally not an input.

*Empirically.* `harness_version()` was captured for the default config before any edit and
re-captured after both splits landed: byte-identical
(`6d9a19b55b55b897f9f95edda5445282ff9b80ed536b13851beb704f8443362d`), along with the
judge-prompt hash and every folded config field.

## Decision

Split both modules into packages with verbatim re-exporting `__init__` files, following the
`dec-115` / `dec-121` precedent, and convert the disproved fear into a permanent test.

`evals/harness/` — one module per stage of the run's data flow:

| Module | Lines | Responsibility |
|---|---|---|
| `errors.py` | 95 | The refusal grammar, declared once because its four variants are raised from four different stages |
| `paths.py` | 39 | Where a run's three outputs land inside a topic |
| `accounting.py` | 145 | The usage-totalling LLM proxy and the post-run spend ceilings it feeds |
| `evaluate.py` | 203 | Driving `dspy.Evaluate`, the two error-capture seams, the instrument-failure rejection |
| `scoring.py` | 230 | Per-example breakdown, the frozen budget, the composed scalar |
| `artifacts.py` | 284 | The record, the manifest, the cross-generation delta, and the one transaction that writes them |
| `run.py` | 303 | `run_eval` itself and the frozen-corpus clone |
| `__init__.py` | 101 | The import surface + the package's own map |

`evals/golden/` — cut at the read/write seam the source already marked with a banner comment:

| Module | Lines | Responsibility |
|---|---|---|
| `contract.py` | 164 | Where a golden set lives, the floor it declares, the refusal grammar both sides raise. The leaf |
| `manifest.py` | 103 | The content-addressing `MANIFEST.json`: parsed on read, rendered on freeze |
| `read.py` | 108 | `load` / `to_example` / `verify_disjoint_from_trainset` |
| `candidates.py` | 48 | The candidate-dict key names and the boundary parsers both write-side stages read through |
| `support.py` | 132 | Locating a model-supplied quote back to real 1-based line numbers |
| `synthesize.py` | 260 | The generate stage: `bootstrap`, `entity_pages`, the uncommitted staging write |
| `freeze.py` | 225 | The freeze stage: the one commit |
| `__init__.py` | 110 | The import surface + the package's own map |

Both dependency graphs are acyclic and one-directional. Package-private helpers keep their
leading underscores and are imported by name across module boundaries — the convention
`dec-121` established, deliberately not re-litigated here.

Alongside the split, `test_the_fingerprint_derives_only_from_its_five_declared_inputs`
recomputes the digest from the five declared inputs with an independently-written canonical
hasher and asserts equality. A sixth input folded into `harness_version` — a source hash, a
`__file__`, a module name — makes the recomputation disagree. Proven to fail before landing:
folding `__file__` into the payload turned it red.

## Considered Options

### Packages with re-exporting `__init__`, after a hard fingerprint gate (chosen)

Zero consumer import churn — no file in `src/` or `tests/` needed an import edit, grep-verified.
Every module lands between 39 and 303 lines, so both ratchet entries are *deleted* rather than
lowered. The gate made the split safe to attempt at all, and the permanent test means the next
reader inherits a checked property instead of the folklore that blocked this for six weeks.

### Sibling modules plus a flat `harness.py` facade

The smaller diff, and briefly preferred on those grounds. Rejected for consistency: `dec-115`
and `dec-121` both established the package form in this codebase, and a facade module of pure
re-exports sitting beside its own siblings is a second pattern for the same job. Same work,
worse uniformity.

### Split only `golden.py`, leave the harness alone

Halves the risk by leaving the actual instrument untouched. Rejected once the gate passed:
the fear it hedges against was the *only* argument for treating the harness as special, and
`harness.py` is both the larger overage (441 vs 175) and the file the ledger row named first.

### Accept with rationale, keep both baselines

The option the ledger row left open. Rejected on `dec-115`'s grounds — a ceiling everything
excuses itself from is not a ceiling — and more specifically because the stated reason for the
exemption had just been disproved. Keeping it would have meant preserving an exemption whose
justification no longer existed.

## Consequences

**Positive.** Both exemptions retire outright. A future eval change lands in a 39–303 line
module beside only the stage it belongs to, rather than in a 1200-line file.

**Positive, and the durable half.** The instrument's layout-independence is now a checked
property. The deferral had been re-recorded across multiple remeasurements without ever being
tested; the test ends that class of deferral for this module.

**Negative.** Fifteen import statements replace two files' section banners. Two harness privates
(`_count_content_pages`, `_compute_held_out_delta`) and one golden private (`_locate_span`) are
re-exported from their `__init__` files purely because tests import them by those paths —
package-private, marked with redundant `as` aliases, absent from `__all__`. Honest but slightly
awkward: they are re-exports serving test convenience, not API.

**Neutral, and caught by an existing guard.** `tests/test_architecture_boundaries.py` allowlists
the golden staging write by *path* (`evals/golden.py::_write_staging`), which the split
invalidated. Its own non-vacuity guard — written to catch exactly "a future rename that silently
stopped the allowlist entry from matching" — fired and was repointed at
`evals/golden/synthesize.py`. The mypy `disallow_subclassing_any` override was likewise repointed
from `knotica.evals.harness` to `knotica.evals.harness.evaluate`, narrowed to the one module
that subclasses `dspy.Module` rather than widened to the package.

## Disconfirmation

**Falsifier.** An eval change that has to touch three or more of the seven harness modules at
once. Most plausible at the `artifacts.py` seam: if manifest rendering and the held-out delta
start versioning independently of the record, that module is two concerns wearing one name.

**Steelmanned runner-up.** Keeping both files and accepting the exemptions has the better track
record: neither had grown, both equalled their baselines, and the ratchet had already closed the
growth. The real argument for splitting is not size but that the *stated reason* for the
exemption was false — and an exemption resting on a disproved premise decays into folklore that
blocks unrelated work, which is exactly what it did here.

**Reversal trigger.** If `contract.py` proves to be the wrong golden seam — if the path helpers
and the error grammar stop changing together, or the write side starts needing its own error
types — split it into `paths` + `errors` rather than letting a third error class be declared in
two places.
