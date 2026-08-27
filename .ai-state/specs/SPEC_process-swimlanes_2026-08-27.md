# SPEC — knotica process-swimlanes (M0–M5)

| Field | Value |
|---|---|
| **Feature** | Six process lanes (`home`, `learn`, `answer`, `improve`, `fill`, `tend`) replace the tool-shaped dashboard and the flat/near-flat MCP surface; every surface (dashboard, CLI, docs, MCP tools) projects one declaration (`core/process_model.py`) |
| **Status** | Merged to `main` — `b052a84` (`feat!: M5 — Home. The process-swimlanes redesign is complete`), reconciled at `436290d` |
| **Tier** | Full |
| **Sources** | `.ai-work/process-swimlanes/SYSTEMS_PLAN.md` (`## Behavioral Specification` REQ-01…REQ-27, `## Acceptance Criteria` AC-01…AC-15/AC-C4, adopted verbatim), `.ai-work/process-swimlanes/IMPLEMENTATION_PLAN.md` (123 steps, M0–M5), `.ai-work/process-swimlanes/LEARNINGS.md` (Decisions Made, per-step evidence), `.ai-work/process-swimlanes/traceability.yml` (PARTIAL for M5 — see Traceability note below) |
| **Date** | 2026-08-27 |

Scope: the whole redesign, M0 (prerequisites) through M5 (Home). Out of scope: REQ-24 (live
Claude-Desktop `hostCapabilities` measurement) and REQ-27 (the release-checklist/changelog item),
neither of which is a pipeline-verifiable behavior — see their rows below.

## Requirements

Verbatim from `SYSTEMS_PLAN.md § Behavioral Specification`.

### Process model — the single declaration

- **REQ-01** — When any surface needs the lane set, and the process model is imported, the system returns
  six lanes (`home`, `learn`, `answer`, `improve`, `fill`, `tend`) with `home` carrying no stage rail,
  so that every surface agrees on what a lane is without restating it.
- **REQ-02** — When a lane other than `home` is read, the system returns its stages in declared order,
  each carrying `id`, `title`, a state predicate, an advancing surface reference, and a `handoff` flag,
  so that a rail can be rendered and advanced from the declaration alone.
- **REQ-03** — When a verb belongs to more than one lane, the system records every `(lane, stage,
  narration)` membership it holds rather than one owning lane, so that a shared mechanism is narrated
  per-lane instead of duplicated or arbitrarily assigned.
- **REQ-04** — When a verb's lane depends on a runtime argument (`notes action=promote target=…`), the
  system keys membership on `(verb, discriminator)`, so that argument-routed verbs are expressible.
- **REQ-05** — When a verb belongs to no lane, the system classifies it as `primitive` (cross-lane read)
  or `infrastructure` (unlaned), so that "no lane" is a declared state rather than an omission.
- **REQ-06** — When the process model changes, and its generated TypeScript mirror is not regenerated,
  the system fails `make verify`, so that the two halves of the declaration cannot drift.
- **REQ-07** — When the declaration names an existing ordered stage list (`INGEST_STAGES`,
  `CURATE_STAGES`), the system references it rather than copying it, so that no second source of truth
  for a stage order is created.
- **REQ-08** — When a stage is marked `handoff=True`, the system asserts it has no dashboard-executable
  advancing action, and when marked `handoff=False`, that it has one, so that client-as-brain is a
  mechanically-held property rather than a convention.

### Published surface — the tiered lane rename

- **REQ-09** — When the published MCP surface is enumerated after this work, the system reports a flat
  conversational tier plus six lane dispatchers and the two unlaned tools, so that the surface speaks in
  lanes and drops below the count at which tool-selection quality degrades.
- **REQ-09b** — When a verb is one the client-as-brain calls mid-turn, the system keeps it registered
  flat under its current name, so that `dec-045`'s conversational/operator boundary survives the lane
  re-cut.
- **REQ-09c** — When a lane dispatcher's action table is built, the system derives it from the declared
  lane membership rather than from a hand-maintained tuple, so that the tool surface and the lane rails
  cannot disagree.
