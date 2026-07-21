---
id: dec-draft-3fc197ba
title: Loop-internals consolidation — branch-namespace SoT, shared best-effort + arena-race + runner-factory primitives, and a credential-conditional discovery default
status: proposed
category: architectural
date: 2026-07-21
summary: Bundle the behavior-preserving loop-internals seams (single branch-namespace module, one best-effort primitive, one arena-race-and-resolve helper, one LoopRunner factory) as an in-scope refactoring phase, and make discover_on_regression default conditional on a valid discovery key — realizing dec-029's named reversal trigger; defer the candidate-gate Protocol, records-schema base, and hotspot file splits.
tags: [loop, consolidation, refactoring, branch-namespace, best-effort, arena, discovery-trigger, growth-readiness]
made_by: agent
agent_type: systems-architect
branch: worktree-loop-consolidation
pipeline_tier: full
re_affirms: dec-029
affected_files:
  - src/knotica/core/loop.py
  - src/knotica/core/source_gate.py
  - src/knotica/core/source_ingest.py
  - src/knotica/core/compile_promote.py
  - src/knotica/core/gapfill_config.py
  - src/knotica/cli/loop.py
  - src/knotica/mcp_server/tools_source_ingest.py
dissent: "Extracting four shared primitives from cohesive-but-large loop code risks a behavior-preserving refactor that subtly shifts arena or gate timing; the safest move for a working, merged loop is to leave it and only pay refactoring cost when a concrete new feature forces the seam."
---

## Context

The observe→eval→gate→heal→suggest loop is fully autonomous once started but its internals
carry duplication that resists growth (research §3–5): branch-prefix constants scattered
across three files with one-directional imports (5 prefix families, no unified module);
~60 near-identical lines between `_heal_prompts_after_regression` and `_race_then_resolve`;
five hand-written best-effort `try/except: pass|return None` sites; two `LoopRunner`
construction sites with divergent hardcoded config (a background watcher vs the synchronous
MCP-gate — autonomy risk #6). Hotspots: `loop.py` 1096 (td-008), `records.py` 947 (td-009),
`harness.py`/`golden.py` (td-002) — the ceiling rows are open and user-deferred.

Separately, `discover_on_regression` defaults to a static `False` (research autonomy gap
#1: incidental friction — a persisted genuine gap sits inert until a human runs
`knotica gapfill discover`). dec-029 chose on-demand-primary + opt-in-loop-side-off-by-
default and named the exact reversal: *"flip default once a key is reliably provisioned."*

This consolidation's goal #3 is to reshape internals for growth **without changing loop
semantics** — so these seams are a first-class deliverable, not a prerequisite for a
distinct feature (hence sequenced as an in-scope `[Phase: Refactoring]` group, not a
separate pre-refactor sub-pipeline).

## Decision

**In scope — four behavior-preserving primitives (characterization-tests-first):**

1. **`core/branch_namespaces.py`** — one module owning all five prefixes (`loop/c/`,
   `loop/r/`, `loop/x/`, `loop/wip/`, `compile/`) plus `classify_candidate` /
   `_parse_candidate_branch` / `_parse_wip_branch`. Every emitted branch string stays
   byte-identical; no test references a prefix literal directly. This also provides the
   cheap 80% of a future candidate-kind extension seam — so the full `CandidateGate`
   Protocol is **not** built now.
2. **A shared best-effort primitive** (`best_effort(...)` context manager / decorator) —
   collapses the five hand-written failure-isolation sites, swallowing exactly the same
   exceptions and taking exactly the same fallback. A future 6th hook lands clean.
3. **`_run_arena_and_resolve(candidate_branch, baseline, on_win, on_lose)`** — dedups the
   two race-and-resolve sites (~60 lines); `arena.race_variants` outcomes, promotion, and
   state transitions identical to today.
4. **`build_loop_runner(vault, topic, *, gapfill_config=None, ...)`** — unifies the two
   construction sites, **preserving each call site's current effective config values** (the
   MCP-gate keeps `arena_enabled=True` / `heuristic_arena_score`; the watcher keeps its
   CLI-driven flags). Construction is unified; value-convergence is a separate later
   decision. Closes the silent-config-drift autonomy risk.

