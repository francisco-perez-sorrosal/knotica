---
id: dec-070
title: Component granularity for the four un-modeled packages, and a concern-level split of core/
status: accepted
category: architectural
date: 2026-08-04
summary: "okf/, guillotine/ and service/ each become one DESIGN.md section-3 row; the 24-line dashboard loader becomes part of a Dashboard row rather than a row of its own; core/ sheds its compile chain to a new row and is re-labelled an explicit residual."
tags: [architecture-docs, component-granularity, testing, topology, tech-debt]
made_by: agent
agent_type: systems-architect
branch: feat-test-topology
pipeline_tier: standard
re_affirms: dec-069
affected_files:
  - .ai-state/DESIGN.md
  - docs/architecture.md
  - .ai-state/TECH_DEBT_LEDGER.md
dissent: "One row for guillotine/ hides a real seam: report.py is 847 lines and holds a zero-caller store write that no fitness check covers, so 'one cohesive pipeline' is a claim about intent that the file sizes do not fully support."
---

## Context

`.ai-state/DESIGN.md` §3 omitted four shipped top-level packages entirely — `src/knotica/okf/`
(11 modules), `guillotine/` (9), `service/` (3), `dashboard/` (1). Verified by grep: `okf`,
`guillotine`, and `service` appeared **zero** times in `.ai-state/DESIGN.md` *and* zero times in
`docs/architecture.md`. That is 24 modules with no component row in either architecture artifact,
while `CLAUDE.md` § Current status describes the guillotine's behavior in detail — the narrative and
structural surfaces disagreed about what the system contains.

`dec-069` bound test-topology group granularity to §3 granularity and, rather than invent
synthetic subsystem names, recorded the gap inside `TEST_TOPOLOGY.md` notes 1–3 and named the unblock:
a §3 refinement pass. Its reversal trigger (a) reads *"revisit when `DESIGN.md` §3 gains rows for any
of the four un-modeled packages."* This pass is that trigger firing, on purpose. Ledger row `td-032`
is its discoverable home (an unfinalized draft is excluded from `DECISIONS_INDEX.md` by design).

Three consequences were live at filing: 11 test files / 128 tests belonged to no topology group, so
scoped runs silently skipped them; the brownfield baseline for any future `guillotine/` or `okf/`
work started from a document that did not know those packages existed; and `src/knotica/core/` was
one row whose prose enumerated 11 modules out of 56, forcing `vault-semantics` to be a residual
group whose size was unfixable from inside the topology.

## Decision

**Each of `okf/`, `guillotine/`, and `service/` gets exactly one §3 row.** Their internal modules
form one vocabulary with several entry points, and they change together — an OKF field-set change
touches `check`, `export`, and `repair` in the same edit; the guillotine's five pipeline stages are
meaningless apart and are composed by a 148-line runner; `service/`'s two platform generators sit
behind one interface by construction. Splitting any of them would separate things that change
together, which is the coupling failure the row is supposed to prevent.

**`src/knotica/dashboard/` gets no row of its own.** A 24-line `importlib.resources` loader is a
packaging seam, not a component: it exists so one built artifact serves both mounts and so an
installed user needs no Node toolchain. It is modeled instead as part of a **Dashboard** row spanning
the repo-root Preact client and that loader. The two mounts (`mcp_server/app_ui.py`,
`mcp_server/http_app.py`) stay with `mcp_server/` — they are MCP adapters, and both import
`dashboard_html`, so the dependency runs that way and never the reverse.

**Under `core/`, one new row and two corrections:**

1. The **compile chain** (`compile_run` / `compile_state` / `compile_promote` / `compiled` /
   `query_engine` / `trainset` / `models_config` / `prompt_diff`) becomes its own row. It had no §3
   anchor at all — `programs/` described only the DSPy program, never the lifecycle around it.
2. The **loop row's Component cell is widened** to name the siblings its own body already described
   (`arena_resolve`, `loop_factory`, `candidate_gate`, `branch_namespaces`, `best_effort`) plus the
   branch/pacing family that appeared nowhere in §3 (`arena`, `branch_scoreboard`, `branch_delete`,
   `loop_promote`, `loop_retry_backoff`, `loop_cadence_config`). `baseline_probe.py` is deliberately
   left out: it writes a cold-start floor for a chart and takes no part in the gate.
3. The remaining `core/` row is **re-labelled an explicit residual** and its enumeration corrected to
   name the shared read/aggregate substrate it had been silently omitting (`status`, `doctor`,
   `metrics`, `datasets_inventory`, `golden_review`, `vault_scaffold`, `config_write`, `prompts`,
   `index_catalog`, `vault_metadata_tree`, `ingest_activity`, `text_reflow`, `errors`).

Both artifacts are updated in the same pass, since the gap existed in both.

## Considered Options

### One row per package, plus a targeted core/ split (chosen)

- **Pro:** Every §3 row corresponds to something that changes as a unit. The four packages become
  visible to the brownfield baseline and resolvable by topology `subsystems` entries.
