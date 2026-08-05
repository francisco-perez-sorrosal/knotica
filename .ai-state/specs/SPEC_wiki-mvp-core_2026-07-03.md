# SPEC — knotica wiki-mvp-core (Phases 0–1)

| Field | Value |
|---|---|
| **Feature** | Vault template + core/MCP/CLI/plugin (PRE_PLAN Phases 0–1) |
| **Status** | active (pipeline `wiki-mvp-core` in flight) |
| **Tier** | Standard |
| **Sources** | `docs/PRE_PLAN.md` v7 (§Phases, §Verification), `SYSTEMS_PLAN.md` (§Behavioral Specification — REQ-MUT/CFG/TOOL/REC/PROMPT/PLUGIN/CLI adopted verbatim) and `INTERFACE_DESIGN.md` (§1–6 contracts), both formerly under `.ai-work/wiki-mvp-core/` and **deleted at pipeline cleanup — not recoverable** (see §Traceability) |
| **Date** | 2026-07-03 |

Scope: **Phase 0–1 behaviors only.** Phases 2–5 (evals, DSPy, SIA, remote) are out of scope.
Contract precedence (orchestrator reconciliation, LEARNINGS 2026-07-03): the tool-result envelope and
tool surface are `INTERFACE_DESIGN.md` §1.3–1.6 — success = data + optional `warnings: [...]`; failure =
`{error: {code, message, fix, retryable}}`; `unconfigured` is the `NOT_CONFIGURED` error code, not a
top-level status. The tool set is the ten-tool surface (`list_topics` added; `list_links`/`backlinks`
consolidated into `list_links(direction=out|in|both)` — ADR `dec-draft-11700457`).

Verification classes: **[auto]** = pytest-verifiable; **[drill]** = verified by the Phase-0 manual
session or the Phase-1 cold-start drill (user-involved acceptance gates), not by automated tests.

## Requirements

### Mutation discipline (MUT) — from SYSTEMS_PLAN, verbatim intent

- **REQ-MUT-01** [auto] — *When* any mutating operation is invoked *and* it touches one or more vault
  files, *the system* performs all writes, the `log.md` append, and the git commit inside a single
  flock-guarded transaction yielding exactly one commit, *so that* the audit trail is one-op-per-commit
  and concurrent writers cannot interleave.
- **REQ-MUT-02** [auto] — *When* a mutating operation raises partway through *and* files were already
  written, *the system* restores the working tree to the pre-op commit and releases the lock, *so that*
  a dead mid-op never leaves a dirty/half-committed vault.
- **REQ-MUT-03** [auto] — *When* content is written *and* it contains a detected secret pattern, *the
  system* scrubs it before commit and reports the redacted spans as a `SECRET_SCRUBBED` warning on the
  successful result, *so that* secrets never enter git history.
- **REQ-MUT-04** [auto] — *When* a mutating op commits, *the system* writes the structured commit message
  `knotica(<op>): <topic> — <title>`, *so that* op→commit is machine-recoverable from `git log`.
- **REQ-MUT-05** [auto] — *When* the mutation path is invoked from an MCP tool *or* from the CLI, *the
  system* routes through the identical `core` transaction — `mcp/` and `cli/` contain no direct git or
  vault-write calls (import-boundary fitness test), *so that* every surface shares one discipline.

### Configuration & unconfigured contract (CFG) — from SYSTEMS_PLAN, verbatim intent

- **REQ-CFG-01** [auto] — *When* the server starts *and* no `config.toml` exists, *the system* boots
  without error and every tool/prompt/resource returns the structured `NOT_CONFIGURED` result, *so that*
  the plugin can start the server before setup runs.
- **REQ-CFG-02** [auto] — *When* a tool is invoked, *the system* resolves `config.toml` fresh (per call),
  *so that* a config written after boot takes effect without a restart.
- **REQ-CFG-03** [auto] — *When* config resolves to a path that is missing or not a knotica vault, *the
  system* returns `NOT_CONFIGURED` with the state-specific remediation, *so that* the three failure
  states (no config / bad path / uninitialized vault) collapse to one user-facing contract while
  `doctor` distinguishes them.
- **REQ-CFG-04** [auto] — *When* `default_vault` is set *and* no explicit vault is passed, *the system*
  resolves the default vault's path (with `~`/env expansion), *so that* the common case needs no vault arg.

### Tools (TOOL)

- **REQ-TOOL-01** [auto] — *When* `write_page` / `store_source` / `create_topic` / `curate_example`
  succeed with an effective change, *the system* produces exactly one commit + one log entry each
  (inherits MUT). *When* `write_page` is called with the optional `index_entry` argument, the same
  commit upserts the page's line (full-path wikilink + entry text) in root `index.md` — reserved
  bookkeeping files (`index.md`, `log.md`, `SCHEMA.md`) are maintained only as atomic side effects,
  never as direct write targets (adjudicated 2026-07-03; see INTERFACE_DESIGN §1.1/§1.3 and ADR
  dec-draft-11700457 as amended).
- **REQ-TOOL-02** [auto] — *When* `list_topics` / `read_page` / `search` / `list_links` / `lint_check`
  run, *the system* produces zero commits and never acquires the write lock, *so that* reads are cheap
  and concurrent.
- **REQ-TOOL-03** [auto] — *When* `create_topic` (or a page write) targets a reserved top-level name
  (`sources`, `index.md`, `log.md`, `SCHEMA.md`, `START_HERE.md`, `.knotica`, `.git`, …), *the system*
  refuses with `RESERVED_NAME` listing the reserved set, *so that* the vault namespace stays intact.
- **REQ-TOOL-04** [auto] — *When* `store_source` stores a source, *the system* writes it immutably under
  `sources/<topic>/<citation_key>` with provenance frontmatter (origin URL, retrieved-at, sha256,
  source_type); same key + different content fails with `SOURCE_EXISTS`, *so that* sources are auditable
  and never silently rewritten.
- **REQ-TOOL-05** [auto] — *When* a tool receives a `topic` argument, *the system* uses it verbatim
  (never a cached "current topic"; required-non-empty on mutating tools, empty = all-topics on reads),
  *so that* the server stays stateless.
- **REQ-TOOL-06** [auto] — *When* `list_topics` is called, *the system* returns all existing topic names
  with page counts (unpaginated, bounded set), *so that* the locked topic-inference policy has its
  deterministic read primitive.
- **REQ-TOOL-07** [auto] — *When* a mutating tool is re-invoked with intent whose result-state already
  holds, *the system* makes **no commit** and returns the truthful no-op flag (`changed:false` /
  `existed:true` / `appended:false` / no-op success for identical `store_source`), *so that* retries are
  safe without idempotency keys and the audit log records only effective mutations.

### Error contract (ERR) — INTERFACE_DESIGN §1.4 (single contract source)

- **REQ-ERR-01** [auto] — *When* any tool call fails, *the system* returns
  `{error: {code, message, fix, retryable}}` **in the tool result content** (never only a transport
  exception), with `code` from the fixed enum {`NOT_CONFIGURED`, `TOPIC_NOT_FOUND`, `PAGE_NOT_FOUND`,
  `RESERVED_NAME`, `SOURCE_EXISTS`, `INVALID_FRONTMATTER`, `LOCK_BUSY`, `GIT_ERROR`, `INVALID_CURSOR`}
  and message/fix following "X failed because Y. To fix: Z."; `LOCK_BUSY` is the only `retryable: true`
  code; `SECRET_SCRUBBED` rides as a warning on success, never an error, *so that* the model can
  self-recover in the same turn.
