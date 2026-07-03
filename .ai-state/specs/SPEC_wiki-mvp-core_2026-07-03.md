# SPEC — knotica wiki-mvp-core (Phases 0–1)

| Field | Value |
|---|---|
| **Feature** | Vault template + core/MCP/CLI/plugin (PRE_PLAN Phases 0–1) |
| **Status** | active (pipeline `wiki-mvp-core` in flight) |
| **Tier** | Standard |
| **Sources** | `docs/PRE_PLAN.md` v7 (§Phases, §Verification), `.ai-work/wiki-mvp-core/SYSTEMS_PLAN.md` (§Behavioral Specification — REQ-MUT/CFG/TOOL/REC/PROMPT/PLUGIN/CLI adopted verbatim), `.ai-work/wiki-mvp-core/INTERFACE_DESIGN.md` (§1–6 contracts) |
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
  (inherits MUT).
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

Live map: `.ai-work/wiki-mvp-core/traceability.yml` (REQ → plan steps → tests → implementation).
The rendered matrix (Requirement | Test(s) | Implementation | Status) is baked into this section at
feature-end archival, before `.ai-work/` cleanup. `[drill]` REQs resolve to drill evidence recorded in
`LEARNINGS.md` / `WIP.md` checkpoints, not pytest node ids.