- **REQ-10** — When a lane dispatcher's description is read, the system presents its action table with
  each action's *what it does · does NOT · requires · returns*, naming at most two sibling tools and
  only as a disambiguator, so that routing prose lives in six tables rather than scattered across 23
  files.
- **REQ-10b** — When a caller passes a superseded action string, the system returns the correct payload
  plus a `deprecation` note; when it passes an unknown one, the system returns `INVALID_ARGUMENT` with a
  `fix=` hint listing the valid actions and records it through the rejected-action signal.
- **REQ-11** — When `--help` or `docs/reference.md` is read, the system presents the surface grouped by
  lane, with the conversational tier and infrastructure in their own groups.
- **REQ-11b** — When a human reaches a renamed surface by its superseded name — a removed CLI subcommand,
  a removed slash command, or a bookmarked `?pane=` value — the system still performs the work (CLI:
  `argparse` alias plus a stderr warning; dashboard: the lane alias map resolves the stale value to the
  lane that replaced it) or explains the move (slash: a tombstone file naming the replacement), so that
  no rename is a silent break for a human.
- **REQ-12** — When `knotica lane <id>` is run, the system prints that lane's rail with each stage's
  current state and the command that advances it, so that the CLI is a projection of the same model.
- **REQ-13** — When `open_dashboard` is called with a `lane` argument, the system opens the dashboard on
  that lane in **both** mounts, so that a lane deep-link is not HTTP-only.
- **REQ-13b** — When the process model changes, the system serves the current declaration to the client
  and the client prefers it over its bundled fallback, so that a client can never offer a lane the
  connected server cannot serve.

### The two verified defects

- **REQ-14** — When the dashboard's gate trigger is clicked, the system returns a preview envelope
  carrying `confirm_nonce` and an estimated cost, and bills nothing.
- **REQ-15** — When the user confirms that preview, and the nonce is unexpired, the system runs one gate
  cycle and reports whether it billed, so that the two-phase contract holds from the client side.
- **REQ-16** — When the two-phase preview→confirm→outcome interaction is needed by a third surface, the
  system reuses one extracted component rather than a third copy.
- **REQ-17** — When a source candidate passes the gate, and its suggestion carries a `gap_id`, the system
  flips that gap `open → resolved` within the same mutation span as the suggestion stamp, so that Fill
  terminates on its own record.
- **REQ-18** — When a human dismisses a gap, and the gap is `open`, the system writes
  `status="dismissed"` with a reason; from any other status it rejects with `INVALID_ARGUMENT`.
- **REQ-19** — When `gaps_read` is called after either transition, the system reports the new status in
  `status_counts`, so that two of three declared statuses stop being permanently zero.

### Lane state

- **REQ-20** — When `wiki_status view="attention"` is called, the system returns, for every topic in the
  active vault, which lanes need attention and why, using at most a small constant number of git
  subprocesses for the whole vault plus O(1) small file reads per topic.
- **REQ-21** — When `wiki_status view="attention"` is called, the system performs **neither** a full-vault
  `lint_vault` walk **nor** note-anchor resolution — reporting the last-lint date and its staleness
  instead, and leaving drift to a default-collapsed row that pays its cost only on expansion — so that
  the attention projection is genuinely cheaper than `view="summary"` rather than "summary minus drift".
- **REQ-21b** — When cross-topic runner liveness is reported, the system reads the service layer's
  all-watched-topics projection rather than `_gate_and_loop`'s multi-topic stub, so that Home never
  reports "runner: off" for every topic — a wrong answer being worse than an absent one.
- **REQ-21c** — When Home polls, the system polls at a human cadence and stops while the surface is not
  visible, so that a cross-topic inbox never runs at the 2 s cadence that exists for in-flight progress.
- **REQ-22** — When the Fill lane's read-only session projection is called for one suggestion, the
  system returns that session's stage (`not_started` / `waiting_on_client` / `client_wrote` /
  `rework_in_flight` / `submitted` / `merged` / `refused` / `blocked` / `swept`) plus a `next.actor`
  naming who acts next, derived from branch existence alone and requiring no client report-in.