- **REQ-ERR-02** [auto] — *When* any surface is used unconfigured, *the system* presents one uniform
  contract: tools/resources/prompts → `NOT_CONFIGURED`; CLI → exit code `3` + three-part stderr message;
  remediation names `/knotica:setup` (Claude Code) and `knotica init` (CLI), *so that* all five surfaces
  degrade identically.

### Search (SRCH) — INTERFACE_DESIGN §1.6

- **REQ-SRCH-01** [auto] — *When* `search` runs, *the system* returns pointer results (topic, path,
  snippet, score) in the envelope `{results, next_cursor, has_more, total_count}` with an opaque,
  self-contained cursor (no server-side cursor state), default 10 / max 50 per page, and fails a
  malformed/stale cursor with `INVALID_CURSOR`, *so that* responses stay small, the server stateless,
  and the contract survives a future backend swap.

### Record schemas (REC) — frozen at Phase 0 (D3)

- **REQ-REC-01** [auto] — *When* `curate_example` appends to `qa.jsonl`, *the system* writes a record
  carrying `schema_version`, `topic`, `created`, `query`, `pages_used`, `answer`, `citations`, `verdict`,
  `corrected_answer`, `source`, `model`, *so that* Phase-2 golden-QA and Phase-3a DSPy trainsets consume
  it without a template migration.
- **REQ-REC-02** [auto] — *When* the log records an op, *the system* writes the H2 line
  `## [YYYY-MM-DD] <op> | <topic> | <title>` (optional touched-pages bullets beneath), *so that* the log
  is greppable and Obsidian-renderable.
- **REQ-REC-03** [auto] — *When* the root `SCHEMA.md` is authored, *the system* documents the `qa.jsonl`,
  `metrics.jsonl`, log-entry, commit-message, and source-provenance record formats under one versioned
  constitution (`schema_version:`), *so that* `knotica migrate` governs their evolution from a single
  source.

### Prompt / command surface (PROMPT)

- **REQ-PROMPT-01** [auto] — *When* the server registers operation prompts (`ingest`, `query`, `lint`,
  `curate`), *the system* declares static names and resolves bodies lazily from vault
  `.knotica/prompts/<op>.md` (root default, earned topic override) on every `prompts/get`, *so that* the
  UX surface and the DSPy/SIA-evolvable substrate are one artifact.
- **REQ-PROMPT-02** [auto] — *When* an operation prompt resolves configured, *the system* returns the
  full protocol body satisfying the INTERFACE_DESIGN §2.3 checklist (read resolved-schema resource → act
  → update index → log; verbatim topic-inference policy block; exact tool names; curation solicitation on
  `ingest`/`query`; citation discipline on `query`), *so that* the client learns the workflow that tool
  schemas alone cannot teach.
- **REQ-PROMPT-03** [auto] — *When* a prompt is invoked while unconfigured, *the system* returns the
  setup-guidance body (inherits CFG-01), *so that* the surface degrades gracefully.

### Resources (RES) — INTERFACE_DESIGN §5

- **REQ-RES-01** [auto] — *When* resources are read, *the system* serves `knotica://schema/root`,
  `knotica://schema/topic/{topic}`, `knotica://schema/resolved/{topic}` (root ⊕ overlay merged), and
  `knotica://index` as `text/markdown` mirroring vault files (resolved = computed), honoring the
  `NOT_CONFIGURED` contract; `log.md` is deliberately not a resource in Phase 1, *so that* prompts can
  direct the client to the effective schema in one fetch.

### Plugin / cold-start (PLUGIN)

- **REQ-PLUGIN-01** [drill] — *When* the plugin is enabled, *the system*'s `.mcp.json` launches the
  server via `uvx --from ${CLAUDE_PLUGIN_ROOT} knotica mcp` and is never marked `alwaysLoad`, *so that*
  a ~24 s cold env resolution cannot block the MCP startup window.
- **REQ-PLUGIN-02** [drill] — *When* a session starts *and* `uvx` is present, *the system*'s SessionStart
  hook fires a **backgrounded, idempotent** pre-warm (`uvx --from "${CLAUDE_PLUGIN_ROOT}" knotica
  --version`), *so that* cold caches (fresh install, plugin update, cache eviction) warm outside the
  handshake with no fragile cold-cache detector (D5).
- **REQ-PLUGIN-03** [drill] — *When* `uvx` is absent, *the system*'s SessionStart hook prints uv-install
  guidance instead of pre-warming, *so that* the hard prerequisite is surfaced, not silently failed.

### CLI (CLI)

- **REQ-CLI-01** [drill] — *When* `knotica init` (or `--yes`) runs, *the system* scaffolds the vault from
  the packaged template, `git init`s it (+ optional `gh` private remote), writes `config.toml`, and
  patches Claude Code (`claude mcp add`) / Claude Desktop MCP registration writing the **absolute `uvx`
  path** into `claude_desktop_config.json`, *so that* the CLI channel reaches first-ingest with no
  hand-editing. (Scaffold/config/registration-content logic is [auto]; end-to-end channel is [drill].)
- **REQ-CLI-02** [auto] — *When* `knotica migrate` runs, *the system* shows a three-way template diff
  (template-old vs template-new vs user-evolved), never clobbers SIA/DSPy-evolved files, and routes the
  SCHEMA rewrite through the mutation transaction; `--check` exits `4` when a migration is available,
  *so that* upgrades are safe and audited.
- **REQ-CLI-03** [auto] — *When* `knotica mcp` serves stdio, *the system* writes **nothing but JSON-RPC
  to stdout** — all diagnostics/logs go to stderr, *so that* the protocol channel is never corrupted.
- **REQ-CLI-04** [auto] — *When* any CLI command exits, *the system* uses the documented exit codes:
  `0` success (warnings allowed), `1` failure, `2` misuse, `3` not-configured, `4` migration-available
  (`migrate --check` only), *so that* hooks and scripts branch deterministically.
- **REQ-CLI-05** [auto] — *When* `knotica prompt <op>` runs, *the system* renders the vault-resolved
  operation prompt body to stdout via the **same resolver** the MCP prompt handler uses, *so that* the
  vault `prompts/` files remain the single source of truth across MCP-prompt and plugin-alias surfaces.
- **REQ-CLI-06** [auto] — *When* `knotica doctor` runs, *the system* performs deterministic mechanical
  checks only (config sanity, schema resolution, reserved names, broken links, git state incl. dirty-tree
  rollback offer and unpushed-commits warning, MCP registration), reports PASS/WARN/FAIL with the specific
  remediation per failure, supports `--quick`/`--json`, never invokes an LLM, and exits `3` when
  unconfigured, *so that* it can serve as the SessionStart nudge and the future loop harness-guard.
- **REQ-CLI-07** [auto] — *When* `knotica status` runs, *the system* prints deterministic counts —
  pages per topic, curated examples ("N, M to compile-ready"), last lint, unpushed commits — with
  `--json`, *so that* the flywheel has a progress bar.