- **Pro:** Moves roughly 4,500 lines (the compile chain and the loop siblings) out of the residual,
  so `vault-semantics` becomes describable — *the shared substrate whose consumers span groups* —
  rather than "whatever is left".
- **Con:** Widening the loop row's Component cell invalidates the verbatim string
  `TEST_TOPOLOGY.md`'s Subsystems table reproduces, so sentinel TT01 fails until `/refresh-topology`
  runs. Accepted: that refresh is already required by the five new rows.

### Decompose okf/ by verb (check / export / repair) into three rows

- **Pro:** Isolates the one mutating module, which is the only one subject to the single-writer rule
  and the reason `okf/` sits in `RAW_WRITE_PACKAGES`.
- **Con:** `export` and `repair` both import `check`, and all three import the same format model.
  Three rows would fire three groups for one conceptual edit while pretending the format vocabulary
  is shared by accident rather than by design.

### A row for `src/knotica/dashboard/` so every directory has one

- **Pro:** Mechanically closes the gap; `test_http_dashboard.py` gets an owning group with no
  judgment required.
- **Con:** Elevates a 24-line resource loader to peer status with `evals/` and the loop runtime. §3
  rows exist to describe architecture, not to unblock test groups — a row created for the second
  reason is exactly the drift `td-032` is complaining about, one level down.

### Full concern-level decomposition of core/ into five or six rows

- **Pro:** The most honest granularity; `vault-semantics` would stop being a residual at all.
- **Con:** Out of proportion to the finding. The mutation core, vault semantics, and the shared
  read/aggregate helpers genuinely do belong together — they are the substrate the other rows build
  on. Splitting them further would produce rows nobody edits independently.

## Consequences

**Positive**

- `okf/`, `guillotine/`, and `service/` are architecturally documented for the first time, in both
  the architect document and the developer guide.
- The developer guide's `Path (verified on disk)` column no longer implies `guillotine/` does not
  exist. Every path in it was re-checked on disk during this pass.
- `dec-069`'s reversal trigger (a) is discharged: `/refresh-topology` can now create
  `okf`, `guillotine`, `service`, and `dashboard` groups, plus a `query-compile` anchor that
  previously had none, and can shrink `vault-semantics` accordingly.
- §3 now records **where each non-`core` domain layer sits relative to the inward-arrow rule** as a
  small table, so the next author does not have to re-derive it from the fitness test's docstring.

**Negative**

- `sentinel` TT01 will fail against `TEST_TOPOLOGY.md` until `/refresh-topology` re-derives the
  Subsystems table — five rows added, one row's Component cell changed. This is a known, sequenced
  step, not a regression, but it is a real red window.
- The `guillotine/` row records an unguarded write (`report.py::write_artifacts` calls
  `store.write_text_atomic` outside any transaction, with zero callers) without fixing it. Documenting
  a defect is weaker than closing it; a reader could mistake the record for sanction.
- The residual `core/` row is now long. It is honest, but a row that needs a paragraph to say what it
  excludes is a row under pressure — the next growth of `core/` should split it further rather than
  extend the enumeration again.
- `Component` cells are now long enough that reproducing them verbatim in `TEST_TOPOLOGY.md` (which
  TT01 requires) is unwieldy. That cost lands on the topology, not here.

## Disconfirmation

**Falsifier.** If `/refresh-topology` cannot in fact create groups for these rows — because a
Component cell is too long or too composite to reproduce verbatim, or because the tests for
`okf/` and `guillotine/` turn out to overlap other groups' `file_dependencies` — then this pass
bought the brownfield-baseline improvement but not the coverage improvement `td-032` asked for, and
the granularity should have been chosen to fit the topology's resolution rule rather than the
architecture's seams.

**Steelmanned runner-up.** Full concern-level decomposition of `core/` is the better long-term
answer, and stopping at one split is a hedge. `core/` still holds 30-odd modules across the mutation
core, vault semantics, and a dozen read-side aggregators that share nothing but a directory; calling
that a "shared substrate" is generous. Every future pass will face the same argument with a larger
file count, and the residual will be harder to split each time — the cheapest moment to do it
properly was this one.

**Reversal trigger.** Revisit when either (a) the residual `core/` row's module count grows past
roughly 40, or a new concern lands under `core/` that no existing row plausibly claims; or (b) a
`/refresh-topology` pass reports that `vault-semantics` is still more than about a third of the
suite after these rows land, which would mean the split was aimed at the wrong seam.

## Prior Decision

`dec-069` is **re-affirmed, not superseded.** Its binding rule — a topology group's
`subsystems` entries resolve only to verbatim §3 Built components, and where §3 is silent no group is
created — was correct, and is the reason this gap was visible enough to fix rather than absorbed into
a widened glob. Nothing about that rule changes here; what changes is §3, which is precisely the
unblock that decision named. A future supersession would need evidence that the binding itself is
wrong — for example, that maintaining verbatim Component-cell reproduction across two files costs
more than the vocabulary fork it prevents.