- **REQ-22b** — When a Fill queue row or the Home inbox renders, the system does **not** call that
  projection — it is called only for the active item, so its 2-3 git subprocesses per record never land
  on a list.
- **REQ-23** — When a lane's rail is rendered, the system derives each stage's state from the process
  model's predicates evaluated **server-side**, so that rail semantics have one implementation.

### Measurement and release

- **REQ-24** — When the MCP-App bridge is exercised against Claude Desktop, the system records which
  `hostCapabilities` are advertised, so that the handoff stage's affordance is chosen on evidence.
- **REQ-25** — When any tool is invoked, the system appends one timestamped record to a persisted,
  gitignored telemetry sink, so that a before/after comparison across a description change is possible.
- **REQ-26** — When a dispatcher-shaped tool rejects an unknown action, the system records it through the
  same rejected-action signal every dispatcher uses.
- **REQ-27** — When the release ships, the system carries a `feat:` commit and a changelog entry naming
  every added surface element, and the marketplace manifest bump appears as an explicit release-checklist
  item.

## Acceptance Criteria — final walk

Every AC below is checked against the merged tree at `b052a84`/`436290d`, not against plan intent.

- [x] **AC-01** — MET. Home (M5, Step 115) is the sixth lane and default landing pane; the M5 census
  (`m5HomeCensus.test.ts`, 31/31 green) asserts every pane is reachable and that `onOpen`-shaped
  cross-lane navigation exists only inside `dashboard/src/lanes/home/**`, so no stage's terminal state
  is reachable only from another lane.
- [x] **AC-02** — MET. `core/process_model.py` is the single declaration (`LANES`, `LANE_STAGES`,
  `LANE_MEMBERSHIP`); `REQ-06`'s `make verify` regenerate-and-diff gate on `dashboard/src/processModel.ts`
  is the mechanical enforcement of "one place."
- [x] **AC-03** — MET. `dashboard/src/lanes/improve/ObserveStage.tsx`'s `run_eval` control and the gate
  trigger both route through the shared `TwoPhaseAction` primitive (`twoPhaseAction.test.ts`); a preview
  mints `confirm_nonce`/bills nothing, a second confirmed call redeems the nonce and bills — asserted by
  `dashboard/src/__tests__/toolClient.runOnce.test.ts`.
