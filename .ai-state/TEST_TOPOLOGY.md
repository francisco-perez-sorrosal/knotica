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
groups so that a pipeline step can run a scoped subset instead of the full suite (2443 tests /
169 test files / ~296 s wall-clock as of 2026-08-04).

The groups are runnable by hand, not only by pipeline agents: `make test-groups` lists them and
`make test-group GROUP=<id>` runs one, both derived from the blocks in this file. See
[Running a group](#running-a-group).

The suite is **not** flat: 147 files sit directly in `tests/`, and 22 more are nested under
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
| `src/knotica/cli/` | `cli-surface` | The `knotica` entry point registry. A sibling delivery surface to `mcp-surface` over the same `core.operations`; both are thin, and neither may write the vault directly. |
| `src/knotica/evals/` | `eval-harness` | Frozen-corpus evaluator: runner, judge, cache, scorer, scalar, golden set, config fingerprint. Carries the `evals` extra (`anthropic`, `dspy`) that is deliberately off the MCP launch path. |
| `src/knotica/evals/error_capture.py` | `eval-harness` | The shared leaf both `harness.py` and `scorer.py` import; it exists only to serve the harness's per-example outcome seam. |
| `src/knotica/programs/` | `query-compile` | DSPy query compile (MIPROv2 + bootstrap fallback) → compiled artifact + `CompiledRunner`. Optimization is a distinct concern from measurement: a compile-artifact change should not force a re-run of the LLM-judge suite. |
| `src/knotica/core/loop.py` + `loop_state.py` + `loop_heartbeat.py` + `loop_progress.py` | `loop-runtime` | The autonomous watcher: observe → gate → heal, plus its extracted siblings (note 2). The most expensive group by construction — real git clones, worktrees, arena races, flock contention. |
| `src/knotica/discovery/` | `discovery-network` | Pure outbound-network boundary: no vault read/write, no state, single inward edge to `core.errors`, enforced by the `mcp_server ⊬ discovery` import-boundary test. The most cleanly dependency-closed group in the project. |
| `src/knotica/core/gap_classifier.py` + `records.GapRecord` | `gapfill-spine` | P1 — regression → fault-class diagnosis, producing the `GapRecord` queue. |
| `src/knotica/core/gapfill.py` + `records.SuggestionRecord` + `mcp_server/tools_suggestions.py` + `cli/gapfill.py` | `gapfill-spine` | P3 — gap × ranked-candidate join, suggestion queue, approval surface. |
| `src/knotica/core/source_gate.py` + `source_ingest.py` + `records.SuggestionRecord.gate_outcome` + `mcp_server/tools_source_ingest.py` + `core/operations/candidate_scope.py` + page-subset filter on `evals/train_bootstrap.py`+`evals/golden.py` | `gapfill-spine` | P4 — worktree-scoped candidate ingest and the merge-or-quarantine gate. P1/P3/P4 are three §3 rows but one hand-forward contract over shared `records.*` schemas and the `.knotica/{gaps,suggestions}` JSONL files; they change together and are meaningless apart. |
| Plugin layer (repo root) | `plugin-layer` | `.claude-plugin/`, `.mcp.json`, `commands/`, `hooks/`, `skills/`, and wheel packaging. The only group whose file dependencies live outside `src/` — a `commands/*.md` edit has no business running 2443 tests. |

**Coverage:** 18 Built components → 11 groups, 1:1 (each component has exactly one owning group).
`src/knotica/agent/` is `Planned` and is deliberately absent — it gets a row when it is Built.

### Note 1 — group granularity is bounded by `DESIGN.md` §3 granularity

A group's `subsystems` entries must resolve to §3 Built components (sentinel TT01). Two
consequences follow, and neither is fixable from inside this file:

- **`vault-semantics` is unavoidably the largest group.** `src/knotica/core/` is one §3 row
  covering ~50 modules. Splitting the group requires splitting the §3 row first.
- **Un-modeled packages get no group.** `src/knotica/okf/` (11 modules), `src/knotica/guillotine/`
  (9), `src/knotica/service/` (3), and `src/knotica/dashboard/` (1) have **no §3 row at all**, so
  no group may name them. Their tests — **11 files / 128 tests** — fall through to pipeline-tier
  (full-suite) execution until §3 gains rows for them:

  | Un-modeled tree | Test files |
  |---|---|
  | `okf/` | `test_okf_cli.py`, `test_okf_frontmatter.py`, `test_okf_links.py`, `test_okf_notes_isolation.py`, `test_okf_repair_characterization.py`, **`test_log_fmt.py`** |
  | `guillotine/` | `test_guillotine.py` |
  | `service/` | `test_service_manager.py`, `test_service_daemon_env.py`, `test_cli_service.py` |
  | `dashboard/` | `test_http_dashboard.py` |

  `test_log_fmt.py` is the trap: it covers `okf/` — it imports `knotica.okf.log_fmt` at line 5 —
  but its filename carries no `okf` marker, so a name-pattern sweep (`test_okf_*.py`) misses it and
  reports five files where there are six. Enumerate this set by import, never by filename glob.

Adding the one file un-grouped for a *different* reason — `test_spine.py`, which is test
infrastructure rather than a §3 gap (see note 3) — gives the whole-file un-grouped total of
**12 files / 149 tests**. The two figures are not interchangeable: 11/128 is the size of the §3
gap this note is about, and 12/149 is what a scoped run leaves to pipeline tier.

The unblock for both is a `.ai-state/DESIGN.md` §3 refinement pass, not a topology edit. Recorded
as `dec-draft-4b91f4f7`. Do **not** invent synthetic subsystem names to close the gap — an
unresolvable `subsystems` entry is a TT01 FAIL, and folding `okf/` or `guillotine/` silently into
another group's `file_dependencies` makes this table lie about what it covers.

### Note 2 — `vault-semantics` is a *residual*, not a directory

Read `vault-semantics` as `src/knotica/core/` **minus** the modules other groups claim. Writing
`src/knotica/core/**` as its `file_dependencies` would swallow four other groups whole. The
claimed subtractions, by group:

| Group | Modules under `core/` it claims |
|---|---|
| `vault-substrate` | `vault_layout.py` |
| `notes-overlay` | `notes/`, `notes_config.py`, `operations/capture_note.py`, `operations/promote_note.py`, `operations/reanchor_note.py` |
| `loop-runtime` | `loop.py`, `loop_state.py`, `loop_heartbeat.py`, `loop_progress.py`, `loop_factory.py`, `loop_promote.py`, `loop_retry_backoff.py`, `loop_cadence_config.py`, `arena.py`, `arena_resolve.py`, `candidate_gate.py`, `branch_namespaces.py`, `branch_scoreboard.py`, `branch_delete.py`, `best_effort.py` |
| `query-compile` | `compile_run.py`, `compile_promote.py`, `compile_state.py`, `compiled.py`, `query_engine.py`, `models_config.py`, `prompt_diff.py`, `trainset.py` |
| `gapfill-spine` | `gap_classifier.py`, `gapfill.py`, `gapfill_config.py`, `source_gate.py`, `source_ingest.py`, `operations/candidate_scope.py` |

The `loop-runtime` and `query-compile` rows are the ones §3 files under the coarse `core/` row but
that are loop- and compile-concern in fact (the P-A/loop-py-extraction siblings and the
compile-artifact chain). Grouping them by concern rather than by table row is what keeps
`vault-semantics` a coherent mutation-core group instead of a catch-all.

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

Eleven groups, one per row-owner in the table above. Every block was authored against the live
suite and every `selectors` entry was executed before it was written down — see
`.ai-work/test-topology-init/TEST_RESULTS.md` for the per-group verification run.

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
real path, and no group id repeats. It refuses any selector `strategy` other than `pytest-globs`
rather than guessing an invocation.

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
the un-grouped set below is provably the complement of the eleven `arg` lists, not an accident.

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

`parallel_safe: true` on all eleven groups is measured, not assumed — each group was re-run under
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
      - tests/test_core_metrics.py
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
  - "src/knotica/core/transaction.py"
  - "src/knotica/core/vault_metadata_tree.py"
  - "src/knotica/core/vault_scaffold.py"
  - "src/knotica/core/vcs.py"
  - "src/knotica/core/operations/__init__.py"
  - "src/knotica/core/operations/create_topic.py"
  - "src/knotica/core/operations/curate_example.py"
  - "src/knotica/core/operations/doctor_repair.py"
  - "src/knotica/core/operations/guillotine.py"
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
  `src/knotica/core/**` glob here would swallow four other groups whole.
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
integration_boundaries: []   # planner-owned; populates lazily in later pipelines
parallel_safe: true
shared_fixture_scope: per-suite
shared_state: tmp_path
notes: >-
  The glob deliberately overlaps `gapfill-spine` on `tools_suggestions.py` and
  `tools_source_ingest.py`: a change there should fire both the surface tests and the spine
  tests.
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
  ownership) even though the DESIGN.md prose for this row names CompiledRunner.