Consequence: `loop.py` drops below its size ceiling as (1)–(3) extract from it →
td-008 addressed incidentally (the implementer flips the row when code lands).

**In scope — credential-conditional discovery default.** `discover_on_regression`
defaults to *on when a valid discovery key is present, off otherwise*, replacing the static
`False`. This **realizes dec-029's named reversal trigger**: a user who provisioned a key
gets autonomous post-classification discovery; a keyless install keeps the offline-
deterministic loop untouched. dec-029's guarantee holds because discovery stays failure-
isolated + `max_gaps`-capped even when on, and "valid key" fails closed to off.

**Deferred (named, out of scope this pass):**
- The full `CandidateGate` Protocol (`PromptGate`/`SourceGate`) — speculative until a SIA
  schema-diff candidate kind exists; the branch-namespace SoT gives the extension seam now.
- The records-schema base mixin (td-009) — respect the user ceiling-deferral; note as a
  candidate (strict byte-identical wire-format constraint if ever done).
- `harness.py`/`golden.py` splits (td-002) — untouched by the in-scope seams.
- Loop-runner config *value* convergence (vs construction unification) — separate semantic
  question.

## Considered Options

### Option A — Bundle the four primitives + conditional default now (chosen)
The seams are cohesive and mutually reinforcing (all reduce loop.py surface and enable
additive growth); one refactoring phase with one characterization net is cheaper than four
separate passes, and the loop is the highest-traffic growth axis.

### Option B — Do nothing; refactor only when a concrete feature forces a seam
Rejected: the duplication actively resists the next candidate kind / best-effort hook /
record type (research §5 "rigid"); paying the cost now, behind a characterization net,
de-risks every near-future loop feature. But its safety argument is real — hence
characterization-tests-first and value-preserving construction.

### Option C — Also build the CandidateGate Protocol + records base now
Rejected: over-designs for futures not yet needed (Incremental Evolution); the branch-ns
SoT covers the near-term extension seam and records base collides with the user ceiling
deferral.

## Consequences

**Positive:** loop internals become growth-ready (a 6th candidate kind / best-effort hook /
record type is a declarative addition, not a copy-paste); config can no longer silently
diverge between watcher and MCP-gate; loop.py returns under ceiling; discovery autonomy
improves exactly where dec-029's offline guarantee is not at risk.

**Negative:** a behavior-preserving refactor of merged, working loop code carries the risk
of a subtle timing shift (mitigated by characterization-tests-first + value-preserving
construction); the conditional default adds a credential-presence check on the loop's
config resolution.

## Disconfirmation

- **Falsifier:** if characterization tests cannot pin arena/gate outcomes byte-for-byte
  before the extraction (i.e. the behavior was never as deterministic as assumed), the
  refactor is riskier than modeled and should be staged one primitive at a time.
- **Steelmanned runner-up:** Option B — a merged, working, fully-autonomous loop is
  precisely where "if it isn't broken structurally in the reader's way, don't touch it"
  has most force; the duplication is legible and the next feature could carry its own
  extraction.
- **Reversal trigger:** if the extracted primitives accrete special-cases per call site
  (the shared helper sprouting flags), the abstraction was wrong and the sites should
  re-inline.

## Prior Decision

Re-affirms **dec-029** (discovery trigger placement). dec-029 stays `accepted`; its
on-demand-primary path and offline-loop guarantee are unchanged. This ADR only **realizes
the reversal trigger dec-029 itself named** — flipping the loop-side default from static-off
to credential-conditional now that key-presence is detectable — strengthening dec-029's
intent (autonomy without compromising the offline mandatory path) rather than reversing it.