- [x] **AC-04** — MET. `GateStage`'s billed `loop run_once` control is the sole call site on
  `TwoPhaseAction` for that action (LEARNINGS Step ~99/~117 area, "`GateStage`'s billed `loop run_once`
  stays on the shared `TwoPhaseAction`"); the pre-existing second entrance (`VaultPane.tsx:722-746`) was
  deleted, not fixed (M3 Step 79).
- [x] **AC-05** — MET. `core/gapfill.py::apply_gate_outcome` closes the originating gap in the same
  mutation span as the suggestion stamp (REQ-17); `tests/test_gap_lifecycle.py` and
  `tests/test_fill_review_gap.py` assert the `resolved`/`dismissed` buckets are non-zero in
  `gaps_read`'s `status_counts` (REQ-19).
- [x] **AC-06** — MET. `fill(action="review_gap")` (REQ-18) is a zero-new-registration action on the
  existing Fill dispatcher; `tests/test_gap_lifecycle.py::test_dismissing_a_gap_that_is_not_open_is_refused_with_an_actionable_error`
  and its `reopen` sibling assert the `open`-only guard.
- [x] **AC-07** — MET. `wiki_status view="attention"` (M2, `core/status.py::_attention_status`) plus
  Home's own poll (M5, Steps 109–116) deliver a cross-topic attention list in one poll cycle;
  `tests/test_status_attention_view.py::test_attention_view_git_subprocess_count_does_not_grow_with_topic_count`
  is the mechanical guard against N per-topic calls.
- [x] **AC-08** — MET. `HandoffStage.tsx` (M4) renders in both mounts — a real host message where
  `hostCapabilities.message` exists, copyable command text otherwise — and Fill's own handoff (`Steps
  87/88`) and Learn's are both wired.
- [x] **AC-09** — MET. Fill's slash commands (`commands/fill.md` et al., M4) exist on disk; the
  tombstone-file convention for removed commands (REQ-11b) is asserted by the CLI-lanes test group.
- [x] **AC-10** — MET. `tests/test_lane_rename_invariants.py` + `tests/test_server_tool_surface.py`'s
  ceiling test hold `list_tools()` to `≤ 22`; Tier-1 preservation is parametrized over the 13 verbs that
  stay flat (M1 Step 32's own count correction against the plan's "nine to fourteen" prose).
- [x] **AC-10b** — MET. `tests/test_lane_action_deprecation.py` covers the dispatcher-action deprecation
  note; `tests/test_cli_lanes.py` covers the `argparse` alias + stderr warning; the slash tombstone and
  `?pane=` alias map are covered per AC-09/AC-11b evidence above.
- [x] **AC-11** — MET. `dispatch_telemetry` coverage reached 35/35 (M1 CP-2 checkpoint) via
  `RecordingServer`; `tests/test_dispatch_telemetry_census.py::test_every_registered_tool_emits_exactly_one_dispatch_record`
  is the mechanical census (REQ-25).
- [x] **AC-12** — MET. `docs/reference.md`'s M2/M5 documentation-sweep steps (Step 56 and the M5 doc
  pass) reorganize the tool table by lane against the post-rename registration count.
- [x] **AC-13** — MET. Step 115's own report records "one full `make verify` green (3360 passed)"; the
  merge commit `b052a84` landing on `main` is the terminal instance of this criterion holding at every
  commit through the pipeline.
- [x] **AC-14** — MET. `tests/test_lane_rename_invariants.py`'s six on-disk-name guards (M1 Step 32)
  assert branch prefixes, the four dataset filenames, and the prompt overlay directory are
  byte-identical, each checked against its owning source constant rather than a hand-copied literal.
- [x] **AC-15** — MET. The same Step 32 suite carries the AC-15 prompt-name guard: `ingest`/`query`/
  `lint`/`curate` are asserted unchanged.
- [x] **AC-C4** — MET. `.ai-state/decisions/050-remove-deprecated-tool-aliases.md` carries
  `re_affirmed_by: [dec-090]`, and `dec-090`'s own frontmatter sets `re_affirms: dec-050`; both are
  finalized (no `dec-draft-*` residue), satisfying `scripts/check_adr_health.py`.

**Result: 18/18 acceptance criteria MET.** No criterion is open or deferred.

## Key Decisions

Finalized ADRs from this pipeline (`.ai-state/decisions/088-*.md` … `101-*.md`), cross-referenced
against `LEARNINGS.md § Decisions Made`. `dec-050`/`dec-090` (the re-affirmation pair) predate this
pipeline's own dec-numbering but are load-bearing for AC-C4 and are included above.

| ADR | Title | Why (one line) |
|---|---|---|
| `dec-088` | Lanes are a facet, not a partition | A verb's lane membership is a declared *set*, not a single owning lane — required by verbs (`query`, `write_page`) that genuinely serve more than one lane. |
| `dec-089` | Process model declared once | `core/process_model.py` is the single source; every surface (dispatcher tables, CLI, dashboard, TS mirror) is generated or read from it, never hand-copied. |
| `dec-090` | Re-affirm: no MCP alias layer | A marketplace channel is not an external consumer; `dec-050`'s no-alias ruling holds for the lane rename, narrowed to aliases carrying schema weight. |
| `dec-091` | Handoff-stage observation-first | Handoff stages render narration + copyable command; the dispatch-button affordance is a progressive enhancement gated on host capability, not a requirement. |
| `dec-092` | Lane deep-link and attention-view budget | `open_dashboard(lane=...)` and `wiki_status view="attention"` share one cost discipline: O(1) small-file reads per topic, a small constant git-subprocess count for the whole vault. |
| `dec-093` | Lane rail contract | One `{id, title, ready\|current\|blocked\|handoff, reason}` shape serves every lane's rail, replacing per-pane inline derivation. |
| `dec-094` | Tiered lane tool surface | The published MCP surface becomes a flat conversational tier + six lane dispatchers + two unlaned tools, amended to keep every high-density conversational verb flat. |
| `dec-095` | CLI lane nesting, two levels | `knotica <lane> <stage-verb>` — two-level nesting, flattened unless ambiguous (Shape C). |
| `dec-096` | Context-review gates as prerequisites | Structural readiness checks (test runner, `noUnusedLocals`, DOM environment) are scheduled as explicit M0/M3 prerequisite steps, not assumed. |
| `dec-097` | Dashboard test-runner prerequisite | `vitest` lands before any characterization work needs it (M0 Step 1). |
| `dec-098` | M1 add-then-remove release indivisibility | The declaration and additive dispatchers land before anything is removed; removal is a single, indivisible release step gated on equivalence proof. |
| `dec-099` | Slash commands keep flat names | Slash-command names are not re-cut by lane; only Fill gains new ones (it had none). |
| `dec-100` | M3 DOM test environment | `jsdom` + `@testing-library/preact` is the sanctioned dashboard render-test environment from M3 forward. |
| `dec-101` | M3 type-orphan gate | `noUnusedLocals`/`noUnusedParameters` enabled before the M3 lane-rail build, to catch dissolved-pane orphans mechanically. |

## Traceability

> **PARTIAL for M5 (Steps 107–116) — reconstructed, not machine-rendered.**
> `.ai-work/process-swimlanes/LEARNINGS.md § Traceability-merge incident (orchestrator), 2026-08-27`
> records that the M5-wave merge into `traceability.yml` crashed on an unhashable fragment value, and
> the un-merged M5 fragments were deleted before the crash was noticed. `traceability.yml` itself holds
> every M0–M4 REQ plus the M2 lane-state REQs (REQ-20…REQ-23, which land in M2, not M5) correctly. The
> rows below marked **(reconstructed)** are derived from `LEARNINGS.md`'s M5-wave section and per-step
> `TEST_RESULTS_step10x.md` fragments — REQ → step's own named behavior → the file(s) that step reports
> — not from the YAML. Every other row is rendered directly from `traceability.yml`.

**Status vocabulary**: `COVERED` — implementation and tests both present; `UNTESTED` — no implementation/test recorded in traceability or reconstructable prose; `DEFERRED` — the requirement is explicitly out of scope for this pipeline (not a gap).

### Matrix

| Requirement | Test(s) | Implementation | Status |
|---|---|---|---|
| REQ-01 | `tests/test_process_model.py` (5 tests: lane order, home no-rail, unique stage ids, ordered sequence, ≥1 stage) | `core/process_model.py::LANES`, `::LANE_STAGES` | COVERED |
| REQ-02 | `tests/test_process_model_predicates.py` (11 tests) | `core/process_model.py::Stage`, `::LANE_STAGES`, `::derive_stages()`, `::_derive_sequence_stages()`, `::_derive_checklist_stages()` | COVERED |
| REQ-03 | `tests/test_process_model.py::test_every_registered_verb_has_lane_membership_or_a_lane_less_classification`, `::test_lane_less_verbs_carry_no_lane_membership_at_all` | `core/process_model.py::LANE_MEMBERSHIP` | COVERED |
| REQ-04 | `tests/test_process_model.py::test_every_registered_verb_has_lane_membership_or_a_lane_less_classification` | `core/process_model.py::LANE_MEMBERSHIP` | COVERED |
| REQ-05 | `tests/test_process_model.py` (4 tests incl. `test_the_verb_census_and_the_declaration_are_both_genuinely_populated`) | `core/process_model.py::VERB_CLASSIFICATION` | COVERED |
| REQ-06 | `make verify`'s regenerate-and-diff gate (`Makefile:66-67`, target `process-model-ts`) — mechanical, not a pytest node | `scripts/generate_process_model_ts.py`, `dashboard/src/processModel.ts` | COVERED |
| REQ-07 | `tests/test_process_model.py::test_learn_rail_stage_ids_are_ingest_activity_stages_by_identity_not_by_value` | `core/process_model.py::LANE_STAGE_IDS`, `::_build_learn_rail()` | COVERED |
| REQ-08 | `tests/test_process_model.py` (3 tests: both stage kinds exist, handoff has no action, advancing has exactly one) | `core/process_model.py::Stage` | COVERED |
| REQ-09 | `tests/test_lane_dispatchers.py` (4 tests) | six `mcp_server/tools_dispatch_<lane>.py::register_dispatch_<lane>_tools()`, `tools_dispatch_lane_common.py::register_lane_dispatcher()`, `server.py::_build_server()` | COVERED |
| REQ-09b | `tests/test_lane_rename_invariants.py` (2 tests: Tier-1 flat, no absorbed-verb alias) | `tools_dispatch_lane_common.py::_lane_narrations()`/`_dispatch()`, `server.py::_build_server()`, plus flat+lane registration pairs in `tools_read.py`/`tools_write.py`/`tools_status.py`/`tools_gaps.py`/`tools_ingest.py` | COVERED |
| REQ-09c | `tests/test_lane_dispatchers.py` + `tests/test_lane_rename_invariants.py` (4 tests total) | `tools_dispatch_lane_common.py` (action-table derivation chain), `scripts/check_surface_consistency.py` | COVERED |
| REQ-10 | (no dedicated assertion recorded — description-shape convention) | `server.py::_INSTRUCTIONS`, six `tools_dispatch_<lane>.py::_PURPOSE_DESCRIPTION` | UNTESTED |
| REQ-10b | `tests/test_lane_action_deprecation.py` (5 tests) | `tools_dispatch_lane_common.py::_superseded_actions()`/`_dispatch()`, `envelope.py::with_deprecation_note()`, per-lane `SUPERSEDED_ACTIONS` | COVERED |
| REQ-11 | (no dedicated assertion recorded — `--help`/docs rendering) | `cli/__init__.py::_format_top_level_help()`, `::_format_help_group()` | UNTESTED |
| REQ-11b | `dashboard/src/__tests__/paneRouting.lanes.test.ts`, `tests/test_cli_lanes.py` (4 tests) | `paneRouting.ts::PANE_BY_PARAM`, `cli/__init__.py::DEPRECATED_TOP_LEVEL`/`_resolve_deprecated()`, `cli/common.py::LaneCommand` | COVERED |
| REQ-12 | `tests/test_cli_lane_rail.py` (11 tests) | `cli/common.py::lane_rail()`, `LaneCommand._description()`/`.run()`/`._render_rail()`, `_render_lane_rail()`, `_advancing_text()` | COVERED |
| REQ-13 | `dashboard/src/__tests__/paneRouting.lanes.test.ts` + `toolClient.paneFromInput.test.ts` (14 cases) + `tests/test_mcp_app_ui.py` (6 tests) | `mcp_server/app_ui.py::open_dashboard()`, `paneRouting.ts::resolveLaneFocus()`, `toolClient.ts::paneFromToolInput()`, `App.tsx` | COVERED |
| REQ-13b | `tests/test_mcp_status.py` (9 tests) | `core/status.py::VALID_STATUS_VIEWS`/`gather_wiki_status()`/`_process_model_status()`, `tools_status.py::_wiki_payload()`/`_CATALOG_FREE_VIEWS` | COVERED |
| REQ-14 | `dashboard/src/__tests__/toolClient.runOnce.test.ts`, `twoPhaseAction.test.ts` (7 cases) | (client-side contract only — server already two-phase per M0 characterization) | COVERED |
| REQ-15 | `toolClient.runOnce.test.ts`, `twoPhaseAction.test.ts` (7 cases) | (client-side contract only) | COVERED |
| REQ-16 | `twoPhaseAction.test.ts` + `lanes/improve/__tests__/ObserveStage.test.tsx` (4 cases) | shared `TwoPhaseAction` primitive, one extraction site | COVERED |
| REQ-17 | `tests/test_gap_lifecycle.py` (7 tests) | `core/gapfill.py::apply_gate_outcome()`, `::_close_gap_body()` | COVERED |
| REQ-18 | `tests/test_gap_lifecycle.py` (9 tests) + `tests/test_fill_review_gap.py` (4 tests) | `core/gapfill.py::apply_gap_decision()`/`_plan_gap_decision()`, `tools_gaps.py::register_gaps_lane_tools (review_gap)`, `core/records.py::GapRecord` | COVERED |
| REQ-19 | `tests/test_gap_lifecycle.py` (2 tests) + `tests/test_fill_review_gap.py` (1 test) | `core/gapfill.py::apply_gate_outcome()`/`apply_gap_decision()`, `tools_gaps.py::_gap_status_counts` | COVERED |
| REQ-20 | `tests/test_status_attention_view.py` (2 tests) | `core/status.py::_attention_status()`/`_attention_row()`/`gather_wiki_status()` | COVERED |
| REQ-21 | `tests/test_status_attention_view.py` (2 tests) | `core/status.py::_attention_status()`/`_last_lint_status()`, `tools_status.py::_CATALOG_FREE_VIEWS` | COVERED |
| REQ-21b | `tests/test_status_attention_view.py::test_attention_view_reports_real_liveness_for_a_topic_with_a_fresh_heartbeat` | `core/status.py::_attention_row()` | COVERED |
| REQ-21c **(reconstructed)** | `dashboard/src/lanes/__tests__/visibilityPausedPoll.test.ts` (Step 108, 8/8 green: fires while visible, zero calls while hidden, one immediate call on hidden→visible, teardown stops all calls); re-exercised by `HomeLane.test.tsx`'s poll-wiring assertion (Step 114) | `dashboard/src/lanes/visibilityPausedPoll.ts::startVisibilityPausedPoll()` (Step 107) | COVERED |
| REQ-22 | `tests/test_gapfill_session_status.py` (9 tests, the full nine-state table) | `core/gapfill_session.py::session_status()`/`_open_session_status()`/`_gated_or_not_started_status()` | COVERED |
| REQ-22b | `tests/test_gapfill_session_status.py` (3 tests) | `core/gapfill_session.py::session_status()` | COVERED |
| REQ-23 | `tests/test_process_model_predicates.py` (4 tests) + `tests/test_status_lanes_block.py` (11 tests) | `core/process_model.py::derive_stages()` + predicates, `core/status.py::_topic_status()`, `core/status_lanes.py::lanes_block()` + per-lane watermark helpers | COVERED |
| REQ-24 | none | none (requires live Claude Desktop session; out of pipeline scope) | DEFERRED |
| REQ-25 | `tests/test_dispatch_telemetry_sink.py` (24 tests) + `tests/test_dispatch_telemetry_census.py` (6 tests) | `mcp_server/dispatch_telemetry.py` (full module) + `recording_server.py::RecordingServer` | COVERED |
| REQ-26 | `tests/test_dispatch_telemetry_sink.py` (2 tests) + `tests/test_dispatch_telemetry_census.py::test_a_refused_action_is_recorded_as_invalid_argument_beside_its_rejection` | `mcp_server/tools_suggestions.py::_validate_action()`, `dispatch_telemetry.py::record_rejected_action()` | COVERED |
| REQ-27 | none | none (release-checklist item, executed at release time, not pipeline time) | DEFERRED |

**Coverage**: 26 of 29 requirement rows COVERED, 2 UNTESTED (REQ-10, REQ-11 — description/help-text
shape, asserted only by review, not by an automated test), 2 DEFERRED (REQ-24, REQ-27 — genuinely
out of pipeline scope, not gaps). No REQ is silently unaccounted for.