```

### `loop-runtime`

```yaml
id: loop-runtime
title: Loop runtime — the autonomous observe / gate / heal watcher
subsystems:
  - "src/knotica/core/loop.py + loop_state.py + loop_heartbeat.py + loop_progress.py"
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
      - tests/test_loop_blocked_failure_backoff.py
      - tests/test_loop_cadence.py
      - tests/test_loop_cadence_characterization.py
      - tests/test_loop_cadence_config.py
      - tests/test_loop_content_classification.py
      - tests/test_loop_eval_error_visibility.py
      - tests/test_loop_factory_cadence_wiring.py
      - tests/test_loop_flock_contention.py
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
  of two (80 s sequential).
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

### Verified runtimes (2026-08-04, single sample each)

Baseline for comparison: full suite 2443 passed in ~296 s.

| Group | Files | Tests | Sequential | `-n 4 --dist loadfile` |
|---|---:|---:|---:|---:|
| `vault-substrate` | 5 | 161 | 1.6 s | 2.7 s |
| `vault-semantics` | 27 | 550 | 24.7 s | 8.9 s |
| `notes-overlay` | 17 | 316 | 23.5 s | 9.7 s |
| `mcp-surface` | 37 | 361 | 41.6 s | 19.2 s |
| `cli-surface` | 12 | 104 | 19.6 s | 10.0 s |
| `eval-harness` | 18 | 330 | 20.4 s | 17.0 s |
| `query-compile` | 6 | 48 | 9.8 s | 8.7 s |
| `loop-runtime` | 21 | 159 | 79.8 s | 34.3 s |
| `discovery-network` | 10 | 130 | 1.3 s | 2.6 s |
| `gapfill-spine` | 13 | 196 | 43.5 s | 17.3 s |
| `plugin-layer` | 4 | 29 | 4.8 s | 2.1 s |

