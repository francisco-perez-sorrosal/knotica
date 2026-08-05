<!--
  Section ownership (per skills/testing-strategy/references/test-topology.md):
  - "## Subsystems" (this file's cross-reference table + its notes) : systems-architect
  - "## Test Groups" (per-group YAML blocks)                        : test-engineer
  - `integration_boundaries` inside each group block                : implementation-planner

  Do not edit a section you do not own. Refresh via /refresh-topology (drift) or
  /refresh-topology --init (rebuild). This file is never auto-regenerated at a
  pipeline boundary.
-->

# Test Topology — Knotica

Maps the Built structural components of [`.ai-state/DESIGN.md`](DESIGN.md) §3 onto logical test
groups so that a pipeline step can run a scoped subset instead of the full suite (2520 tests /
175 test files / ~296 s wall-clock, sampled 2026-08-05).

The groups are runnable by hand, not only by pipeline agents: `make test-groups` lists them and
`make test-group GROUP=<id>` runs one, both derived from the blocks in this file. See
[Running a group](#running-a-group).

The suite is **not** flat: 153 files sit directly in `tests/`, and 22 more are nested under
`tests/core/` (1), `tests/core/notes/` (13), and `tests/discovery/` (8). Three groups therefore
select a directory rather than a file list, and any count taken with a `tests/test_*.py` glob
alone will under-report by 22.

Schema, tier vocabulary, selector registries, and closure semantics:
`skills/testing-strategy/references/test-topology.md`.

## Subsystems

Every row is a `Status: Built` component from `.ai-state/DESIGN.md` §3, mapped 1:1 onto the group
that owns its tests. Group ids are kebab-case and collision-free against the trunk reserved set
(`unit` / `integration` / `contract` / `e2e`). Component names are reproduced verbatim from the
§3 table so sentinel TT01 can resolve them.

| Component (`.ai-state/DESIGN.md` §3) | Group | Why it sits here |
|---|---|---|
| `src/knotica/store/` | `vault-substrate` | Atomic byte-level storage primitives; stdlib-only, the innermost point of the inward-arrow rule. |
| `src/knotica/search/` | `vault-substrate` | Read-only text search; depends only on store paths + the `vault_layout` leaf, so it closes the sub-DAG rather than reaching into vault semantics. |
| `src/knotica/core/vault_layout.py` | `vault-substrate` | Pure path→family classifier with zero `knotica` imports. Grouped by *concern* (paths and bytes, no vault semantics) rather than by its `core/` directory: `search.ripgrep._classify` delegates to it, and the notes-overlay Phase 0 swap showed the two change together. |
| `src/knotica/core/` | `vault-semantics` | The vault-semantics + single-mutation core: config, schema, page/links, lint, vcs, lock, scrub, records, template, `VaultTransaction`, `operations.*`. **Residual definition — see note 2.** |
| `src/knotica/core/notes/` | `notes-overlay` | The note document model, anchor history, and the resolution ladder. |
| `src/knotica/core/notes_config.py` | `notes-overlay` | The `[notes]` thresholds the ladder consumes; validated as a cross-key pair, so it is untestable apart from the ladder it parameterizes. |
| `src/knotica/core/operations/capture_note.py` | `notes-overlay` | The one-shot note write and its fidelity-degradation ladder. Constraints §7 keeps `notes/` out of every scoring surface and forbids the loop from writing into it — so the overlay is structurally isolated from the eval/loop spine and makes a clean group. |
| `src/knotica/mcp_server/` | `mcp-surface` | FastMCP tool/dispatcher/resource/prompt surface incl. §3c's seven action dispatchers and the dashboard app-UI mount. Thin and stateless by contract, so its tests are surface tests, not semantics tests. |
| Dashboard: `dashboard/` (repo root) + `src/knotica/dashboard/` | `mcp-surface` | **Mapped onto the surface group rather than given one of its own.** The Python half is a 22-line `importlib.resources` loader; the repo-root Preact tree has no pytest coverage at all, so a `dashboard/src/**` edit is not a Python test trigger under any grouping. Every Python test that exercises the loader also drives an `mcp_server` mount: `test_mcp_app_ui.py` (already `mcp-surface`) via `app_ui`, and `test_http_dashboard.py` via `http_app.create_http_app` — where two of its three tests are pure mount assertions (CORS preflight; the lost-lifespan streamable-HTTP regression). A separate group would therefore either co-claim a file `mcp-surface` owns or take a mount test away from its mount. §3 makes the same call one level up: the loader is a packaging seam, not a component, and both mounts stay with `mcp_server/`. |
| `src/knotica/cli/` | `cli-surface` | The `knotica` entry point registry. A sibling delivery surface to `mcp-surface` over the same `core.operations`; both are thin, and neither may write the vault directly. |
| `src/knotica/evals/` | `eval-harness` | Frozen-corpus evaluator: runner, judge, cache, scorer, scalar, golden set, config fingerprint. Carries the `evals` extra (`anthropic`, `dspy`) that is deliberately off the MCP launch path. |
| `src/knotica/evals/error_capture.py` | `eval-harness` | The shared leaf both `harness.py` and `scorer.py` import; it exists only to serve the harness's per-example outcome seam. |
| `src/knotica/programs/` | `query-compile` | DSPy query compile (MIPROv2 + bootstrap fallback) → compiled artifact + `CompiledRunner`. Optimization is a distinct concern from measurement: a compile-artifact change should not force a re-run of the LLM-judge suite. |
| `src/knotica/core/compile_run.py` + `compile_state.py` + `compile_promote.py` + `compiled.py` + `query_engine.py` + `trainset.py` + `models_config.py` + `prompt_diff.py` | `query-compile` | The `core/` half of the same chain: the run pipeline, its pollable state file, promote, the artifact reader, the unified `query_engine` facade, the trainset counts the gate reads, the `[models]` overrides, and the deterministic `query.md` diff. It ships and breaks with `programs/` — an artifact-format change touches the DSPy program and its lifecycle in one edit. Note 2 already subtracted these eight modules from the `vault-semantics` residual on concern grounds; **this row is the §3 anchor that subtraction previously lacked.** |
| `src/knotica/core/loop.py` + `loop_state.py` + `loop_heartbeat.py` + `loop_progress.py` + `loop_factory.py` + `loop_promote.py` + `loop_retry_backoff.py` + `loop_cadence_config.py` + `arena.py` + `arena_resolve.py` + `candidate_gate.py` + `branch_namespaces.py` + `branch_scoreboard.py` + `branch_delete.py` + `best_effort.py` | `loop-runtime` | The autonomous watcher: observe → gate → heal, together with the arena, branch and pacing siblings the §3 cell now names outright instead of leaving to prose (note 2). The most expensive group by construction — real git clones, worktrees, arena races, flock contention. |
| `src/knotica/discovery/` | `discovery-network` | Pure outbound-network boundary: no vault read/write, no state, single inward edge to `core.errors`, enforced by the `mcp_server ⊬ discovery` import-boundary test. The most cleanly dependency-closed group in the project. |
| `src/knotica/core/gap_classifier.py` + `records.GapRecord` | `gapfill-spine` | P1 — regression → fault-class diagnosis, producing the `GapRecord` queue. |
| `src/knotica/core/gapfill.py` + `records.SuggestionRecord` + `mcp_server/tools_suggestions.py` + `cli/gapfill.py` | `gapfill-spine` | P3 — gap × ranked-candidate join, suggestion queue, approval surface. |
| `src/knotica/core/source_gate.py` + `source_ingest.py` + `records.SuggestionRecord.gate_outcome` + `mcp_server/tools_source_ingest.py` + `core/operations/candidate_scope.py` + page-subset filter on `evals/train_bootstrap.py`+`evals/golden.py` | `gapfill-spine` | P4 — worktree-scoped candidate ingest and the merge-or-quarantine gate. P1/P3/P4 are three §3 rows but one hand-forward contract over shared `records.*` schemas and the `.knotica/{gaps,suggestions}` JSONL files; they change together and are meaningless apart. |
| `src/knotica/okf/` | `okf-conformance` | One format vocabulary with three verbs over it — `check` (read-only findings), `export` (bundle outside the vault), `repair` (the one module here that mutates, and only through `VaultTransaction`). Both `export` and `repair` import `check`, so an OKF field-set change touches all three in a single edit. Its adapters are thin and stay with their surface groups: `cli/okf.py` with `cli-surface`, the `vault_health` dispatcher's `okf_check`/`okf_repair` actions with `mcp-surface`. Enumerate this tree's tests **by import** — one of them carries no `okf` marker in its filename (note 1). |
| `src/knotica/guillotine/` | `guillotine-audit` | A read-only claim-trial pipeline — search → classify → score → patch → report, composed by `runner`. Deliberately **not** folded into `okf-conformance` despite the single `guillotine.report → okf.frontmatter` edge: the two answer different questions (format conformance vs. claim retraction) and change for different reasons, so sharing a group would fire an okf edit into the guillotine suite for nothing. The group also claims `core/operations/guillotine.py` — the transaction-bearing adapter §3 keeps *outside* the package precisely to hold the analysis layer inward-arrow-clean, and the module whose tests live here rather than in `vault-semantics` (note 2). |
| `src/knotica/service/` | `service-lifecycle` | Install / uninstall / status / supervise for the headless loop: two platform generators (launchd verified, systemd untested and self-reporting so) behind one interface, plus the daemon entry, with an injectable `Runner` seam that makes the `launchctl`/`systemctl` calls testable without touching the machine. Deliberately **not** folded into `loop-runtime`: §3's contract is that installing or querying the service never drags the loop runtime in (those imports are lazy, inside the supervision cycle), and `loop-runtime` is the slowest group in the project — a three-module OS-lifecycle edit has no business paying for an e2e clone-and-race suite. |
| Plugin layer (repo root) | `plugin-layer` | `.claude-plugin/`, `.mcp.json`, `commands/`, `hooks/`, `skills/`, and wheel packaging. The only group whose file dependencies live outside `src/` — a `commands/*.md` edit has no business running 2520 tests. |

**Coverage:** 23 Built components → 14 groups. The map is **total and single-valued** — every Built
component has exactly one owning group — but not injective: `vault-substrate`, `notes-overlay`,
`gapfill-spine`, `eval-harness`, `query-compile`, and `mcp-surface` each own more than one row.
`src/knotica/agent/` is `Planned` and is deliberately absent — it gets a row when it is Built.

### Note 1 — group granularity is bounded by `DESIGN.md` §3 granularity

A group's `subsystems` entries must resolve to §3 Built components (sentinel TT01), so this table
*inherits* its granularity rather than choosing it. That binding is the standing rule
(`dec-draft-4b91f4f7`); what follows is where it currently leaves us.

**The four un-modelled trees are closed.** `src/knotica/okf/`, `src/knotica/guillotine/`,
`src/knotica/service/`, and the dashboard pair each had **no §3 row at all**, so no group could
name them and their tests fell through to pipeline-tier execution. The §3 refinement pass
(`dec-draft-c20759d6`, realizing reversal trigger (a) of `dec-draft-4b91f4f7`) gave three of them a
row apiece and modelled the dashboard as one row spanning the repo-root client and its packaging
loader. All four are mapped in the table above — the first three to new groups, the dashboard onto
`mcp-surface` for the reason its row states. `td-032` stays `in-flight` until the group blocks
under `## Test Groups` exist and are measured; that is the test-engineer's half, not this table's.

**`vault-semantics` is still the largest group, and still not fixable from inside this file.**
`src/knotica/core/` remains one §3 row. It is a narrower row than it was — §3 split the compile
chain out and widened the loop row to name its fifteen siblings, which moved both clusters from
"claimed by note 2 on concern grounds" to "anchored in §3" — but what is left is a residual over a
coarse row (note 2), and splitting the group further still requires splitting the row first.

**Enumerate a tree's tests by import, never by filename glob.** `test_log_fmt.py` is the standing
proof: it covers `okf/` — it imports `knotica.okf.log_fmt` at line 5 — and its filename carries no
`okf` marker, so a `test_okf_*.py` sweep silently under-reports that tree by one file. The lesson
outlives the gap that surfaced it and applies to every group's selector list.

Do **not** invent synthetic subsystem names for anything §3 does not model — dev tooling outside
`src/knotica/` (e.g. `scripts/test_group.py`) is the live example. An unresolvable `subsystems`
entry is a TT01 FAIL, and quietly folding an unmodelled tree into another group's
`file_dependencies` makes this table lie about what it covers. The unblock is always a §3
refinement pass first, a topology edit second.

### Note 2 — `vault-semantics` is a *residual*, not a directory

Read `vault-semantics` as `src/knotica/core/` **minus** the modules other groups claim. Writing
`src/knotica/core/**` as its `file_dependencies` would swallow every other group that owns modules
under it. The claimed subtractions, by group:

| Group | Modules under `core/` it claims |
|---|---|
| `vault-substrate` | `vault_layout.py` |
| `notes-overlay` | `notes/`, `notes_config.py`, `operations/capture_note.py`, `operations/promote_note.py`, `operations/reanchor_note.py` |
| `loop-runtime` | `loop.py`, `loop_state.py`, `loop_heartbeat.py`, `loop_progress.py`, `loop_factory.py`, `loop_promote.py`, `loop_retry_backoff.py`, `loop_cadence_config.py`, `arena.py`, `arena_resolve.py`, `candidate_gate.py`, `branch_namespaces.py`, `branch_scoreboard.py`, `branch_delete.py`, `best_effort.py` |
| `query-compile` | `compile_run.py`, `compile_promote.py`, `compile_state.py`, `compiled.py`, `query_engine.py`, `models_config.py`, `prompt_diff.py`, `trainset.py` |
| `gapfill-spine` | `gap_classifier.py`, `gapfill.py`, `gapfill_config.py`, `source_gate.py`, `source_ingest.py`, `operations/candidate_scope.py` |
| `guillotine-audit` | `operations/guillotine.py` |

The `loop-runtime` and `query-compile` subtractions were originally made on concern grounds while
§3 still filed both clusters under the coarse `core/` row. §3 now carries them outright — a
compile-chain row, and a loop row widened to name all fifteen siblings — so those two rows are
**anchored, not asserted**. Grouping by concern rather than by table row is what kept
`vault-semantics` a coherent mutation-core group in the interim, and §3 has since agreed with it.

**`operations/guillotine.py` moves to `guillotine-audit`, and this is the note-2 slip pattern
again.** The module is the transaction-bearing adapter for the guillotine pipeline: §3 keeps it
outside `guillotine/` on purpose, so that the package stays a pure read-only analysis layer. Its
tests are `test_guillotine.py`, which imports `apply_guillotine` and `persist_guillotine_artifacts`
from it directly — while `vault-semantics` currently lists the module in `file_dependencies` and
runs none of those tests. Editing it therefore fires a group that cannot see it break, exactly as
`promote_note.py` / `reanchor_note.py` did before they were re-homed. The claim is recorded here;
reconciling `vault-semantics`' `file_dependencies` against it is a selector change and belongs to
the test-engineer.

`okf/` and `service/` claim nothing under `core/`: `okf.repair` owns its own `VaultTransaction`
inside the package, and `service/` only *consumes* `core.config` / `errors` / `lint` /
`loop_heartbeat`, all of which are owned elsewhere.

**All three note operations are claimed, not just `capture_note.py`.** The `notes-overlay` row
originally listed `operations/capture_note.py` alone, which left `promote_note.py` and
`reanchor_note.py` in the residual while their tests (`tests/core/notes/test_promote_note.py`,
`test_reanchor_note.py`) sat in `notes-overlay` — so editing either module fired a group that does
not run its own tests. `capture_note` / `promote_note` / `reanchor_note` are the write, lifecycle,
and re-resolution ends of one overlay contract; the §3 row names only the first because it is the
one the ladder prose describes, not because the other two are semantics-core. Splitting them was an
enumeration slip, not a judgment.

#### Deliberately unclaimed: `baseline_probe.py` and `core/metrics.py` stay in the residual

Both fall to `vault-semantics` under the residual rule, and that is the intended home — recorded
here so it is not re-opened as an oversight. Neither is loop- or eval-owned:

- **`core/metrics.py`** is the shared read path for `<topic>/.knotica/metrics.jsonl`, consumed by
  the MCP dashboard tools, the CLI, and (later) the loop runner — always through `VaultStore`.
  Three consumer groups, so no single consumer owns it. Re-homing it to any one of them would fire
  that group's tests on edit while leaving the other two consumers' tests unrun, which is strictly
  worse than the current placement: `vault-semantics` at least runs `test_core_metrics.py`, the
  tests that actually cover the module. Multi-consumer read helpers over a vault-persisted artifact
  are exactly what the mutation-core group is for.
- **`core/baseline_probe.py`** is a cold-start placeholder, not gate machinery: it persists a fixed
  `0.0` scalar so the dashboard chart has a floor before any real measurement exists. It takes no
  part in the gate decision (that is `loop_state.py` / `candidate_gate.py`), and its surface
  consumer is MCP — `test_mcp_baseline_probe.py` lives in `mcp-surface`. "Baseline" names the value
  it writes, not the loop concern it serves.

This tightens the residual's definition rather than merely tolerating it: `vault-semantics` is not
"whatever is left over" but **the shared core substrate other groups build on** — modules whose
consumers span groups, and which therefore belong to none of them. A module leaves the residual
only when one concern owns both its changes and its tests. Since module and test already agree for
both of these, the placement needs no `file_dependencies` or selector change in the test-engineer's
section.

### Note 3 — cross-cutting fitness tests belong to no group

`test_architecture_boundaries.py`, `test_file_size_ratchet.py`, `test_spine.py`,
`test_server_tool_surface.py`, `test_tool_description_guards.py`, `test_template.py`, and
`test_vault_targeting.py` assert invariants *across* subsystem boundaries (import direction, file
ceilings, spine coherence, tool-surface shape, packaging). By construction they have no single
owning subsystem, and the trunk is explicit that cross-cutting capabilities make no sound
test-group boundary — so they are deliberately un-grouped.

They are also cheap (import-graph walks, `stat` calls, markdown scans) and they are precisely the
tests a scoped run is blind to. Whether to pin them into every scoped invocation or leave them to
pipeline tier is a selector decision and belongs to the test-engineer; this note only records that
the omission is intentional, not an oversight.

## Test Groups

Fourteen groups, one per row-owner in the table above. Every block was authored against the live
suite and every `selectors` entry was executed before it was written down — see
`.ai-work/test-topology-init/TEST_RESULTS.md` for the original eleven and
`.ai-work/sentinel-remediation/TEST_RESULTS.md` for the three added when §3 first modelled `okf/`,
`guillotine/`, and `service/`.

### Running a group

`scripts/test_group.py` *derives* the pytest invocation from the blocks below rather than restating
them:

| Command | Effect |
|---|---|
| `make test-groups` | List every group with its tier and test-file count |
| `make test-group GROUP=<id> [ARGS="-x -q"]` | Run one group; `ARGS` is forwarded to pytest |
| `make verify` | Runs `scripts/test_group.py --check` first, ahead of mypy / pytest / ruff |

Both targets go through `uv run --extra evals`, matching `verify` — `eval-harness` imports
`anthropic`/`dspy` for real, and the script itself needs an environment with `pyyaml` to parse this
file. Exit codes: `0` success, `1` a failed check or failed pytest (pytest's own code is propagated
unchanged), `2` usage — an unknown group prints the valid ids. `--help` covers flag detail.

`--check` validates this file against the filesystem: every selector arg exists on disk and holds no
wildcard, every required trunk field is present, every `file_dependencies` glob matches at least one
real path, and no group id repeats. It also cross-checks completeness — the ids it parsed from the
YAML blocks must match the ids the `## Subsystems` table declares as an independent declaration, in
both directions (a table row with no block, a block with no table row) — which lifts the guarantee
from "the blocks I found are valid" to "…and they are all of them", so a parser regression matching
4 of 14 blocks now fails instead of printing `topology check OK — 4 groups` and reading as a pass.
The same cross-check is what caught this section lagging §3's refinement pass: the architect's table
gained `okf-conformance` / `guillotine-audit` / `service-lifecycle` rows and `make verify` went red
until the matching blocks below existed.
It refuses any selector `strategy` other than `pytest-globs` rather than guessing an invocation.

Copying group membership into `pyproject.toml` was the alternative, and was rejected: this file is
what sentinel audits (TT01–TT06) and what `/refresh-topology` regenerates, so a second copy would
leave the audited file no longer authoritative.

### Why `pytest-globs` with explicit paths

`pytest-markers` is unavailable without a change no topology file is allowed to make: this project
has no `[tool.pytest.ini_options]` block at all (markers are registered from `tests/conftest.py`
via `pytest_configure`, and only `slow` is registered), so every group marker would have to be
added to ~150 existing test files plus a new pyproject section. `pytest-keywords` matches
substrings of node ids and would silently over-select. `pytest-globs` needs neither.

The `arg` lists hold **literal paths, never wildcards**. Verified: pytest does not expand globs
itself — `pytest "tests/test_evals_ca*.py"` exits 4 with `file or directory not found`. A wildcard
in this file would therefore only work when the runner happens to go through a shell. Literal
paths work under `subprocess.run(..., shell=False)` too, and they make group membership auditable:
the un-grouped set below is provably the complement of the fourteen `arg` lists, not an accident.

The cost is drift, and it is caught at two speeds. The fast path is `make verify`, which runs
`scripts/test_group.py --check` before mypy / pytest / ruff: a selector pointing at a renamed or
deleted file fails at the developer's own gate, immediately. What `--check` cannot see is a
genuinely *new* test file that no group claims — every selector still resolves, so the check passes
while the file sits outside the topology. That residual case is what the `/refresh-topology` drift
path and sentinel TT03 exist for: the slower, broader backstop.

### Fixtures, parallelism, and shared state

`tests/conftest.py` builds one **session-scoped** `vault_seed` (template + `git init` + initial
commit) that `template_vault` / `vault_config` copy per test. Every group except
`discovery-network` reaches that fixture, hence `shared_fixture_scope: per-suite` almost
everywhere. It needs **no** filelock under xdist: the seed is created through
`tmp_path_factory.mktemp`, which is worker-local, so workers build independent seeds rather than
racing for one path.

`parallel_safe: true` on all fourteen groups is measured, not assumed — each group was re-run under
`-n 4 --dist loadfile` and passed with an identical test count (`pytest-xdist` is not a project
dependency; verification used an ephemeral `uv run --with pytest-xdist` environment, which is
itself occasionally flaky at worker start-up — that flakiness is the harness, not the tests).
`--dist loadfile` is the required distribution mode: several files share module-scoped state.

`shared_state: tmp_path` holds for every group — no test touches a shared filesystem location, a
real network, or an external service; the only repo-root reads (`test_file_size_ratchet.py`,
`plugin-layer`) are read-only.

### `vault-substrate`

```yaml
id: vault-substrate
title: Vault substrate — bytes, paths, and read-only search
subsystems:
  - "src/knotica/store/"
  - "src/knotica/search/"
  - "src/knotica/core/vault_layout.py"
tier: integration
selectors:
  - strategy: pytest-globs
    arg:
      - tests/test_file_size_ratchet.py
      - tests/test_query_retrieval.py
      - tests/test_search.py
      - tests/test_store.py
      - tests/test_vault_layout.py
file_dependencies:
  - "src/knotica/store/**"
  - "src/knotica/search/**"
  - "src/knotica/core/vault_layout.py"
integration_boundaries: []   # planner-owned; populates lazily in later pipelines
parallel_safe: true
shared_fixture_scope: per-suite
shared_state: tmp_path
notes: >-
  Fastest group in the project (161 tests / 1.6 s); real filesystem I/O and a ripgrep
  subprocess keep it off the `unit` tier despite the speed.
```

### `vault-semantics`

```yaml
id: vault-semantics
title: Vault semantics — config, schema, lint, and the single mutation path
subsystems:
  - "src/knotica/core/"
tier: integration
selectors:
  - strategy: pytest-globs
    arg:
      - tests/test_architecture_boundaries.py
      - tests/test_baseline_probe.py
      - tests/test_config.py
      - tests/test_config_write.py
      - tests/test_core_jsonl.py
      - tests/test_core_metrics.py
      - tests/test_core_topics.py
      - tests/test_datasets_inventory.py
      - tests/test_errors.py
      - tests/test_eval_characterization.py
      - tests/test_file_size_ratchet.py
      - tests/test_ingest_activity.py
      - tests/test_links.py
      - tests/test_lint.py
      - tests/test_lock.py
      - tests/test_op_create_topic.py
      - tests/test_op_curate_example.py
      - tests/test_op_store_source.py
      - tests/test_op_write_page.py
      - tests/test_page.py
      - tests/test_prompts.py
      - tests/test_records.py
      - tests/test_schema.py
      - tests/test_scrub.py
      - tests/test_text_reflow.py
      - tests/test_transaction.py
      - tests/test_vault_metadata_tree.py
      - tests/test_vault_scaffold.py
      - tests/test_vcs.py
file_dependencies:
  - "src/knotica/core/__init__.py"
  - "src/knotica/core/baseline_probe.py"
  - "src/knotica/core/config.py"
  - "src/knotica/core/config_write.py"
  - "src/knotica/core/datasets_inventory.py"
  - "src/knotica/core/doctor.py"
  - "src/knotica/core/errors.py"
  - "src/knotica/core/golden_review.py"
  - "src/knotica/core/index_catalog.py"
  - "src/knotica/core/ingest_activity.py"
  - "src/knotica/core/jsonl.py"
  - "src/knotica/core/links.py"
  - "src/knotica/core/lint.py"
  - "src/knotica/core/lock.py"
  - "src/knotica/core/metrics.py"
  - "src/knotica/core/page.py"
  - "src/knotica/core/prompts.py"
  - "src/knotica/core/records.py"
  - "src/knotica/core/schema.py"
  - "src/knotica/core/scrub.py"
  - "src/knotica/core/status.py"
  - "src/knotica/core/template.py"
  - "src/knotica/core/text_reflow.py"
  - "src/knotica/core/topics.py"
  - "src/knotica/core/transaction.py"
  - "src/knotica/core/vault_metadata_tree.py"
  - "src/knotica/core/vault_scaffold.py"
  - "src/knotica/core/vcs.py"
  - "src/knotica/core/operations/__init__.py"
  - "src/knotica/core/operations/create_topic.py"
  - "src/knotica/core/operations/curate_example.py"
  - "src/knotica/core/operations/doctor_repair.py"
  - "src/knotica/core/operations/migrate.py"
  - "src/knotica/core/operations/reflow_sources.py"
  - "src/knotica/core/operations/store_source.py"
  - "src/knotica/core/operations/write_page.py"
integration_boundaries: []   # planner-owned; populates lazily in later pipelines
parallel_safe: true
shared_fixture_scope: per-suite
shared_state: tmp_path
notes: >-
  Enumerated module-by-module on purpose: this group is the `core/` residual of Note 2, so a
  `src/knotica/core/**` glob here would swallow five other groups whole.
  `operations/guillotine.py` was dropped from this list when `guillotine-audit` landed: Note 2
  reassigns it there, and the tests that cover it (`test_guillotine.py`) never ran here.
```

### `notes-overlay`

```yaml
id: notes-overlay
title: Notes overlay — note model, anchor history, resolution ladder
subsystems:
  - "src/knotica/core/notes/"
  - "src/knotica/core/notes_config.py"
  - "src/knotica/core/operations/capture_note.py"
tier: integration
selectors:
  - strategy: pytest-globs
    arg:
      - tests/core/notes/
      - tests/core/test_status_notes.py
      - tests/test_file_size_ratchet.py
      - tests/test_notes_config.py
      - tests/test_score_isolation_characterization.py
file_dependencies:
  - "src/knotica/core/notes/**"
  - "src/knotica/core/notes_config.py"
  - "src/knotica/core/operations/capture_note.py"
  - "src/knotica/core/operations/promote_note.py"
  - "src/knotica/core/operations/reanchor_note.py"
integration_boundaries: []   # planner-owned; populates lazily in later pipelines
parallel_safe: true
shared_fixture_scope: per-suite
shared_state: tmp_path
notes: >-
  Also homes the two notes-isolation standing guards
  (`test_score_isolation_characterization.py`, `tests/core/notes/test_contamination.py`) even
  though they execute `core/lint.py` and `evals/harness.py` — the invariant they defend is the
  overlay's, so a `notes/` change is what must re-run them.
```

### `mcp-surface`

```yaml
id: mcp-surface
title: MCP surface — FastMCP tools, dispatchers, resources, prompts
subsystems:
  - "src/knotica/mcp_server/"
  - "Dashboard: dashboard/ (repo root) + src/knotica/dashboard/"
tier: integration
selectors:
  - strategy: pytest-globs
    arg:
      - tests/test_architecture_boundaries.py
      - tests/test_decision_envelope.py
      - tests/test_dispatch_arena.py
      - tests/test_dispatch_branches.py
      - tests/test_dispatch_compile.py
      - tests/test_dispatch_datasets.py
      - tests/test_dispatch_golden.py
      - tests/test_dispatch_loop.py
      - tests/test_dispatch_vault.py
      - tests/test_dispatch_vault_health.py
      - tests/test_file_size_ratchet.py
      - tests/test_http_dashboard.py
      - tests/test_loop_dispatch_cadence_run_eval.py
      - tests/test_loop_dispatch_run_once.py
      - tests/test_mcp_app_ui.py
      - tests/test_mcp_arena.py
      - tests/test_mcp_baseline_probe.py
      - tests/test_mcp_compile.py
      - tests/test_mcp_datasets.py
      - tests/test_mcp_golden.py
      - tests/test_mcp_guide.py
      - tests/test_mcp_notes.py
      - tests/test_mcp_notes_drift_cause.py
      - tests/test_mcp_prompt_diff.py
      - tests/test_mcp_prompts.py
      - tests/test_mcp_query.py
      - tests/test_mcp_read.py
      - tests/test_mcp_resources.py
      - tests/test_mcp_status.py
      - tests/test_mcp_vault.py
      - tests/test_mcp_write.py
      - tests/test_server_instructions.py
      - tests/test_server_tool_surface.py
      - tests/test_tool_description_guards.py
      - tests/test_vault_targeting.py
      - tests/test_wiki_status_gaps.py
      - tests/test_wiki_status_scope_view.py
      - tests/test_wiki_status_suggestions.py
file_dependencies:
  - "src/knotica/mcp_server/**"
  - "src/knotica/dashboard/**"
integration_boundaries: []   # planner-owned; populates lazily in later pipelines
parallel_safe: true
shared_fixture_scope: per-suite
shared_state: tmp_path
notes: >-
  The glob deliberately overlaps `gapfill-spine` on `tools_suggestions.py` and
  `tools_source_ingest.py`: a change there should fire both the surface tests and the spine
  tests. The Dashboard subsystem contributes only its *Python* half to
  `file_dependencies` — `src/knotica/dashboard/**`, the `importlib.resources` loader. The
  repo-root `dashboard/` Preact tree is named in the §3 row and so in `subsystems`, but is
  deliberately **not** a file dependency: the table's own reasoning is that it has no pytest
  coverage at all, so listing it would claim a `dashboard/src/**` edit triggers a 366-test
  Python run that could not observe the change. `test_http_dashboard.py` joins this group
  because two of its three tests are pure `create_http_app` mount assertions (CORS preflight;
  the lost-lifespan streamable-HTTP regression).
```

### `cli-surface`

```yaml
id: cli-surface
title: CLI surface — the `knotica` entry-point registry
subsystems:
  - "src/knotica/cli/"
tier: integration
selectors:
  - strategy: pytest-globs
    arg:
      - tests/test_architecture_boundaries.py
      - tests/test_cli_desktop.py
      - tests/test_cli_doctor.py
      - tests/test_cli_eval.py
      - tests/test_cli_init.py
      - tests/test_cli_loop.py
      - tests/test_cli_mcp.py
      - tests/test_cli_migrate.py
      - tests/test_cli_prompt.py
      - tests/test_cli_status.py
      - tests/test_file_size_ratchet.py
      - tests/test_status_nudge.py
file_dependencies:
  - "src/knotica/cli/**"
integration_boundaries: []   # planner-owned; populates lazily in later pipelines
parallel_safe: true
shared_fixture_scope: per-suite
shared_state: tmp_path
notes: >-
  The glob covers `cli/gapfill.py` (also claimed by `gapfill-spine`) and `cli/service.py` /
  `cli/okf.py` / `cli/guillotine.py`, whose tests are un-grouped — see the un-grouped
  subsection below.
```

### `eval-harness`

```yaml
id: eval-harness
title: Eval harness — frozen-corpus runner, judge, cache, scorer, scalar
subsystems:
  - "src/knotica/evals/"
  - "src/knotica/evals/error_capture.py"
tier: integration
selectors:
  - strategy: pytest-globs
    arg:
      - tests/test_architecture_boundaries.py
      - tests/test_evals_cache.py
      - tests/test_evals_citations.py
      - tests/test_evals_config.py
      - tests/test_evals_error_capture.py
      - tests/test_evals_golden.py
      - tests/test_evals_harness.py
      - tests/test_evals_judge.py
      - tests/test_evals_llm.py
      - tests/test_evals_llm_completeness.py
      - tests/test_evals_program.py
      - tests/test_evals_runner.py
      - tests/test_evals_scalar.py
      - tests/test_evals_scorer.py
      - tests/test_file_size_ratchet.py
      - tests/test_llm_temperature_conditionalization.py
      - tests/test_models_harness_fingerprint.py
      - tests/test_train_bootstrap.py
file_dependencies:
  - "src/knotica/evals/**"
integration_boundaries: []   # planner-owned; populates lazily in later pipelines
parallel_safe: true
shared_fixture_scope: per-suite
shared_state: tmp_path
notes: >-
  Owns `evals/train_bootstrap.py` and `evals/golden.py` outright; `gapfill-spine` holds only a
  partial (page-subset filter) interest in them per the DESIGN.md P4 row.
```

### `query-compile`

```yaml
id: query-compile
title: Query compile — DSPy optimization and the compiled artifact chain
subsystems:
  - "src/knotica/programs/"
  - "src/knotica/core/compile_run.py + compile_state.py + compile_promote.py + compiled.py + query_engine.py + trainset.py + models_config.py + prompt_diff.py"
tier: integration
selectors:
  - strategy: pytest-globs
    arg:
      - tests/test_file_size_ratchet.py
      - tests/test_models_config.py
      - tests/test_phase3a_compile.py
      - tests/test_prompt_diff.py
      - tests/test_query_engine.py
      - tests/test_query_model_config.py
file_dependencies:
  - "src/knotica/programs/**"
  - "src/knotica/core/compile_run.py"
  - "src/knotica/core/compile_promote.py"
  - "src/knotica/core/compile_state.py"
  - "src/knotica/core/compiled.py"
  - "src/knotica/core/query_engine.py"
  - "src/knotica/core/models_config.py"
  - "src/knotica/core/prompt_diff.py"
  - "src/knotica/core/trainset.py"
integration_boundaries: []   # planner-owned; populates lazily in later pipelines
parallel_safe: true
shared_fixture_scope: per-suite
shared_state: tmp_path
notes: >-
  `evals/compiled_runner.py` and `evals/program.py` stay with `eval-harness` (directory
  ownership) even though the DESIGN.md prose for this row names CompiledRunner. The eight
  `core/` modules in `file_dependencies` predate their `subsystems` entry: they were claimed on
  concern grounds under Note 2 while §3 still filed them under the coarse `core/` row, and the
  second `subsystems` entry is the §3 anchor arriving after the fact — not a widening.
```

### `loop-runtime`

```yaml
id: loop-runtime
title: Loop runtime — the autonomous observe / gate / heal watcher
subsystems:
  - "src/knotica/core/loop.py + loop_state.py + loop_heartbeat.py + loop_progress.py + loop_factory.py + loop_promote.py + loop_retry_backoff.py + loop_cadence_config.py + arena.py + arena_resolve.py + candidate_gate.py + branch_namespaces.py + branch_scoreboard.py + branch_delete.py + best_effort.py"
tier: e2e
selectors:
  - strategy: pytest-globs
    arg:
      - tests/test_arena.py
      - tests/test_arena_race_characterization.py
      - tests/test_best_effort_characterization.py
      - tests/test_branch_namespaces_characterization.py
      - tests/test_branch_scoreboard.py
      - tests/test_candidate_gate_characterization.py
      - tests/test_file_size_ratchet.py
      - tests/test_loop_attempt.py
      - tests/test_loop_blocked_failure_backoff.py
      - tests/test_loop_cadence.py
      - tests/test_loop_cadence_characterization.py
      - tests/test_loop_cadence_config.py
      - tests/test_loop_content_classification.py
      - tests/test_loop_eval_error_visibility.py
      - tests/test_loop_factory_cadence_wiring.py
      - tests/test_loop_flock_contention.py
      - tests/test_loop_noop_attempt_characterization.py
      - tests/test_loop_progress.py
      - tests/test_loop_runner.py
      - tests/test_loop_runner_factory_characterization.py
      - tests/test_loop_state.py
      - tests/test_loop_state_additive_fields.py
      - tests/test_td011_eval_rearm.py
file_dependencies:
  - "src/knotica/core/loop.py"
  - "src/knotica/core/loop_state.py"
  - "src/knotica/core/loop_heartbeat.py"
  - "src/knotica/core/loop_progress.py"
  - "src/knotica/core/loop_factory.py"
  - "src/knotica/core/loop_promote.py"
  - "src/knotica/core/loop_retry_backoff.py"
  - "src/knotica/core/loop_cadence_config.py"
  - "src/knotica/core/loop_attempt.py"
  - "src/knotica/core/arena.py"
  - "src/knotica/core/arena_resolve.py"
  - "src/knotica/core/candidate_gate.py"
  - "src/knotica/core/branch_namespaces.py"
  - "src/knotica/core/branch_scoreboard.py"
  - "src/knotica/core/branch_delete.py"
  - "src/knotica/core/best_effort.py"
integration_boundaries: []   # planner-owned; populates lazily in later pipelines
parallel_safe: true
shared_fixture_scope: per-suite
shared_state: tmp_path
notes: >-
  `e2e` rather than `integration`: these tests drive the deployed daemon's full stack — real
  git clones, worktrees, arena races, flock contention — and are the slowest group by a factor
  of two (96.6 s sequential).
```

### `discovery-network`

```yaml
id: discovery-network
title: Discovery network — the outbound source-search boundary
subsystems:
  - "src/knotica/discovery/"
tier: unit
selectors:
  - strategy: pytest-globs
    arg:
      - tests/discovery/
      - tests/test_discovery_import_boundary.py
      - tests/test_file_size_ratchet.py
file_dependencies:
  - "src/knotica/discovery/**"
integration_boundaries: []   # planner-owned; populates lazily in later pipelines
parallel_safe: true
shared_fixture_scope: per-test
shared_state: tmp_path
notes: >-
  The only `unit`-tier group: no vault fixture, no real network (HTTP is stubbed), single
  inward edge to `core.errors`. 130 tests in 1.3 s.
```

### `gapfill-spine`

```yaml
id: gapfill-spine
title: Gap-fill spine — diagnose, discover, queue, gate
subsystems:
  - "src/knotica/core/gap_classifier.py + records.GapRecord"
  - "src/knotica/core/gapfill.py + records.SuggestionRecord + mcp_server/tools_suggestions.py + cli/gapfill.py"
  - "src/knotica/core/source_gate.py + source_ingest.py + records.SuggestionRecord.gate_outcome + mcp_server/tools_source_ingest.py + core/operations/candidate_scope.py + page-subset filter on evals/train_bootstrap.py+evals/golden.py"
tier: integration
selectors:
  - strategy: pytest-globs
    arg:
      - tests/test_cli_gapfill.py
      - tests/test_file_size_ratchet.py
      - tests/test_gap_classifier.py
      - tests/test_gapfill.py
      - tests/test_gapfill_discovery_default.py
      - tests/test_gapfill_integration.py
      - tests/test_loop_gapfill_hook.py
      - tests/test_mcp_source_ingest.py
      - tests/test_mcp_suggestions.py
      - tests/test_records_gap.py
      - tests/test_records_suggestion.py
      - tests/test_source_gate.py
      - tests/test_source_ingest.py
file_dependencies:
  - "src/knotica/core/gap_classifier.py"
  - "src/knotica/core/gapfill.py"
  - "src/knotica/core/gapfill_config.py"
  - "src/knotica/core/source_gate.py"
  - "src/knotica/core/source_ingest.py"
  - "src/knotica/core/operations/candidate_scope.py"
  - "src/knotica/mcp_server/tools_suggestions.py"
  - "src/knotica/mcp_server/tools_source_ingest.py"
  - "src/knotica/cli/gapfill.py"
integration_boundaries: []   # planner-owned; populates lazily in later pipelines
parallel_safe: true
shared_fixture_scope: per-suite
shared_state: tmp_path
notes: >-
  Homes `test_loop_gapfill_hook.py` and `test_gapfill_discovery_default.py` despite their
  `loop`-flavoured names: both pin `[gapfill] discover_on_regression` behaviour read out of
  `core/gapfill_config.py`.
```

### `okf-conformance`

```yaml
id: okf-conformance
title: OKF conformance — check, export, repair over one format vocabulary
subsystems:
  - "src/knotica/okf/"
tier: integration
selectors:
  - strategy: pytest-globs
    arg:
      - tests/test_architecture_boundaries.py
      - tests/test_file_size_ratchet.py
      - tests/test_log_fmt.py
      - tests/test_okf_cli.py
      - tests/test_okf_frontmatter.py
      - tests/test_okf_links.py
      - tests/test_okf_notes_isolation.py
      - tests/test_okf_repair_characterization.py
file_dependencies:
  - "src/knotica/okf/**"
integration_boundaries: []   # planner-owned; populates lazily in later pipelines
parallel_safe: true
shared_fixture_scope: per-suite
shared_state: tmp_path
notes: >-
  Membership was enumerated **by import**, not by filename: `test_log_fmt.py` imports
  `knotica.okf.log_fmt` and carries no `okf` marker in its name, so a `test_okf_*.py` glob
  under-reports this tree by one file (Note 1's standing proof). `test_okf_cli.py` is the
  mirror-image trap — it is named for a CLI it never drives, importing `okf.check` /
  `okf.export` / `okf.repair` directly, so it belongs here rather than to `cli-surface`.
  `test_architecture_boundaries.py` is pinned in because `okf/` is a named member of that
  file's `RAW_WRITE_PACKAGES` scan (td-020: `repair.py` mutates the live vault and must stay on
  the `core.transaction` path) — this is the fifth group to pin it, and the pin is a scan
  membership, not a courtesy. `test_okf_notes_isolation.py` stays here rather than in
  `notes-overlay` for the same by-import rule: the invariant is the overlay's, but the
  regression it pins was an `okf/` bug and an `okf/` edit is what must re-run it — the overlay
  side already has `tests/core/notes/test_contamination.py`.
```

### `guillotine-audit`

```yaml
id: guillotine-audit
title: Guillotine audit — claim trial, verdict, risk report, gap filing
subsystems:
  - "src/knotica/guillotine/"
tier: integration
selectors:
  - strategy: pytest-globs
    arg:
      - tests/test_file_size_ratchet.py
      - tests/test_guillotine.py
file_dependencies:
  - "src/knotica/guillotine/**"
  - "src/knotica/core/operations/guillotine.py"
integration_boundaries: []   # planner-owned; populates lazily in later pipelines
parallel_safe: true
shared_fixture_scope: per-suite
shared_state: tmp_path
notes: >-
  Claims `core/operations/guillotine.py`, which §3 keeps *outside* the package so the analysis
  layer stays read-only and inward-arrow-clean; Note 2 records the reassignment and
  `vault-semantics` dropped it in the same edit. The single test file is dense (39 tests) and
  drives real git commits plus a ripgrep search, hence `integration`. `test_gapfill_integration.py`
  also imports `run_guillotine` and `apply_guillotine` but stays sole-owned by `gapfill-spine`:
  it is one cross-spine flywheel test (2.9 s) in which the guillotine is a *step*, and this
  group already covers the guillotine's own contract 39 ways. That is the same shape
  `mcp-surface` accepts on `tools_suggestions.py` — a file dependency whose extra coverage lives
  in a sibling group — not the Note 2 slip pattern, which is a module with *no* tests in its
  owning group.
```

### `service-lifecycle`

```yaml
id: service-lifecycle
title: Service lifecycle — install / uninstall / status / supervise the loop daemon
subsystems:
  - "src/knotica/service/"
tier: integration
selectors:
  - strategy: pytest-globs
    arg:
      - tests/test_cli_service.py
      - tests/test_file_size_ratchet.py
      - tests/test_service_daemon_env.py
      - tests/test_service_manager.py
file_dependencies:
  - "src/knotica/service/**"
integration_boundaries: []   # planner-owned; populates lazily in later pipelines
parallel_safe: true
shared_fixture_scope: per-suite
shared_state: tmp_path
notes: >-
  Cheapest non-`unit` group in the project (55 tests / 2.2 s) and the clearest case for §3's
  refusal to fold `service/` into `loop-runtime`: the same edit would otherwise pay
  `loop-runtime`'s 96.6 s of clones and arena races. `integration` rather than `unit` despite the
  speed — an autouse fixture forbids a *real* `subprocess.run` (so no `launchctl`/`systemctl`
  ever fires), but the tests still write real plists and `.env` files under `tmp_path` and
  `test_cli_service.py` reaches the session `vault_config` fixture, which is what sets
  `shared_fixture_scope: per-suite`. `test_cli_service.py` sits here rather than in
  `cli-surface` by the by-import rule: it drives `knotica.cli.main` only to reach
  `knotica.service.manager`, and a `service/` edit is what breaks it. That leaves `cli/service.py`
  covered by `cli-surface`'s glob without its own test — see the divergence subsection below.
```

### `plugin-layer`

```yaml
id: plugin-layer
title: Plugin layer — shipped manifest, hooks, commands, packaging
subsystems:
  - "Plugin layer (repo root)"
tier: contract
selectors:
  - strategy: pytest-globs
    arg:
      - tests/test_file_size_ratchet.py
      - tests/test_hooks_session_start.py
      - tests/test_packaging_evals_extra.py
      - tests/test_template.py
file_dependencies:
  - ".claude-plugin/**"
  - ".mcp.json"
  - "commands/**"
  - "hooks/**"
  - "skills/**"
  - "pyproject.toml"
  - "vault-template/**"
integration_boundaries: []   # planner-owned; populates lazily in later pipelines
parallel_safe: true
shared_fixture_scope: per-suite
shared_state: tmp_path
notes: >-
  `contract` tier because its tests pin agreements between shipped declarations and the launch
  paths that consume them (the `evals` extra vs the Desktop argv; the template inventory vs
  the instantiated vault).
```

### Verified runtimes (single sample each)

Baseline for comparison: full suite 2520 passed; the ~296 s wall-clock is the last full-suite
sample, taken at 2520 tests on 2026-08-05.

Rows marked ‡ were re-measured after the duplicate-consolidation and `td-031` passes, which gave
`vault-semantics` and `loop-runtime` two new test files each. Rows marked † are the 2026-08-05
fourteen-group sample; the rest are the 2026-08-04 originals.

Four unmarked rows moved anyway, by exactly +2 tests each — `mcp-surface`, `cli-surface`,
`eval-harness`, `okf-conformance`. None of their `arg` lists changed; the pinned
`test_architecture_boundaries.py` did, growing 10 → 12 (one clause renamed, two added) when the
sole-writer scan widened from store adapters to `core.transaction`. A pinned fitness file is shared
by construction, so its growth propagates to every group that pins it — the fifth such group,
`vault-semantics`, absorbs the same +2 inside its ‡ re-measurement. Their Tests column is corrected
here; their runtimes are not re-sampled, a two-test fitness addition sitting inside single-sample
noise.

| Group | Files | Tests | Sequential | `-n 4 --dist loadfile` |
|---|---:|---:|---:|---:|
| `vault-substrate` | 5 | 161 | 1.6 s | 2.7 s |
| `vault-semantics` ‡ | 29 | 569 | 26.6 s | 13.3 s |
| `notes-overlay` | 17 | 316 | 23.5 s | 9.7 s |
| `mcp-surface` † | 38 | 366 | 44.1 s | 19.4 s |
| `cli-surface` | 12 | 106 | 19.6 s | 10.0 s |
| `eval-harness` | 18 | 332 | 20.4 s | 17.0 s |
| `query-compile` | 6 | 48 | 9.8 s | 8.7 s |
| `loop-runtime` ‡ | 23 | 198 | 96.6 s | 32.0 s |
| `discovery-network` | 10 | 130 | 1.3 s | 2.6 s |
| `gapfill-spine` | 13 | 196 | 43.5 s | 17.3 s |
| `okf-conformance` † | 8 | 55 | 6.0 s | 3.9 s |
| `guillotine-audit` † | 2 | 45 | 6.2 s | 7.2 s |
| `service-lifecycle` † | 4 | 55 | 2.2 s | 0.9 s |
| `plugin-layer` | 4 | 29 | 4.8 s | 2.1 s |

The column sums to 2606 against 2480 unique, and the 126-test excess is exactly the pinned overlap:
`test_file_size_ratchet.py` counted in all 14 groups (13 × 6 = 78) and `test_architecture_boundaries.py`
in 5 (4 × 12 = 48). That identity is the table's own arithmetic check — it is what forced the four
unmarked corrections above, since leaving them at their pre-widening values would have made the
column contradict the partition check below.

The three groups added when §3 first modelled `okf/`, `guillotine/`, and `service/` are cheap — 155
tests for 14.4 s sequential between them, against the ~296 s
they cost at pipeline tier before they had blocks. `guillotine-audit` is the one group that is
*slower* under `-n 4` (7.2 s vs 6.2 s): 45 of its tests sit in a single file, so `--dist loadfile`
puts them all on one worker and the run pays worker start-up for nothing. It is still
`parallel_safe: true` — the count is identical and the mode is correct, it just buys nothing here.

`expected_runtime_envelope` is deliberately omitted: one sample per group is a measurement, not a
p50/p95 distribution, and the trunk makes the field optional until M3 precisely so nobody invents
one.

### Cross-cutting fitness tests — the selector decision Note 3 defers here

Note 3 leaves the disposition of the seven un-grouped fitness tests to this section. Blanket-pinning
all seven into all fourteen groups was rejected on measurement: `test_spine.py` alone costs ~16 s,
more than ten of the fourteen groups cost in total. Each file is instead pinned to the groups whose
`file_dependencies` can actually break it:

| Fitness test | Cost | Pinned into | Why |
|---|---:|---|---|
| `test_file_size_ratchet.py` | 0.1 s | all 14 groups | Its trigger is literally "a file under `src/` grew"; no group can be exempt and the cost is noise. |
| `test_architecture_boundaries.py` | 0.8 s | `vault-semantics`, `mcp-surface`, `cli-surface`, `eval-harness`, `okf-conformance` | It AST-scans exactly those five trees: `ADAPTER_PACKAGES` (`cli`, `mcp_server`, `evals`) for the adapter-boundary clauses, plus `okf` — `RAW_WRITE_PACKAGES` is `ADAPTER_PACKAGES + ("okf",)` because `okf/repair.py` mutates the live vault (td-020). |
| `test_server_tool_surface.py` | — | `mcp-surface` | Asserts the shape of the MCP tool surface. |
| `test_tool_description_guards.py` | — | `mcp-surface` | Asserts MCP tool description content. |
| `test_vault_targeting.py` | — | `mcp-surface` | Asserts the per-call `vault` selector reaches `config.resolve` through the tools. |
| `test_template.py` | 0.6 s | `plugin-layer` | Validates the shipped `vault-template/` inventory, which is a `plugin-layer` file dependency. |
| `test_spine.py` | 16.6 s | **none** | Its trigger is `tests/conftest.py` + `tests/support/` — test infrastructure, which no group's `file_dependencies` covers. Runs at pipeline tier. |

Pinned files are counted once in the runtime table above and are the only deliberate overlap
between group `arg` lists.

One clause inside `test_architecture_boundaries.py` — `test_core_transaction_is_the_only_caller_of_mutating_vcs_methods` — walks `SRC_ROOT.rglob("*.py")`, i.e. the whole tree, so *any* group could in
principle break it. Pinning it into all fourteen on that basis was rejected: the pin list above
tracks the **named** package scans, which is the signal a reader can act on, and the codebase-wide
clause is already carried unpinned by the eight other `src/`-owning groups. It is caught at pipeline
tier like any other whole-tree invariant.

### Un-grouped tests — now test infrastructure only

**The §3 gap is closed: 0 files.** The previous revision of this section carried 11 files / 128
tests un-grouped because four module trees — `okf/`, `guillotine/`, `service/`, and the dashboard
pair — had no §3 row, and `subsystems` entries must resolve to a §3 Built component (sentinel
TT01). §3's refinement pass gave all four a row, so the three new groups above plus the Dashboard
row's assignment to `mcp-surface` absorb every one of those files. Nothing is un-grouped for lack
of a §3 row any more.

What remains is **test infrastructure — 3 files / 40 tests**, un-grouped for a different reason
that closing the §3 gap never addressed: each covers code outside `src/knotica/`, which §3 does not
model *by design*, so Note 1's prohibition on synthetic subsystem names applies permanently rather
than pending a refinement pass.

| Un-grouped file | Tests | Covers | Why no group |
|---|---:|---|---|
| `test_spine.py` | 21 | `tests/conftest.py` + `tests/support/` | Test infrastructure; no group's `file_dependencies` covers it (Note 3). Also the most expensive single un-grouped file at ~16.6 s. |
| `test_topology_runner.py` | 10 | `scripts/test_group.py` | Dev tooling outside `src/knotica/` — the very runner documented above. |
| `test_adr_health.py` | 9 | `scripts/check_adr_health.py` | Dev tooling outside `src/knotica/`, same constraint. |

All 40 fall through to **pipeline tier (full suite)** — never skipped, only absent from the scoped
inner loop. The two `scripts/`-covering files are the live illustration of Note 1's closing
paragraph: inventing an `scripts/` subsystem would be a TT01 FAIL, and folding either into a
group's `file_dependencies` would make that group claim coverage of a tree §3 does not describe.
Un-grouped is the correct state for both, not a debt.

**The by-import rule survives the gap it surfaced.** `test_log_fmt.py` is an `okf/` test by import
(`knotica.okf.log_fmt`) and by nothing else — its filename carries no `okf` marker, and a
`test_okf_*.py` glob would have left it here. Its mirror image, `test_okf_cli.py`, is named for a
CLI it never drives. Both are now in `okf-conformance`; both were placed by reading imports, and
either filename would have misled a glob in the opposite direction.

### Partition check

Re-proved against the live tree after the duplicate-consolidation and `td-031` passes.

**Files — 175 total.** 166 in group `arg` lists as own membership + 6 pinned fitness files + 3
un-grouped = 175. Exactly two files appear in more than one group's `arg` list
(`test_file_size_ratchet.py` in all 14, `test_architecture_boundaries.py` in 5); both are pinned
fitness tests, counted once in the runtime table. No file is assigned to two groups' own
membership.

**Tests — 2520 total.** Running the fourteen groups yields 2480 unique tests; 2480 + 40 un-grouped
= **2520**, exactly the full-suite collection. The topology covers the suite with no silent drop
and no stray.

**Two movements, in that order.** The §3 gap closed first; this revision then grew the suite
without reopening it. Both are recorded because the second is only legible against the first:

1. **§3 refinement — the gap closed.** The 128 tests that were the §3 gap did not disappear, they
   moved into groups: `okf-conformance` claimed 37 of them, `guillotine-audit` 39,
   `service-lifecycle` 49, and `mcp-surface` the dashboard's 3. Grouped-unique rose 2294 → 2422
   (+128) while the full suite rose 2453 → 2461 (+8, the then-new `test_adr_health.py`), and the
   un-grouped set fell 159 → 39. That transition is history; the gap it closed has stayed closed.
2. **Duplicate consolidation + `td-031` — files added, no gap reopened.** Four new test files
   landed *inside existing groups*, so the partition widened rather than fraying:
   `tests/test_core_topics.py` (11) and `tests/test_core_jsonl.py` (6) into `vault-semantics`,
   `tests/test_loop_attempt.py` (33) and `tests/test_loop_noop_attempt_characterization.py` (6)
   into `loop-runtime` — 56 tests across 4 files. Adding the pinned
   `test_architecture_boundaries.py`'s +2 (counted once, though it fires in five groups),
   grouped-unique rose 2422 → 2480 (+58); the full suite rose 2461 → 2520 (+59), the extra one
   being a ninth `test_adr_health.py` test — a regression guard for a finalized record pointing at
   a draft id — which lands un-grouped and takes that set 39 → 40. Files rose 171 → 175, every one
   of them into a group. No group was created, none dissolved, and no file fell outside the
   fourteen `arg` lists.

One count still answers what two used to: **3 files / 40 tests** is both the §3-gap size (zero of
it) and the size of what a scoped run leaves to pipeline tier. The revision before last needed
11/128 and 13/159 as separate figures precisely because the gap and the infrastructure remainder
were different sets; with the gap closed they collapsed into one, and movement 2 did not separate
them again — it added to the grouped side and to the infrastructure side without creating a third.

### Open divergence — one, and §3 did not close it the way this section predicted

`src/knotica/cli/**` (`cli-surface`) covers three thin adapters whose tests are not `cli-surface`
tests. The previous revision predicted this would close itself once §3 gained rows for `okf/`,
`guillotine/`, and `service/`. §3 gained them; it did not close, and the reason is worth recording
because it was a wrong prediction rather than a delayed one — the divergence was never about §3
rows at all:

| Adapter | Its tests | Where they live now | Why `cli-surface` still misses them |
|---|---|---|---|
| `cli/service.py` | `test_cli_service.py` (9) | `service-lifecycle` | It drives `knotica.cli.main` only to reach `knotica.service.manager`; a `service/` edit is what breaks it, so the by-import rule homes it there. `cli-surface` covers the adapter without running its test. |
| `cli/okf.py` | none | — | `test_okf_cli.py` is named for this adapter but imports `okf.check`/`export`/`repair` directly and never invokes the CLI. The adapter has **no** test anywhere. |
| `cli/guillotine.py` | none | — | Nothing in the suite drives `knotica.cli` with `guillotine` argv. The adapter has **no** test anywhere. |

So the residue is one genuine group-placement divergence (`cli/service.py`) and two genuine
coverage holes (`cli/okf.py`, `cli/guillotine.py`) that no grouping can fix — an ungrouped test
cannot be re-homed if it does not exist. The placement divergence is a selector call and *is*
fixable here: pinning `test_cli_service.py` into `cli-surface` as a third overlap would cost ~1 s.
It was not taken in this pass, which was scoped to the three missing blocks; the choice is between
accepting the divergence and admitting a non-fitness file to a second group's `arg` list, and it
deserves its own decision rather than a side effect of this one.

*Resolved, recorded so they are not re-opened:* three earlier divergences are absorbed.
`promote_note.py` / `reanchor_note.py` appear under `notes-overlay` in Note 2's subtraction table;
`baseline_probe.py` / `core/metrics.py` are recorded there as deliberate residual placement
(multi-consumer core substrate) — neither needed a selector change. `operations/guillotine.py` did
need one and got it: it left `vault-semantics`' `file_dependencies` for `guillotine-audit`'s in the
same edit that created that group, so the module and the 39 tests that cover it are finally in the
same place.
