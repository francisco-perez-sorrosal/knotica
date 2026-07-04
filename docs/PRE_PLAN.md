# Knotica — LLM-Wiki MVP: Converged Design (v7)

## Context

Knotica implements Karpathy's **llm-wiki** pattern — an AI-maintained, compounding markdown knowledge base in an Obsidian vault — with per-topic self-improving agents. Self-improvement follows autoresearch's methodology (immutable harness + editable artifact + objective metric + keep/discard) as **two nested loops**: **DSPy** (inner, metric-driven prompt optimization) and **SIA** (outer, structural/schema evolution). No model weights are modified — the system's "weights" are its schemas and prompts. Python backend (uv); MCP as the sole interface (stdio local, streamable HTTP + OAuth remote); Obsidian as the frontend.

Version history: v3 = converged architecture (2026-07-02/03). v4 = first-run experience (init wizard, MCP prompts, doctor, CLI shape). v5 = **Claude plugin marketplace as primary distribution channel** (verified against plugin-dev references), five first-run decisions locked, install dry-run fixes, scenario-reflection gap fixes (layered doctor, status view, flock guard, `create_topic`, vault `.gitignore`). v6 = consistency pass: dual command surface (MCP prompts canonical + plugin-command aliases), static-name/lazy-body prompt registration, **stateless server principle**, clone-based loops even locally, Phase 0 declared software-free. v7 = research-verified updates (pipeline `wiki-mvp-core` in flight): all five third-party assumptions confirmed by the researcher (RESEARCH_FINDINGS.md); cold-start pre-warm requirement, Desktop absolute-path gotcha, process-lifetime softening, primary-surface decision, seed corpus locked. Phases 0–1 run as a **Standard-tier praxion pipeline** seeded by this document.

## Two repositories