`expected_runtime_envelope` is deliberately omitted: one sample per group is a measurement, not a
p50/p95 distribution, and the trunk makes the field optional until M3 precisely so nobody invents
one.

### Cross-cutting fitness tests — the selector decision Note 3 defers here

Note 3 leaves the disposition of the seven un-grouped fitness tests to this section. Blanket-pinning
all seven into all eleven groups was rejected on measurement: `test_spine.py` alone costs ~16 s,
more than eight of the eleven groups cost in total. Each file is instead pinned to the groups whose
`file_dependencies` can actually break it:

| Fitness test | Cost | Pinned into | Why |
|---|---:|---|---|
| `test_file_size_ratchet.py` | 0.1 s | all 11 groups | Its trigger is literally "a file under `src/` grew"; no group can be exempt and the cost is noise. |
| `test_architecture_boundaries.py` | 0.8 s | `vault-semantics`, `mcp-surface`, `cli-surface`, `eval-harness` | It AST-scans exactly those four trees for the single-writer invariant. |
| `test_server_tool_surface.py` | — | `mcp-surface` | Asserts the shape of the MCP tool surface. |
| `test_tool_description_guards.py` | — | `mcp-surface` | Asserts MCP tool description content. |
| `test_vault_targeting.py` | — | `mcp-surface` | Asserts the per-call `vault` selector reaches `config.resolve` through the tools. |
| `test_template.py` | 0.6 s | `plugin-layer` | Validates the shipped `vault-template/` inventory, which is a `plugin-layer` file dependency. |
| `test_spine.py` | 16.6 s | **none** | Its trigger is `tests/conftest.py` + `tests/support/` — test infrastructure, which no group's `file_dependencies` covers. Runs at pipeline tier. |