### Vault template (VLT) — Phase 0

- **REQ-VLT-01** [auto-after-Phase-1: lint fixture / drill at Phase 0] — *When* `vault-template/` is
  instantiated, *the system(template)* provides: root `SCHEMA.md` (invariants + `schema_version:` + the
  frozen REC formats), `index.md`, `log.md`, `START_HERE.md`, vault `.gitignore` (`.obsidian/`, `.trash/`,
  `.DS_Store`; `.knotica/` committed), root `.knotica/prompts/{ingest,query,lint,curate}.md` defaults,
  and an `agentic-systems/` seed topic (overlay `SCHEMA.md`, `.knotica/` scaffold with empty
  `datasets/qa.jsonl` and `prompts/`, `compiled/`; **no** `metrics.jsonl` — Phase-2 producer), *so that*
  Phase 1 code and tests target real schemas.
- **REQ-VLT-02** [auto] — *When* the template ships, *the system(template)* keeps all user-facing content
  (`START_HERE.md`, demo pages) out of — and never wikilinking into — dot-folders, *so that* Obsidian's
  hard dot-path ignore hides nothing the user needs.
- **REQ-VLT-03** [auto] — *When* the demo-ingest sample (Agent Workflow Memory, arXiv 2409.07429) ships,
  *the system(template)* includes its stored source under `sources/agentic-systems/`, its entity pages,
  and matching `index.md` + `log.md` entries obeying every frozen format, clearly marked deletable,
  *so that* session 1 shows a populated graph and documents a completed ingest by example.
- **REQ-VLT-04** [drill] — *When* the template is instantiated at `~/dev/data/knotica` (git init +
  private remote) and opened in Obsidian, *the system(vault)* renders pages, backlinks, the graph, and a
  Dataview `TABLE` over the seed frontmatter, and survives a manual ingest/query/lint session on 2–3 real
  seed-corpus papers (ReAct, Darwin Gödel Machine) honoring per-op-commit + log disciplines with plain
  file tools only, *so that* Phase 1 crystallizes only conventions Phase 0 proved.

### Acceptance gates (DRILL) — Phase-1 exit

- **REQ-DRILL-01** [drill] — *When* a clean Claude Code environment runs `/plugin marketplace add` →
  `/plugin install` → `/knotica:setup` → `/knotica:ingest <paper>`, *the system* reaches a committed
  ingested page with no manual config beyond opening the Obsidian vault; `knotica doctor` green;
  SessionStart nudges fire when config is missing, *so that* the plugin channel is proven end-to-end.
- **REQ-DRILL-02** [drill] — *When* a clean machine runs `uv tool install` from the git URL →
  `knotica init --yes` → ingest in Claude Desktop, *the system* succeeds with the absolute `uvx` path
  written into `claude_desktop_config.json`; `knotica doctor` green, *so that* the CLI fallback channel
  is proven end-to-end.

## Key Decisions

Draft ADRs governing this spec (finalized ids assigned at merge-to-main):
`dec-draft-6ea4e4f3` (SDK: official `mcp`), `dec-draft-9039d858` (single mutation path),
`dec-draft-6ab0db31` (config/unconfigured contract), `dec-draft-e5cf9cf1` (record-schema freeze),
`dec-draft-75ee2605` (pre-warm), `dec-draft-11700457` (tool decomposition), `dec-draft-14fe025b`
(error grammar + idempotency), `dec-draft-189be0f4` (cursor pagination), `dec-draft-8d8c18a1`
(adapter package decomposition — planner).

## Traceability

> **⚠ RE-DERIVED, NOT CONTEMPORANEOUS — 2026-08-04.**
> The original live map (`.ai-work/wiki-mvp-core/traceability.yml`, plus the `LEARNINGS.md` / `WIP.md`
> drill checkpoints in the same directory) was **deleted at `.ai-work/` cleanup before the archival
> bake-in ran**. That evidence is unrecoverable. The matrix below was reconstructed on **2026-08-04**
> by reading each REQ's behavioral statement and searching the *current* suite and source tree for the
> test that asserts that behavior — REQ → behavior → test, never REQ → filename similarity.
> It therefore describes **today's coverage of these behaviors**, not the coverage that existed at
> Phase-1 exit. Do not read a `COVERED` row as evidence that the test predates the REQ, nor an
> `UNTESTED` row as evidence the behavior was never verified — the Phase-0/1 drills genuinely ran;
> only their written record is gone.
> Recorded as ledger row `td-034` (sentinel finding I4).

**Derivation method.** Mappings were derived from test names, module docstrings, and spot-reads of
assertion bodies — not by executing each test against each REQ. The suite has grown well past this
feature (2453 tests / 170 files at re-derivation time), so several REQs map to tests written after
Phase 1. Rows whose link rests on a behavioral judgment rather than an explicit assertion are marked
`(inferred)`. Parametrized cases are cited by bare function name; the `[param]` suffixes are elided.

**Status vocabulary.**

| Status | Meaning |
|---|---|
| `COVERED` | At least one test asserts this REQ's behavior directly. |
| `PARTIAL` | The principal behavior is asserted, but a **named clause of the REQ has no assertion**. The gap is stated in the row. |
| `UNTESTED` | No automated test asserts this behavior. |
| `DRILL` | `[drill]`-classed REQ with no automated coverage by design; original manual evidence is unrecoverable. |

### Matrix