| Repo | Path | Contents | Git remote |
|---|---|---|---|
| Code | `~/dev/knotica` | Python package + Claude plugin/marketplace, MCP server, evals, vault template | GitHub (public/private, user's call) |
| Vault | `~/dev/data/knotica` | The wiki itself; Obsidian points here | **Private** GitHub remote — required as the sync channel for remote loops |

## Vault layout (topics at root)

```
~/dev/data/knotica/            # vault root = git repo
  .gitignore                   # .obsidian/ + .DS_Store (device state); .knotica/ IS committed
  SCHEMA.md                    # root constitution (invariants; schema_version:)
  index.md                     # global catalog
  log.md                       # append-only op log
  START_HERE.md                # onboarding page (visible in Obsidian)
  .knotica/prompts/            # default operation prompts (topic overrides earn divergence)
  sources/                     # immutable raw sources (per-topic subdirs)
  <topic>/                     # e.g. agentic-systems/
    SCHEMA.md                  # topic overlay (visible in Obsidian; schema_version:)
    .knotica/                  # topic agent state (hidden from Obsidian)
      datasets/qa.jsonl        # curated query/answer examples (flywheel)
      prompts/                 # operation prompt overrides (earned divergence)
      compiled/                # DSPy-compiled artifacts
      metrics.jsonl            # per-generation eval history
    <pages>.md
```

Reserved top-level names (`sources`, `index.md`, `log.md`, `SCHEMA.md`, `START_HERE.md`, `.knotica`, `.git`, …) may not be topic names — lint-enforced.

## Seed topic sketch: `agentic-systems/` (papers)

- **Root `SCHEMA.md` invariants**: wikilink syntax; core frontmatter (`type`, `topic`, `created`, `updated`, `confidence`, `sources: []`, `status: active|stale`, `supersedes`/`superseded_by`, `tags`); `log.md` entry format (`## [YYYY-MM-DD] <op> | <title>`); cross-topic linking rules; one git commit per mutating op; secret-scrub on ingest.
- **Topic overlay**: entity types `paper`, `method`, `system`, `benchmark`, `concept`, `person-or-lab`; page template (Summary → Key claims with citations → Relations → Open questions); ingest rule: source PDFs/md into `sources/agentic-systems/` under citation-key names; each paper ingest touches its entity pages + index + log.
- Divergence is earned: new topics start with an empty overlay inheriting root.
- **Seed corpus (locked from research, 2026-07-03):** ReAct ([2210.03629](https://arxiv.org/abs/2210.03629), agent architectures, ar5iv HTML), Darwin Gödel Machine ([2505.22954](https://arxiv.org/abs/2505.22954), self-improvement, native HTML), Agent Workflow Memory ([2409.07429](https://arxiv.org/abs/2409.07429), memory, native HTML, short — **the demo-ingest sample**). Ingestion ergonomics: native arxiv HTML > ar5iv HTML > PDF.

## Settled design decisions

1. **Hierarchical schema**: root invariants + per-topic overlays that extend but never contradict root (lint-enforced).
2. **Client-as-brain MVP**: MCP server exposes deterministic tools; the MCP client's LLM performs ingest/query/lint guided by schemas. Holds identically over stdio and remote HTTP — the brain is always the client. Headless work (scheduled lint, DSPy compiles, SIA generations) is the only thing needing server-side LLM access (Phase 3a+).
   - **Stateless server (corollary):** the server holds no session state — no "active topic," no cursors, no memory between calls. The only state is the vault (git) and `config.toml`, both resolved per call. Tools that need a topic take it as an explicit argument (the client infers and passes it, e.g. `curate_example(topic=…)`). This is what makes concurrent sessions, restarts, and the local→remote lift trivially safe.
3. **Two-loop self-improvement**: DSPy owns prompts (query op first — retrieve→synthesize-with-citations; trainsets = per-topic `qa.jsonl`; optimizer MIPROv2/GEPA; artifacts to `.knotica/compiled/`). SIA owns structure (knotica as SIA custom task parameterized by topic; feedback agent may invoke DSPy compile as a move). No DSPy on SIA's own agents.
4. **Model policy**: Sonnet-class models for loop workers; stronger model only for LLM-as-judge. Eval scalar = QA accuracy + citation validity + lint violations **− token-cost penalty**.
5. **Flywheel**: conversational curation only for MVP — `curate_example` logs (query, pages used, answer, verdict) to the topic's `qa.jsonl`. Periodic transcript distillation deferred.
6. **Cheap rohitg00 conventions only**: supersession markers, confidence field, log + git audit trail, secret-scrub. Hybrid search/typed graph/memory tiers/quality gating/mesh deferred (~100 pages/topic trigger).
7. **Obsidian = frontend**: plain markdown + wikilinks + frontmatter; atomic writes (temp+rename); no Obsidian plugin. `VaultStore` abstraction with configurable root (Archil-ready).

## First-run experience (frictionless path)

Design target: empty machine → first ingested page with **zero knotica-owned API keys** (client-as-brain uses the user's existing Claude subscription until Phase 3a).

### Primary channel: Claude plugin marketplace (Claude Code)

The GitHub repo is **both the Python package and a Claude plugin** (`.claude-plugin/plugin.json` alongside `pyproject.toml`); it is published through the external **`bit-agora`** marketplace (`francisco-perez-sorrosal/bit-agora`) rather than acting as its own marketplace. Install:

```
/plugin marketplace add francisco-perez-sorrosal/bit-agora
/plugin install knotica@bit-agora
```

- **MCP server auto-registration, zero separate install:** plugin `.mcp.json` launches the server from its own checkout — `{"knotica": {"command": "uvx", "args": ["--from", "${CLAUDE_PLUGIN_ROOT}", "knotica", "mcp"]}}`. uvx resolves the env on first run; no PyPI needed.
- **Setup wizard as a command** (plugins have no install-time interactivity): `/knotica:setup` uses AskUserQuestion to collect vault path, first topic name, git-remote preference; then drives `knotica init` (scaffold vault incl. `START_HERE.md`, git init + optional `gh` private remote, write config file) and can also patch `claude_desktop_config.json` for Claude Desktop. Prints the one manual step: open the folder as a vault in Obsidian.
- **SessionStart hooks (proactive nudges):** missing config → "run `/knotica:setup`"; plugin version vs vault `schema_version` mismatch → suggest `/knotica:migrate`; `knotica doctor --quick` warnings (e.g., dirty vault tree from a dead mid-ingest → offer rollback to last per-op commit).
- **Plugin skill `wiki-maintenance/`:** deep llm-wiki methodology via progressive disclosure, auto-activating when the client works the wiki — keeps operation prompts thin; the skill is one more SIA-evolvable artifact.
- Commands pre-allow the server's tools (`mcp__plugin_knotica_knotica__*` naming).

**Install dry-run findings (design requirements):**
- **Graceful unconfigured boot + lazy config:** the plugin starts the MCP server at enable time, *before* any config/vault exists — the server must boot cleanly in unconfigured state (tools return "not configured — run `/knotica:setup`") and resolve `config.toml` **per tool call**, so setup takes effect without a session restart.
- **`uv` is a hard prerequisite** the plugin cannot install: SessionStart hook checks `command -v uvx` and prints install instructions if absent.
- **Cold-start pre-warm (research-measured, 2026-07-03):** first `uvx --from` env resolution took **24.4 s** with fastmcp-class deps (warm: 0.04–0.2 s) — enough to trip Claude Code's MCP startup window on first launch after install/update. Therefore: `/knotica:setup` AND the SessionStart hook (on cold-cache detection) pre-warm via `uvx --from "${CLAUDE_PLUGIN_ROOT}" knotica --version` outside the MCP handshake; **never set `alwaysLoad`** on the knotica server. Each plugin update changes `${CLAUDE_PLUGIN_ROOT}` and re-pays one cold resolution (`/reload-plugins` repoints).
- **Confirmed versions (2026-07-03):** `mcp` 1.28.1 / `fastmcp` 3.4.2 (both verified adequate for every requirement incl. Phase-4 HTTP+OAuth). Dependency policy: floors, not pins. **Which package is the architect's opening decision** (orchestrator lean: official `mcp` — lighter deps shrink the cold start; counterargument: jlowin's in-memory test client).
- **`store_source` tool**: fetch/copy a source into `sources/<topic>/` immutably, with provenance frontmatter + git commit — ingest must not rely on the client scattering files by hand.
- **Curation must be solicited:** the flywheel won't fill itself — the query/ingest prompts end by offering to save the interaction as a curated example (one keystroke, `curate_example`).

### Fallback channel: CLI (Claude Desktop-only users, non-plugin setups)

`uv tool install` from the same repo → `knotica init` interactive wizard (`--yes` for defaults) does everything `/knotica:setup` does, incl. Claude Code `claude mcp add` and Desktop config patch. Same engine, two entry points. **Desktop gotcha (research-verified):** Desktop launches servers with a minimal PATH — init writes the **absolute `uvx` path** into `claude_desktop_config.json` (`~/Library/Application Support/Claude/`, full app restart required); doctor checks Desktop logs at `~/Library/Logs/Claude/mcp*.log`.

### First use (both channels)

The four operations — **ingest, query, lint, curate** — ship as a **dual command surface with a single source of truth**:

- **MCP prompts (canonical, both clients):** registered by the server; surface as prompt templates in Claude Desktop and as `mcp__`-namespaced slash commands in Claude Code. **Names register statically; bodies resolve lazily from the vault per invocation** — consistent with graceful unconfigured boot (an unconfigured invocation returns the "run setup" message) and with per-call config resolution.
- **Plugin command aliases (ergonomics, Claude Code only):** thin `commands/*.md` shims providing the clean names `/knotica:ingest|query|lint|curate|setup|doctor|status|migrate`; each injects the same vault-resolved prompt body via the CLI. **The aliases are the documented primary surface in Claude Code** (research-verified raw prompt names render as `/mcp__plugin_knotica_knotica__<op>` — functional but not advertised).
- Both surfaces read the same vault `prompts/` files that DSPy/SIA later evolve — UX surface and self-improvement substrate are one artifact.

Each operation prompt carries the full protocol (read schema resource → act → update index → append log) — tool schemas alone don't teach the workflow, and MCP resources aren't auto-loaded.

- **Topic inference policy (locked):** the operation prompt instructs the client to list existing topics and infer placement — auto-place when clearly matching an existing topic; ask the user when ambiguous or when a new topic seems warranted (then `create_topic`). Explicit `topic=` override always wins; the client always passes the resolved topic explicitly to tools (stateless server).
- **Prompt resolution mirrors schema resolution:** defaults at vault root `.knotica/prompts/`; topic overrides once divergence is earned.

### Configuration & migration (locked decisions)

- **Config file (required):** `~/.config/knotica/config.toml` (default vault + named vaults), written by setup/init. It is the keystone of the plugin channel: `.mcp.json` is static, so the server discovers the vault via config, not CLI args.
- **Schema versioning:** root and topic `SCHEMA.md` carry `schema_version:`; `knotica migrate` shows template-diff and **never clobbers SIA/DSPy-evolved files** (three-way: template-old vs template-new vs user-evolved).
- **Safety net — doctor activation model (layered):** doctor runs only *deterministic* mechanical checks (config sanity, schema resolution, reserved names, broken links, git state incl. dirty-tree rollback offer and **unpushed-commits warning**, MCP registration). Layers: (a) on-demand `knotica doctor` / `/knotica:doctor`; (b) SessionStart hook `--quick`; (c) **harness guard** — automatically pre/post every DSPy/SIA run from Phase 3 on (a loop never starts from or leaves a broken vault). No periodic daemon in MVP: the vault only mutates during sessions until headless loops exist, so SessionStart is the right cadence. *Semantic* checks (contradictions, staleness) are `/knotica:lint` — LLM-driven, client-run; scheduled agent-mode later.
- **Status view:** `/knotica:status` + `knotica status` — deterministic counts: pages per topic, curated examples ("N to compile-ready"), last lint, unpushed commits. The flywheel's progress bar.
- **Demo ingest (locked: yes, 2026-07-03):** one tiny pre-processed sample paper in the template, clearly deletable — session 1 shows a populated graph, and it documents by example what a completed ingest looks like.

**CLI shape:** single `knotica` entry point with subcommands `init`, `mcp` (serve stdio; later `--http`), `doctor`, `status`, `migrate`, plus `eval` (Phase 2) and `compile --topic` (Phase 3a) as the loops' manual triggers — scheduling arrives later.

## Deployment topology: local Obsidian + remote loops

**Locality decision (final): Phases 0–3 run entirely locally** — including the DSPy and SIA loops. Phase 4 (Railway) begins only once everything runs smoothly with local MCP servers end-to-end. The loops operate on a git clone either way, so the lift to remote is a relocation, not a redesign.

- **Git is the only sync channel.** Path-ownership eliminates conflicts:
  - Content (pages, index, log): **local-owned**, single writer on main.
  - Loop outputs (`.knotica/compiled/`, `metrics.jsonl`, schema-diff proposals): **loop-owned**, produced on a clone, returned as **branches — never direct commits to main**; human reviews and merges (locally until the private remote exists, as PRs after).
- **Loops always work on a clone — even locally.** The live vault is never a loop's working tree; a Phase-3 local run clones to a temp dir, runs generations there, and returns a branch. This keeps the path-ownership rules and the doctor harness-guard airtight, and makes the Phase-4 lift a pure relocation.
- Remote loops run on snapshots (`git pull` at job start) — a feature, not staleness: the autoresearch pattern requires a frozen harness for comparable runs.
- **Local/remote symmetry**: loops operate on a git clone either way → develop locally in Phase 3, lift to Railway in Phase 4 unchanged.
- **Remote interactive use is content-read-only** until Phase 5 (two-writer problem otherwise). Remote `curate_example` writes, if ever needed early, go to a branch/inbox.
- Archil, when mature on macOS, may replace git-sync with a shared filesystem; per-path single-writer discipline persists.

## Code repository shape

```
~/dev/knotica/              # dual-role: Python package + Claude plugin (published via the bit-agora marketplace)
  pyproject.toml            # uv, Python 3.12+
  .claude-plugin/
    plugin.json             # plugin manifest (distributed through francisco-perez-sorrosal/bit-agora)
  .mcp.json                 # launches server via uvx --from ${CLAUDE_PLUGIN_ROOT}
  commands/                 # /knotica:setup, doctor, migrate (thin shims over CLI)
  hooks/hooks.json          # SessionStart: config nudge, schema-version check, doctor --quick
  skills/wiki-maintenance/  # deep llm-wiki methodology (progressive disclosure)
  docs/PRE_PLAN.md          # this document
  src/knotica/
    core/                   # vault model, page ops, schema resolution (root+overlay), lint checks
    store/                  # VaultStore protocol; LocalFS impl
    search/                 # pluggable: ripgrep/BM25 now
    cli/                    # `knotica` entry point: init (wizard), mcp (serve), doctor, migrate
    mcp/                    # FastMCP server: stdio + streamable HTTP; tools, resources, prompts
    programs/               # DSPy modules (query first) — Phase 3a
    agent/                  # headless runners — Phase 3a+
    evals/                  # SIA-compatible evaluator, golden-set tooling
  vault-template/           # root SCHEMA.md, index.md, log.md, START_HERE.md, sources/, agentic-systems/
  tests/
```

## Phases & execution

**Status: pipeline `wiki-mvp-core` in flight (2026-07-03).** Kickoff done (repo initialized, worktree `pipeline/wiki-mvp-core`, TASK_BRIEF); researcher complete (all five third-party assumptions verified — see `.ai-work/wiki-mvp-core/RESEARCH_FINDINGS.md`); next: systems-architect + interface-designer (shadow).

- **Phase 0 — Vault + schemas (deliberately software-free).** No knotica code runs in this phase: the client (Claude Code) follows `SCHEMA.md` conventions using plain file tools, manually honoring the per-op-commit and log disciplines. Author `vault-template/` (root `SCHEMA.md`, overlay mechanism, `agentic-systems` seed topic, `START_HERE.md`, `.gitignore`, demo-ingest sample: one pre-processed paper with its pages/index/log entries); instantiate at `~/dev/data/knotica`; `git init` vault + private remote; point Obsidian at it. Exercise ingest/query/lint on 2–3 real papers; confirm Obsidian rendering (graph, backlinks, Dataview). Phase 1 then crystallizes only conventions Phase 0 proved.
- **Phase 1 — Core + MCP (stdio) + flywheel + first-run UX.** Plugin layer: `.claude-plugin/` manifest + marketplace, `.mcp.json` (uvx-from-plugin-root launch), `/knotica:setup` wizard command, SessionStart hooks (config nudge, `schema_version` check, `doctor --quick`), `wiki-maintenance` skill. CLI layer: `knotica init` wizard (fallback channel, incl. Desktop config patch), `knotica doctor`, `knotica migrate`, config file `~/.config/knotica/config.toml`. Core: topic-aware `VaultStore`; tools `read_page`, `write_page` (atomic + git commit + log + secret-scrub), `store_source` (immutable + provenance), `create_topic` (deterministic overlay + `.knotica/` scaffolding), `search`, `list_links`/`backlinks`, `lint_check`, `curate_example(topic=…)`; mutating ops flock-guarded (**load-bearing**: research found stdio servers may be long-lived and shared across sessions — the design must not rely on process-per-session, nor on process-per-user); graceful unconfigured boot + per-call config resolution; schemas + index as MCP resources; **dual command surface** (MCP prompts canonical, static names + lazy vault-resolved bodies; plugin command aliases in Claude Code). Verified in Claude Code (plugin channel) and Claude Desktop (CLI channel).
- **Phase 2 — Eval harness.** Frozen corpus; per-topic golden QA (seeded from curated examples); evaluator → one scalar per topic (incl. token-cost penalty); `metrics.jsonl`. Baseline Phase-0 schemas.
- **Phase 3a — DSPy inner loop.** `query` as DSPy program; per-topic compile gated on dataset size (≥ ~30–50 examples); optimized `wiki_query` tool; server gains optional LLM access. Runs locally first.
- **Phase 3b — SIA outer loop.** SIA custom task + evaluator; generations mutate overlays/prompts/structure, optionally invoking DSPy; keep/discard on eval score; winning diffs land as vault-repo PRs.
- **Phase 4 — Remote.** Streamable HTTP + OAuth 2.1; Railway deploy; loops move to remote clone + branch-return, unchanged. **Gated on Phases 0–3 running smoothly locally (MCP servers, evals, both loops).**
- **Phase 5+ — Scale.** Hybrid search, typed graph, quality scoring, event-driven wiki automation (rohitg00; distinct from Claude plugin hooks), mesh/multi-writer, Archil-backed vault, content-level page-rewriting loop, transcript distillation.

## Verification

- Phase 0: full ingest/query/lint session from Claude Code on real papers; Obsidian renders pages/links/Dataview; vault pushes to private remote.
- Phase 1: **cold-start drill on a clean machine/account**, both channels: (a) plugin — `/plugin marketplace add` → `/plugin install` → `/knotica:setup` → `/knotica:ingest <paper>` succeeds in Claude Code with no manual config beyond the Obsidian step; (b) CLI — `uv tool install` from git URL → `knotica init --yes` → ingest succeeds in Claude Desktop. `knotica doctor` green in both; SessionStart nudges fire when config is missing; every tool exercised end-to-end; `pytest` over core ops; one git commit per mutating op; `curate_example` appends valid JSONL.
- Phase 2: evaluator runs headless with a stable scalar on the frozen corpus.
- Phase 3a: compiled query program beats uncompiled baseline on held-out examples.
- Phase 3b: one full `sia run` yields generation artifacts + a schema-diff PR with an eval delta.

## Out of scope (MVP)

Vector/hybrid search, typed graph store, memory tiers, Ebbinghaus decay, quality-score gating, mesh/multi-writer sync, Obsidian plugin, Archil mounting, custom web frontend, transcript distillation, DSPy on SIA's own agents, remote interactive content writes.

## Source material

- [Karpathy — llm-wiki](https://gist.githubusercontent.com/karpathy/442a6bf555914893e9891c11519de94f/raw/ac46de1ad27f92b28ac95459c782c07f6b8c964a/llm-wiki.md) — the base pattern (ingest/query/lint, index.md, log.md, schema doc, Obsidian + git).
- [rohitg00 — llm-wiki improvements](https://gist.githubusercontent.com/rohitg00/2067ab416f7bbe447c1977edaaa681e2/raw/266b02033a9adca0cd896bd39465c7a67a72fdb0/llm-wiki.md) — staged upgrade menu (confidence, supersession, hybrid search, tiers, mesh).
- [karpathy/autoresearch](https://github.com/karpathy/autoresearch) — the improvement-loop methodology (immutable harness / editable artifact / metric / keep-discard).
- [hexo-ai/sia](https://github.com/hexo-ai/sia) — the outer-loop implementation (meta/target/feedback agents, custom tasks + evaluators, Claude Agent SDK).
- [DSPy](https://github.com/stanfordnlp/dspy) — the inner-loop prompt optimizer (signatures/modules, MIPROv2/GEPA).
- [Archil](https://archil.com) — deferred shared-filesystem candidate for the vault (macOS mounting not yet reliable).