Pinned files are counted once in the runtime table above and are the only deliberate overlap
between group `arg` lists.

### Un-grouped tests — the `DESIGN.md` §3 gap, plus one test-infrastructure file

Two different reasons put a file outside every group, and their counts are not interchangeable:

- **The §3 gap — 11 files / 128 tests.** Note 1 documents four module trees with no §3 row.
  `subsystems` entries must resolve to a §3 Built component (sentinel TT01), so no group may name
  them and none does.
- **Test infrastructure — 1 file / 21 tests.** `test_spine.py` is un-grouped by design (Note 3):
  its trigger is `tests/conftest.py` + `tests/support/`, which no group's `file_dependencies`
  covers. It is not a §3 gap, and closing the gap would not group it.

Together: **12 files / 149 tests** (128 + 21). All of it falls through to **pipeline tier (full
suite)** — never skipped, only absent from the scoped inner loop:

| Un-modelled tree | Test files |
|---|---|
| `src/knotica/okf/` | `test_okf_cli.py`, `test_okf_frontmatter.py`, `test_okf_links.py`, `test_okf_notes_isolation.py`, `test_okf_repair_characterization.py`, `test_log_fmt.py` |
| `src/knotica/guillotine/` | `test_guillotine.py` |
| `src/knotica/service/` | `test_service_manager.py`, `test_service_daemon_env.py`, `test_cli_service.py` |
| `src/knotica/dashboard/` | `test_http_dashboard.py` |
| (test infrastructure — not a §3 gap) | `test_spine.py` — see the fitness table above |

`test_log_fmt.py` is an `okf/` test by import (`knotica.okf.log_fmt`) and by nothing else — its
filename carries no `okf` marker. Enumerate an un-modelled tree's tests by import; a filename glob
(`test_okf_*.py`) under-reports.

The unblock for the 11/128 §3 gap is the `DESIGN.md` §3 refinement pass recorded as
`dec-draft-4b91f4f7`, not an edit here. `test_spine.py` stays un-grouped regardless.

### Partition check

169 test files total. 151 in group `arg` lists + 12 un-grouped + 6 pinned fitness files = 169, no
file assigned to two groups' own membership. Running the eleven groups yields 2294 unique tests;
2294 + 149 un-grouped = **2443**, exactly the full-suite baseline. The topology covers the suite
with no silent drop.

The 149 is the whole un-grouped set — the 11 §3-gap files (128 tests) plus `test_spine.py` (21).
Use 11/128 when sizing the §3 refinement pass and 12/149 when sizing what a scoped run leaves to
pipeline tier; they answer different questions.

### Open divergence from Note 2 — one, and it is not fixable here

`src/knotica/cli/**` (`cli-surface`) covers `cli/service.py`, `cli/okf.py`, and
`cli/guillotine.py`, whose tests are un-grouped for lack of a §3 row — so editing any of those
three fires `cli-surface` without running its own tests. It is the last mismatch between this
section and Note 2, and it closes itself when §3 gains rows for those trees, not by an edit here.

*Resolved, recorded so they are not re-opened:* the two earlier divergences are absorbed by the
amended Note 2 — `promote_note.py` / `reanchor_note.py` now appear under `notes-overlay` in the
subtraction table, and `baseline_probe.py` / `core/metrics.py` are recorded there as deliberate
residual placement (multi-consumer core substrate). Both agree with the `file_dependencies` in the
group blocks above as written; neither needed a selector change.