| Requirement | Test(s) | Implementation | Status |
|---|---|---|---|
| **REQ-MUT-01** one flock-guarded transaction → exactly one commit | `tests/test_transaction.py::test_effective_write_makes_exactly_one_commit`, `::test_commit_touches_exactly_the_page_and_log`, `::test_one_log_entry_appended_per_operation`, `::test_multi_page_write_is_one_commit_with_all_pages`, `::test_concurrent_transaction_raises_retryable_lock_busy`; `tests/test_lock.py::test_a_second_acquirer_gets_busy_within_the_timeout_bound`, `::test_contention_holds_across_real_processes`, `::test_nested_acquisition_in_the_same_process_contends_not_reenters` | `core/transaction.py`, `core/lock.py`, `core/vcs.py` | COVERED |
| **REQ-MUT-02** mid-op raise → restore + release | `tests/test_transaction.py::test_rollback_removes_a_new_page_and_makes_no_commit`, `::test_rollback_after_partial_writes_restores_each_written_path`, `::test_rollback_restores_a_modified_tracked_page`, `::test_lock_released_after_rollback_lets_next_transaction_succeed`, `::test_exception_inside_block_writes_nothing`, `::test_lock_released_when_block_raises`; `tests/test_vcs.py::test_rollback_restores_named_paths_to_their_state_at_the_ref`, `::test_rollback_deletes_paths_created_since_the_ref` | `core/transaction.py`, `core/vcs.py` | COVERED |
| **REQ-MUT-03** secret scrubbed pre-commit, reported as `SECRET_SCRUBBED` warning | `tests/test_scrub.py::test_real_credential_formats_are_redacted_with_a_span_report`, `::test_the_span_report_never_carries_the_secret_itself`, `::test_legitimate_research_content_passes_through_untouched`, `::test_every_secret_in_a_document_is_redacted_with_ordered_spans`, `::test_only_the_assigned_value_is_redacted_never_the_variable_name`, `::test_scrubbing_is_idempotent`, `::test_clean_text_passes_through_with_an_empty_span_report`; `tests/test_transaction.py::test_real_key_is_committed_scrubbed_with_spans_on_the_result`, `::test_false_positive_corpus_is_committed_verbatim`; `tests/test_op_write_page.py::test_real_key_is_redacted_in_committed_content`, `::test_real_key_redaction_is_surfaced_as_a_warning`; `tests/test_mcp_write.py::test_write_page_with_a_secret_succeeds_and_warns`; `tests/test_errors.py::test_secret_scrubbed_cannot_be_constructed_as_an_error`, `::test_the_warning_type_carries_the_secret_scrubbed_code` | `core/scrub.py`, `core/transaction.py`, `core/errors.py` | COVERED |
| **REQ-MUT-04** frozen commit-subject grammar | `tests/test_transaction.py::test_commit_subject_follows_the_frozen_grammar`, `::test_construction_rejects_a_bad_commit_grammar_before_any_lock`; `tests/test_vcs.py::test_commit_message_round_trips_the_frozen_grammar`; `tests/test_records.py::test_commit_subject_round_trips_through_the_frozen_grammar`, `::test_live_vault_commit_corpus_parses_with_the_expected_operation`, `::test_non_knotica_and_malformed_subjects_are_rejected` | `core/records.py`, `core/transaction.py` | COVERED |
| **REQ-MUT-05** `mcp/` and `cli/` hold no direct git/vault-write calls (fitness test) | `tests/test_architecture_boundaries.py::test_adapters_do_not_shell_out_for_git`, `::test_adapters_do_not_import_the_vault_lock`, `::test_adapters_do_not_call_mutating_store_methods`, `::test_adapters_do_not_call_mutating_vcs_methods`, `::test_adapters_do_not_perform_raw_filesystem_writes`, `::test_core_transaction_is_the_only_caller_of_mutating_vcs_methods`, `::test_adapters_may_read_git_state` | `mcp_server/`, `cli/`, `core/transaction.py` | COVERED — the scanner's `ADAPTER_PACKAGES` is `("cli", "mcp_server", "evals")`, a superset of the REQ's two surfaces |
| **REQ-CFG-01** boots unconfigured; every surface returns `NOT_CONFIGURED` | `tests/test_mcp_read.py::test_server_imports_and_builds_without_vault_access`, `::test_every_read_tool_returns_not_configured_when_unconfigured`; `tests/test_mcp_status.py::test_status_tools_return_not_configured_when_unconfigured`; `tests/test_mcp_resources.py::test_unconfigured_resource_read_surfaces_setup_guidance`; `tests/test_mcp_prompts.py::test_unconfigured_get_prompt_returns_setup_guidance`; `tests/test_prompts.py::test_unconfigured_invocation_serves_setup_guidance_instead_of_failing`; `tests/test_cli_mcp.py::test_mcp_boots_over_stdio_even_when_the_vault_is_unconfigured` | `core/config.py`, `mcp_server/vault_ctx.py`, `mcp_server/server.py` | COVERED |
| **REQ-CFG-02** config resolved fresh per call | `tests/test_config.py::test_config_written_after_a_failed_resolve_takes_effect_without_reload`, `::test_config_rewrite_between_calls_redirects_to_the_new_vault`; `tests/test_mcp_read.py::test_config_written_mid_session_takes_effect_on_next_call`; `tests/test_prompts.py::test_configuration_written_after_a_guidance_response_takes_effect_without_restart`, `::test_prompt_edited_between_calls_is_served_fresh`; `tests/test_schema.py::test_overlay_edited_between_calls_is_served_fresh` | `core/config.py`, `mcp_server/vault_ctx.py` | COVERED |
| **REQ-CFG-03** three failure states collapse to one contract; `doctor` distinguishes | `tests/test_config.py::test_missing_config_file_reports_not_configured_with_setup_remediation`, `::test_config_pointing_at_a_missing_path_reports_not_configured`, `::test_a_directory_that_is_not_an_initialized_vault_reports_not_configured`, `::test_no_config_and_bad_path_carry_distinct_remediations`, `::test_the_module_distinguishes_the_three_internal_states`, `::test_malformed_toml_reports_not_configured_not_a_parser_crash`; `tests/test_schema.py::test_vault_without_a_root_constitution_reports_not_configured`; `tests/test_cli_doctor.py::test_bad_config_is_not_a_clean_exit_and_names_the_vault_problem` | `core/config.py`, `core/doctor.py` | COVERED |
| **REQ-CFG-04** `default_vault` resolves with `~`/env expansion | `tests/test_config.py::test_default_vault_pointing_at_an_initialized_vault_resolves_its_root`, `::test_explicit_vault_argument_overrides_the_default_vault`, `::test_tilde_in_vault_path_expands_to_the_current_home`, `::test_environment_variables_in_vault_path_expand_at_resolution_time`, `::test_missing_default_vault_key_reports_not_configured`, `::test_unknown_vault_name_reports_not_configured_naming_the_vault`; `tests/test_vault_targeting.py::test_core_tool_rejects_an_unknown_vault_name`, `::test_naming_the_configured_default_resolves_the_same_vault`; `tests/test_cli_init.py::test_default_vault_path_resolves_through_home_and_env_expansion` | `core/config.py`, `core/config_write.py` | COVERED |
| **REQ-TOOL-01** one commit + one log per effective mutation; `index_entry` upserts in the same commit; bookkeeping files never a direct target | `tests/test_op_write_page.py::test_valid_page_is_written_in_exactly_one_commit`, `::test_index_entry_upserts_this_pages_catalog_line`, `::test_index_upsert_leaves_every_other_catalog_line_untouched`, `::test_index_upsert_is_part_of_the_same_single_commit`, `::test_index_entry_updates_an_existing_pages_line_without_adding_a_duplicate`; `tests/test_op_create_topic.py::test_new_topic_makes_exactly_one_commit_with_a_clean_tree`, `::test_new_topic_adds_a_catalog_entry_to_root_index`; `tests/test_op_store_source.py::test_store_makes_exactly_one_commit_with_a_clean_tree`; `tests/test_op_curate_example.py::test_curate_makes_exactly_one_commit_following_the_frozen_grammar`; `tests/test_transaction.py::test_write_rejects_the_reserved_log_path` | `core/operations/`, `core/index_catalog.py`, `core/transaction.py` | COVERED |
| **REQ-TOOL-02** reads produce zero commits and never take the write lock | `tests/test_mcp_read.py::test_reads_make_zero_git_commits`; `tests/test_links.py::test_link_queries_never_commit_or_dirty_the_vault`; `tests/test_lint.py::test_lint_makes_no_commit_and_leaves_a_clean_tree`; `tests/test_mcp_status.py::test_wiki_status_is_read_only`; `tests/test_architecture_boundaries.py::test_adapters_do_not_import_the_vault_lock`; `tests/test_lint.py::test_a_core_read_path_completes_while_the_write_lock_is_held` | `mcp_server/tools_read.py`, `core/lint.py`, `core/links.py`, `search/` | COVERED — the never-locks clause is now behavioral for `core`, not only structural for adapters: the read runs with the mutation lock held, and `vault_lock` is not re-entrant (a second acquire raises `LockBusyError`), so an acquiring implementation would fail rather than pass |
| **REQ-TOOL-03** reserved top-level names refused with `RESERVED_NAME` | `tests/test_op_create_topic.py::test_reserved_top_level_name_is_refused`; `tests/test_op_write_page.py::test_reserved_page_name_is_refused_with_reserved_name`; `tests/test_mcp_write.py::test_write_page_targeting_a_reserved_file_returns_reserved_name`, `::test_create_topic_with_reserved_name_returns_reserved_name`; `tests/test_vault_scaffold.py::test_reserved_topic_name_raises_reserved_name`, `::test_reserved_topic_names_is_the_same_object_as_vault_layouts_set`; `tests/test_vault_layout.py::test_reserved_top_level_names_is_a_frozenset_of_the_six_pre_existing_names_plus_notes`; `tests/test_lint.py::test_top_level_directory_with_reserved_name_is_flagged`, `::test_reserved_names_are_declared_exactly_once` | `core/vault_layout.py`, `core/operations/`, `core/lint.py` | COVERED — **drift**: the reserved set has since gained `notes` (see `test_notes_is_a_member_of_reserved_top_level_names`) |
| **REQ-TOOL-04** sources immutable under `sources/<topic>/<key>` with provenance; `SOURCE_EXISTS` on conflict | `tests/test_op_store_source.py::test_source_is_persisted_under_the_sources_tree`, `::test_provenance_records_origin_type_and_body_digest`, `::test_stored_body_round_trips_to_the_passed_content`, `::test_conflicting_restore_fails_with_source_exists_and_writes_nothing`, `::test_identical_restore_makes_no_new_commit`; `tests/test_mcp_write.py::test_store_source_persists_under_sources_topic_key`, `::test_storing_same_key_different_content_returns_source_exists`; `tests/test_records.py::test_provenance_file_round_trips_fields_and_body_byte_exactly`, `::test_rendered_provenance_frontmatter_carries_exactly_the_frozen_field_set`, `::test_provenance_missing_required_fields_is_rejected`, `::test_template_source_digest_covers_exactly_the_post_frontmatter_bytes` | `core/operations/store_source.py`, `core/records.py` | COVERED |
| **REQ-TOOL-05** `topic` used verbatim, never cached; required-non-empty on mutations, empty = all-topics on reads | `tests/test_schema.py::test_topic_names_are_used_stripped_but_otherwise_verbatim`, `::test_non_topic_shaped_names_are_rejected_before_touching_the_vault`; `tests/test_search.py::test_empty_topic_searches_all_topics`, `::test_named_topic_excludes_every_other_topic`, `::test_malformed_topic_names_are_refused`; `tests/test_mcp_status.py::test_metrics_read_rejects_empty_topic`; `tests/test_mcp_write.py::test_write_page_to_missing_topic_returns_topic_not_found`; `tests/test_op_curate_example.py::test_curating_into_a_missing_topic_is_refused`; `tests/test_mcp_write.py::test_a_mutating_tool_refuses_an_empty_topic_and_makes_no_commit` | `core/schema.py`, `search/`, `mcp_server/vault_ctx.py` | COVERED — all four mutating tools, both `""` and whitespace, asserted refused with no commit. Note: they refuse in three different shapes (two typed envelopes, two raw exception strings); the safety clause holds, the envelope divergence is ledgered as td-041 |
| **REQ-TOOL-06** `list_topics` returns every topic with page counts, unpaginated | `tests/test_mcp_read.py::test_list_topics_reports_the_template_topic_with_a_page_count` | `mcp_server/tools_read.py` | COVERED (inferred) — names + counts asserted by one test; the "unpaginated / bounded set" clause is structural (the tool takes no cursor argument), not asserted |
| **REQ-TOOL-07** re-invoked intent → no commit + truthful no-op flag | `tests/test_transaction.py::test_identical_content_is_a_no_op`, `::test_noop_writes_no_log_entry`, `::test_only_changed_pages_of_a_mixed_write_are_committed`; `tests/test_op_write_page.py::test_identical_rewrite_makes_no_new_commit`; `tests/test_op_create_topic.py::test_recreating_an_existing_topic_is_a_no_op`, `::test_creating_a_fresh_topic_reports_it_did_not_already_exist`; `tests/test_op_store_source.py::test_identical_restore_makes_no_new_commit`; `tests/test_op_curate_example.py::test_a_duplicate_example_is_not_appended`; `tests/test_mcp_write.py::test_recreating_an_existing_topic_is_a_no_op`, `::test_rewriting_identical_page_content_is_a_no_op`, `::test_storing_same_source_key_and_content_is_a_no_op`, `::test_duplicate_curated_example_is_not_appended`; `tests/test_cli_migrate.py::test_reapplying_an_applied_migration_is_a_noop` | `core/transaction.py`, `core/operations/` | COVERED |
| **REQ-ERR-01** `{error:{code,message,fix,retryable}}` in the tool result, fixed enum, `LOCK_BUSY` sole retryable | `tests/test_errors.py::test_the_code_enum_matches_the_contract_code_set_exactly`, `::test_constructed_error_round_trips_all_four_contract_fields`, `::test_error_envelope_shape_matches_the_wire_contract`, `::test_default_retryable_flag_mirrors_the_contract_table`, `::test_every_error_code_carries_a_default_fix_text`, `::test_lock_busy_fix_tells_the_model_to_retry`, `::test_secret_scrubbed_cannot_be_constructed_as_an_error`; `tests/test_mcp_read.py::test_error_object_carries_typed_retryable_and_enum_code` | `core/errors.py`, `mcp_server/envelope.py` | COVERED — **drift**: the enum has grown from the 9 codes frozen here to 17 (`INVALID_ARGUMENT`, `LLM_API_ERROR`, `SEARCH_API_ERROR`, `SUGGESTION_NOT_FOUND`, `SUGGESTION_NOT_APPROVED`, `NOTE_NOT_FOUND`, `ANCHOR_DEGRADED` added by Phases 2–4). The enum-exactness test tracks the *current* contract, not this REQ's frozen set |
| **REQ-ERR-02** one uniform unconfigured contract across all five surfaces | `tests/test_errors.py::test_not_configured_default_fix_names_both_setup_paths`; `tests/test_config.py::test_missing_config_file_reports_not_configured_with_setup_remediation`; `tests/test_cli_status.py::test_unconfigured_vault_exits_three_with_the_setup_remediation`; `tests/test_cli_doctor.py::test_unconfigured_vault_exits_three_with_the_setup_remediation`; `tests/test_cli_prompt.py::test_unconfigured_prompt_exits_three_and_mirrors_the_setup_remediation`; `tests/test_mcp_read.py::test_every_read_tool_returns_not_configured_when_unconfigured`; `tests/test_mcp_resources.py::test_unconfigured_resource_read_surfaces_setup_guidance`; `tests/test_mcp_prompts.py::test_unconfigured_get_prompt_returns_setup_guidance` | `core/errors.py`, `cli/common.py`, `mcp_server/vault_ctx.py` | COVERED |
| **REQ-SRCH-01** pointer results, `{results,next_cursor,has_more,total_count}`, opaque self-contained cursor, 10/50, `INVALID_CURSOR` | `tests/test_search.py::test_envelope_and_pointer_render_shapes_match_the_tool_contract`, `::test_default_page_size_is_ten`, `::test_limit_above_the_maximum_is_clamped_to_fifty`, `::test_cursor_walk_covers_the_whole_corpus_without_duplicates_or_gaps`, `::test_walk_envelope_flags_flip_exactly_at_the_end`, `::test_two_identical_walks_return_identical_pages`, `::test_cursor_tokens_round_trip_their_pagination_state`, `::test_garbage_cursors_are_rejected_with_the_typed_error`, `::test_a_cursor_minted_for_a_different_query_is_stale`, `::test_invalid_cursor_is_a_value_error_so_adapters_can_map_it`, `::test_cursor_validation_guards_every_backend_not_just_ripgrep`, `::test_the_pagination_contract_holds_for_a_non_filesystem_backend`; `tests/test_mcp_read.py::test_search_returns_pointer_results_with_the_pagination_envelope`, `::test_search_limit_one_paginates_with_a_usable_next_cursor`, `::test_search_malformed_cursor_returns_invalid_cursor` | `search/cursor.py`, `search/ripgrep.py`, `search/retrieval.py` | COVERED — the "survives a backend swap" intent is directly proven by the `CannedBackend` protocol tests |
| **REQ-REC-01** `qa.jsonl` record carries the frozen field set | `tests/test_records.py::test_qa_line_round_trips_with_identical_fields`, `::test_qa_line_carries_exactly_the_frozen_field_set`, `::test_qa_answer_with_embedded_newlines_stays_a_single_jsonl_line`, `::test_each_verdict_round_trips_with_its_corrected_answer`, `::test_malformed_qa_lines_are_rejected`, `::test_future_qa_schema_version_with_unknown_fields_still_parses`, `::test_appended_records_read_back_in_order_with_prior_bytes_untouched`; `tests/test_op_curate_example.py::test_appends_a_field_complete_qa_record`, `::test_appends_preserve_prior_records_and_order` | `core/records.py`, `core/operations/curate_example.py` | COVERED |
| **REQ-REC-02** log H2 line `## [YYYY-MM-DD] <op> \| <topic> \| <title>` | `tests/test_records.py::test_log_entry_with_bullets_round_trips_byte_identically`, `::test_log_heading_without_bullets_round_trips`, `::test_rendered_log_heading_satisfies_the_independent_grammar`, `::test_template_log_corpus_re_renders_byte_identically`, `::test_malformed_log_headings_are_rejected`; `tests/test_template.py::test_template_log_entries_obey_the_frozen_grammar`, `::test_log_entry_bullets_point_at_files_that_exist`; `tests/test_transaction.py::test_one_log_entry_appended_per_operation` | `core/records.py`, `core/transaction.py` | COVERED |
| **REQ-REC-03** root `SCHEMA.md` documents all five record formats under one `schema_version:` | `tests/test_schema.py::test_root_constitution_reads_with_its_version_and_frontmatter_stripped_body`, `::test_effective_schema_version_is_the_root_constitutions`; `tests/test_lint.py::test_missing_root_schema_version_is_flagged`, `::test_overlay_schema_version_conflict_is_flagged`; `tests/test_template.py::test_template_instantiates_with_the_full_root_and_topic_inventory`; `tests/test_records.py::test_root_constitution_documents_all_five_frozen_record_formats` | `vault-template/SCHEMA.md`, `core/schema.py`, `core/lint.py` | COVERED — the constitution is now read back and its five numbered record sections asserted, so the document can no longer drop or rename one while the record tests pass against their own copy of the grammars |
| **REQ-PROMPT-01** four static prompt names, bodies resolved lazily from the vault (root default ⊕ earned override) | `tests/test_prompts.py::test_the_prompt_surface_is_exactly_the_four_locked_operations`, `::test_prompt_paths_mirror_schema_resolution_shape`, `::test_root_default_is_served_when_the_topic_has_no_override`, `::test_topic_override_wins_once_divergence_is_earned`, `::test_an_override_for_one_operation_leaves_the_others_on_root_defaults`, `::test_unresolvable_topics_fall_through_to_the_root_default`, `::test_unknown_operation_is_rejected_naming_the_valid_ones`, `::test_missing_root_prompt_file_is_a_typed_error_not_a_silent_fallback`, `::test_prompt_edited_between_calls_is_served_fresh`; `tests/test_mcp_prompts.py::test_prompt_list_is_static_and_names_the_four_operations`, `::test_prompt_body_reflects_a_vault_edit_between_two_gets` | `core/prompts.py`, `mcp_server/prompts.py` | COVERED |
| **REQ-PROMPT-02** prompt body satisfies the INTERFACE_DESIGN §2.3 checklist | `tests/test_prompts.py::test_operation_bodies_embed_the_topic_inference_policy_verbatim`, `::test_flywheel_operations_solicit_curation`, `::test_operation_bodies_point_at_the_resolved_schema_resource`; `tests/test_mcp_prompts.py::test_shipped_prompts_reference_tools`, `::test_every_prompt_tool_reference_is_a_registered_tool`; `tests/test_prompts.py::test_the_query_prompt_body_mandates_citation_discipline` | `vault-template/.knotica/prompts/`, `core/prompts.py` | COVERED for citation discipline — asserted on the mandatory phrasing and on the grounding clause (`not from memory`). The `read → act → update index → log` **ordering** is still unasserted as a sequence |
| **REQ-PROMPT-03** unconfigured prompt → setup guidance | `tests/test_prompts.py::test_unconfigured_invocation_serves_setup_guidance_instead_of_failing`, `::test_configuration_written_after_a_guidance_response_takes_effect_without_restart`; `tests/test_mcp_prompts.py::test_unconfigured_get_prompt_returns_setup_guidance`; `tests/test_cli_prompt.py::test_unconfigured_prompt_exits_three_and_mirrors_the_setup_remediation` | `core/prompts.py`, `mcp_server/prompts.py` | COVERED |
| **REQ-RES-01** four resources as `text/markdown`; `resolved` = root ⊕ overlay; `log.md` deliberately absent | `tests/test_mcp_resources.py::test_static_schema_and_index_resources_are_advertised`, `::test_parameterised_schema_resources_are_advertised_as_templates`, `::test_each_resource_reads_as_non_empty_markdown`, `::test_resolved_resource_equals_the_root_overlay_merge`, `::test_resolved_resource_carries_the_topic_overlay_content`, `::test_log_is_not_exposed_as_a_resource`, `::test_unconfigured_resource_read_surfaces_setup_guidance`; `tests/test_schema.py::test_overlay_refinements_read_after_the_root_so_they_take_precedence`, `::test_merged_document_names_each_layers_file_as_provenance` | `mcp_server/resources.py`, `core/schema.py` | COVERED |
| **REQ-PLUGIN-01** `[drill]` `.mcp.json` launches via `uvx --from ${CLAUDE_PLUGIN_ROOT} knotica mcp`, never `alwaysLoad` | `tests/test_plugin_manifest.py` (4 tests) | `.mcp.json` | COVERED — the launch line is asserted argument-for-argument, exactly one server is declared, and `alwaysLoad` is asserted absent over the raw bytes |
| **REQ-PLUGIN-02** `[drill]` backgrounded, idempotent SessionStart pre-warm | `tests/test_hooks_session_start.py::test_warm_path_makes_exactly_one_status_nudge_call_and_stays_fast`; `tests/test_hooks_session_start.py::test_the_prewarm_invokes_the_plugin_root_version_check_in_the_background`, `::test_the_prewarm_never_writes_to_the_vault_so_it_is_idempotent` | `hooks/session_start.sh`, `hooks/hooks.json` | COVERED — the pre-warm command, its `--from "$ROOT"` target, subshell backgrounding, and stdin/stdout detachment are asserted; idempotence is pinned as a property of the command (a pure read) rather than of a guard around it |
| **REQ-PLUGIN-03** `[drill]` `uvx` absent → uv-install guidance, not silent failure | `tests/test_hooks_session_start.py::test_uvx_missing_skips_the_nudge_entirely` (asserts `"needs uv"` is printed and no nudge subprocess runs) | `hooks/session_start.sh` | COVERED — **reclassify**: this REQ was specified `[drill]` but has since acquired automated coverage |
| **REQ-CLI-01** `knotica init` scaffolds + `git init` + config + client registration with absolute `uvx` path | `tests/test_cli_init.py::test_init_scaffolds_a_bare_vault_and_writes_a_resolvable_config`, `::test_running_init_twice_leaves_the_vault_and_config_intact`, `::test_desktop_patch_merges_preserving_servers_and_writes_absolute_launch_with_backup`, `::test_mcp_from_source_falls_back_to_cwd_when_file_is_outside_the_checkout`, `::test_mcp_from_source_falls_back_to_package_name_outside_any_checkout`, `::test_init_writes_nothing_outside_the_temp_sandbox`; `tests/test_cli_desktop.py::test_install_writes_the_entry_without_touching_config_toml`, `::test_install_is_idempotent_and_preserves_the_credentials_env_block`, `::test_a_no_op_run_does_not_overwrite_the_backup_of_the_original`, `::test_install_reports_the_file_and_the_key_it_writes`, `::test_status_reports_the_entry_without_modifying_it`; `tests/test_vault_scaffold.py::test_scaffolds_a_fresh_vault_with_template_git_and_commit`, `::test_rescaffolding_an_initialized_vault_is_idempotent_and_does_not_clobber` | `cli/init.py`, `cli/desktop.py`, `core/vault_scaffold.py`, `core/config_write.py` | COVERED for the `[auto]` half (scaffold / config / registration content). The `[drill]` end-to-end channel and the **optional `gh` private-remote** step have no automated evidence |
| **REQ-CLI-02** `migrate` three-way diff, never clobbers evolved files, SCHEMA rewrite through the transaction, `--check` exits `4` | `tests/test_cli_migrate.py::test_up_to_date_migrate_makes_no_commit`, `::test_up_to_date_check_reports_success`, `::test_stale_check_reports_migration_available`, `::test_dry_run_shows_diff_without_writing`, `::test_evolved_file_preserved_byte_identical`, `::test_applied_migration_commits_once_with_grammar_and_log`, `::test_reapplying_an_applied_migration_is_a_noop` | `cli/migrate.py`, `core/operations/migrate.py` | COVERED |
| **REQ-CLI-03** `knotica mcp` writes only JSON-RPC to stdout | `tests/test_cli_mcp.py::test_mcp_stdout_carries_only_jsonrpc_during_a_handshake`, `::test_mcp_routes_diagnostics_to_stderr_not_stdout`, `::test_mcp_boots_over_stdio_even_when_the_vault_is_unconfigured` | `cli/mcp.py`, `mcp_server/server.py` | COVERED |
| **REQ-CLI-04** documented exit codes `0/1/2/3/4` | `0`: `tests/test_cli_doctor.py::test_healthy_vault_has_no_failing_check_and_exits_zero`; `1`: `tests/test_cli_doctor.py::test_bad_config_is_not_a_clean_exit_and_names_the_vault_problem`; `2`: `tests/test_cli_prompt.py::test_unknown_operation_is_misuse_and_exits_two`, `tests/test_cli_init.py::test_no_input_without_a_vault_path_fails_fast_with_misuse_exit`, `tests/test_cli_desktop.py::test_bare_desktop_command_is_a_usage_error`; `3`: `tests/test_cli_status.py::test_unconfigured_vault_exits_three_with_the_setup_remediation`, `tests/test_cli_doctor.py::test_unconfigured_vault_exits_three_with_the_setup_remediation`, `tests/test_cli_prompt.py::test_unconfigured_prompt_exits_three_and_mirrors_the_setup_remediation`; `4`: `tests/test_cli_migrate.py::test_stale_check_reports_migration_available` | `cli/common.py` | COVERED — **drift**: the table has since gained `5` (`EXIT_NO_GOLDEN_SET`, `eval` only). No single test pins the whole table; each code is covered by its own command's test |
| **REQ-CLI-05** `knotica prompt <op>` renders through the same resolver as the MCP handler | `tests/test_cli_prompt.py::test_prompt_output_equals_the_shared_resolver_body`, `::test_prompt_renders_the_earned_topic_override`, `::test_prompt_accepts_the_source_flag_and_still_serves_the_resolver_body`; `tests/test_prompts.py::test_both_entry_points_serve_byte_identical_bodies`, `::test_an_earned_override_is_served_through_the_shared_entry_point`, `::test_malformed_vault_error_propagates_through_the_shared_entry_point` | `cli/prompt.py`, `core/prompts.py` | COVERED |
| **REQ-CLI-06** `doctor` runs mechanical LLM-free checks, PASS/WARN/FAIL + remediation, `--quick`/`--json`, exit `3` | `tests/test_cli_doctor.py::test_healthy_vault_has_no_failing_check_and_exits_zero`, `::test_bad_config_is_not_a_clean_exit_and_names_the_vault_problem`, `::test_unresolved_schema_flags_the_schema_check_with_remediation`, `::test_reserved_name_collision_flags_the_reserved_row_with_rename_remediation`, `::test_broken_wikilink_flags_the_links_row_as_unresolved`, `::test_page_citing_an_unstored_source_flags_the_citations_row`, `::test_dirty_working_tree_flags_a_git_row_and_offers_fix`, `::test_unpushed_commit_flags_a_git_row_with_push_remediation`, `::test_quick_is_a_strict_subset_of_the_full_check_set`, `::test_json_output_is_stable_machine_readable`, `::test_unconfigured_vault_exits_three_with_the_setup_remediation`, `::test_doctor_fix_points_at_repair_not_restore_dot`; `tests/test_mcp_vault.py::test_doctor_run_matches_cli_json_shape`; `tests/test_cli_doctor.py::test_importing_doctor_never_pulls_in_an_llm_client`, `::test_the_check_set_includes_the_mcp_registration_row` | `core/doctor.py`, `cli/doctor.py` | COVERED — LLM-freedom pinned structurally in a fresh interpreter (the module cannot call what it cannot import, which a behavioral test cannot prove for paths it does not take), and the host-dependent `mcp` row's presence pinned separately from its verdict |
| **REQ-CLI-07** `status` prints deterministic counts with `--json` | `tests/test_cli_status.py::test_page_count_equals_fixture_ground_truth`, `::test_page_count_tracks_an_added_page`, `::test_topic_scoping_counts_only_the_named_topic`, `::test_json_output_is_stable_machine_readable`, `::test_unconfigured_vault_exits_three_with_the_setup_remediation`; `tests/test_cli_status.py::test_curated_example_count_tracks_a_real_curated_example`, `::test_last_lint_reports_the_latest_lint_date_recorded_in_the_log`, `::test_unpushed_counts_commits_the_remote_has_not_seen` | `core/status.py`, `cli/status.py` | COVERED — all three counts asserted non-vacuously against ground truth: a real curated example, a real lint log entry, and a real unpushed commit against a real upstream |
| **REQ-VLT-01** template ships the full root + seed-topic inventory (and no `metrics.jsonl`) | `tests/test_template.py::test_template_instantiates_with_the_full_root_and_topic_inventory`, `::test_topic_qa_dataset_ships_empty`, `::test_no_metrics_file_ships_in_the_template`, `::test_vault_gitignore_keeps_agent_state_committed_but_ignores_app_state`, `::test_fresh_vault_has_exactly_one_initial_commit_and_a_clean_tree`, `::test_fresh_vault_baseline_carries_no_operation_commits`; `tests/test_op_create_topic.py::test_new_topic_creates_its_directory_and_schema_overlay`, `::test_new_topic_scaffolds_an_empty_qa_dataset`, `::test_new_topic_does_not_create_metrics_jsonl`, `::test_new_topic_scaffolds_empty_prompts_and_compiled_dirs`; `tests/test_vault_scaffold.py::test_scaffold_with_a_topic_seeds_only_that_topic_and_stays_bare` | `vault-template/`, `core/template.py`, `core/vault_scaffold.py` | COVERED — the inventory test asserts the REQ's enumeration item-for-item. The REQ's `[auto-after-Phase-1]` promise was kept |
| **REQ-VLT-02** user-facing content never lives in, nor wikilinks into, dot-folders | `tests/test_template.py::test_user_facing_pages_never_link_into_dot_folders`; `tests/test_lint.py::test_link_into_dot_folder_is_flagged`; `tests/test_links.py::test_page_scan_covers_the_inventory_and_skips_dot_folders_and_non_markdown`; `tests/test_search.py::test_dot_folders_dot_files_and_non_markdown_are_never_returned` | `vault-template/`, `core/links.py`, `core/lint.py` | COVERED |
| **REQ-VLT-03** demo-ingest sample ships complete, format-conformant, and marked deletable | `tests/test_template.py::test_demo_entity_pages_carry_schema_conformant_frontmatter`, `::test_demo_pages_are_clearly_marked_deletable`, `::test_demo_source_carries_the_frozen_provenance_frontmatter`, `::test_index_catalogs_every_demo_entity_page_with_full_path_links`, `::test_template_log_entries_obey_the_frozen_grammar`, `::test_log_entry_bullets_point_at_files_that_exist`; `tests/test_lint.py::test_demo_pages_are_all_indexed`, `::test_pristine_template_is_mechanically_clean`; `tests/test_links.py::test_template_graph_totals_twenty_links_all_resolved`, `::test_demo_anchor_page_collects_backlinks_from_index_and_both_entity_pages`, `::test_template_outbound_counts_match_the_hand_counted_graph` | `vault-template/` | COVERED |
| **REQ-VLT-04** `[drill]` Obsidian render (pages, backlinks, graph, Dataview) + manual ingest/query/lint session on real papers | *none* — Obsidian rendering and the manual session are outside pytest's reach | `vault-template/` | DRILL — original evidence lived in the deleted `.ai-work/wiki-mvp-core/LEARNINGS.md`; **unrecoverable**. Partial mechanical proxies exist for the link-graph half: `tests/test_links.py::test_template_graph_totals_twenty_links_all_resolved`, `tests/test_lint.py::test_pristine_template_is_mechanically_clean`. The Obsidian/Dataview render half has no proxy |
| **REQ-DRILL-01** `[drill]` plugin channel end-to-end (marketplace → install → setup → ingest) | *none* | `.claude-plugin/`, `commands/`, `hooks/` | DRILL — original evidence unrecoverable. No automated proxy: the marketplace/install path is not exercised by any test |
| **REQ-DRILL-02** `[drill]` CLI channel end-to-end (`uv tool install` → `init --yes` → Desktop ingest) | *none* | `cli/init.py`, `cli/desktop.py` | DRILL — original evidence unrecoverable. Partial mechanical proxies for the registration step: `tests/test_cli_init.py::test_desktop_patch_merges_preserving_servers_and_writes_absolute_launch_with_backup`, `tests/test_cli_desktop.py::test_install_writes_the_entry_without_touching_config_toml`. The `uv tool install` and live-ingest steps have no proxy |

### Coverage summary (re-derived 2026-08-04; gaps closed 2026-08-05)

| Status | Count | Requirements |
|---|---|---|
| `COVERED` | 39 | MUT-01…05, CFG-01…04, TOOL-01…07, ERR-01, ERR-02, SRCH-01, REC-01…03, PROMPT-01…03, RES-01, PLUGIN-01…03, CLI-01…07, VLT-01…03 |
| `PARTIAL` | 0 | — |
| `UNTESTED` | 0 | — |
| `DRILL` (no automated coverage by design; evidence lost) | 3 | VLT-04, DRILL-01, DRILL-02 |
| **Total** | **42** | |

**The eight gaps the 2026-08-04 re-derivation surfaced are closed (td-037).** Two caveats travel
with that, because a summary row reading `0` is exactly where a reader stops looking:

- **REQ-PROMPT-02 is covered for citation discipline, not for everything it names.** The
  `read → act → update index → log` ordering is still unasserted as a *sequence*; the row says so.
  It is counted `COVERED` because its principal clause — the one with user-visible consequence — now
  has an assertion, not because nothing is left.
- **Writing the REQ-TOOL-05 assertion surfaced a new defect rather than confirming a clean one.**
  All four mutating tools refuse an empty topic, but in three different shapes: `create_topic` and
  `curate_example` return the typed `{"error": {code, message, fix, retryable}}` envelope, while
  `write_page` and `store_source` return a raw exception string. The safety clause holds; the
  envelope contract does not. Ledgered as `td-041` rather than frozen into this matrix.

One row is marked `(inferred)` — **TOOL-06**, where the "unpaginated / bounded set" clause is
structural rather than asserted. Every other mapping rests on a test whose assertion (or name plus
spot-read body) states the REQ's behavior directly.

**Contract drift observed while re-deriving** (recorded here so a future reader does not mistake the
spec for current truth): the ten-tool surface of this spec is now 33 tools behind seven
action-parameterized dispatchers (`dec-050` / `dec-045`); the `ERR-01` nine-code enum is now 17
codes; the `CLI-04` exit table has gained `5`; the `TOOL-03` reserved set has gained `notes`. The
underlying **behaviors** these REQs specify all still hold — only the frozen enumerations grew.
