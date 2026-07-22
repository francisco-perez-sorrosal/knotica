# Knotica

**An AI-maintained knowledge wiki that improves itself — without touching model weights.**

Knotica implements [Karpathy's llm-wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f): an LLM does the grunt work of knowledge management — summarizing, cross-referencing, filing, bookkeeping — over a plain-markdown wiki you read and edit in [Obsidian](https://obsidian.md). On top of the pattern, knotica adds a self-improvement stack: the wiki's *operating program* (schemas + operation prompts) is evolved against an objective evaluation metric — [DSPy](https://github.com/stanfordnlp/dspy) optimizing prompts (inner), [SIA](https://github.com/hexo-ai/sia) evolving structure (outer, later).

## How it works

- **The intelligence is your Claude client** for ingest, curate, and exploratory Q&A (`read_protocol` + search/read). Knotica's server exposes deterministic, stateless tools and holds no session state. **Headless paths** — MCP `query`, compile, eval, loop/Arena — use a server-side LLM and need `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` in the environment ([Desktop setup](docs/CLAUDE_DESKTOP.md#headless-llm-credentials-query--compile--eval)).
- **Everything is a file in git.** Pages, schemas, prompts, curated examples, compiled artifacts, and eval metrics all live in the vault. One git commit per mutating operation — a full audit trail, nothing hidden.
- **Per-topic agents, earned divergence.** Each topic directory can carry its own schema overlay, prompt overrides, dataset, and metrics — created only when the topic's data justifies it.
- **The flywheel.** Curated query/answer examples accumulate per topic. At ~30 query-style examples you can **compile** (DSPy) onto a review branch; after merge, `query` silently uses the compiled engine. Arena races prompts when a loop gate fails (reactive heal). Both prove out by asking the same question again.

> [!IMPORTANT]
> **The vault is data; this repo is code.** The wiki lives in a **separate git repo** at a user-configured path (dev default `~/dev/data/knotica`), never inside this repo. All vault access goes through the `VaultStore` abstraction — vault paths are never hardcoded.

## Prerequisites

- **[uv](https://docs.astral.sh/uv/)** — required; launches the MCP server and powers the CLI.
- **git** — the vault is a git repo; one commit per mutating operation.
- **[ripgrep](https://github.com/BurntSushi/ripgrep)** — used for full-text search.
- Python 3.12+ (uv manages the interpreter).
- A Claude client: **Claude Desktop** and/or **Claude Code**.

## Install

Knotica ships through two channels. Both back the same MCP server.

### Channel 1 — Claude Code plugin (recommended for Code)

```
/plugin marketplace add francisco-perez-sorrosal/bit-agora
/plugin install knotica@bit-agora
```

Then `/knotica:setup` (wizard) → open the vault in Obsidian → `/knotica:ingest` / `/knotica:query`.

### Channel 2 — CLI + Claude Desktop (recommended for Desktop Chat)

```bash
uv tool install --from . knotica    # from a repo checkout
knotica init --desktop --yes        # vault + config + Desktop MCP entry
```

**Fully quit and reopen Claude Desktop**, then confirm the `knotica` MCP server is connected.

> **Full Desktop walkthrough (install + AWM prove use case):**  
> **[`docs/CLAUDE_DESKTOP.md`](docs/CLAUDE_DESKTOP.md)**

Desktop gotcha: the config must use the **absolute path to `uvx`** (minimal PATH at launch). `knotica init --desktop` writes that for you. Logs: `~/Library/Logs/Claude/mcp*.log`.

`knotica init` also scaffolds the vault, runs `git init` (optional private `gh` remote), writes `~/.config/knotica/config.toml`, and pre-warms `uvx`.

## First run (either channel)

1. Finish setup (`/knotica:setup` or `knotica init`).
2. Open the scaffolded folder as a vault in Obsidian.
3. **Ask** a grounded question via the MCP `query` tool (or `/knotica:query` in Code).
4. **Curate** good answers (`curate_example` / `/knotica:curate`) until compile-ready.
5. Optional: open the dashboard — Desktop: ask Claude to call `open_dashboard`; or `knotica mcp --http` and browse `http://127.0.0.1:8765/`.

**Optional configuration:** [Eval cadence and model selection](docs/CLAUDE_DESKTOP.md#configuration-models-and-eval-cadence) are available via `~/.config/knotica/config.toml` `[loop]` and `[models]` tables. All keys are optional with defaults; no new required setup.

### Quick Desktop smoke test (demo topic)

The template ships `agentic-systems` with Agent Workflow Memory pages. In Claude Desktop Chat:

1. “Call knotica `open_dashboard` with topic `agentic-systems`.”
2. “Call `query` on `agentic-systems` with: *How does Agent Workflow Memory improve web agents without changing model weights, and what relative gains does it report on Mind2Web and WebArena?*”
3. Expect grounded gains **24.6% Mind2Web** / **51.1% WebArena**, citing `wang2024awm`.

Step-by-step compile → merge → prove: [`docs/CLAUDE_DESKTOP.md`](docs/CLAUDE_DESKTOP.md#real-use-case-agent-workflow-memory-awm).

## Command surface

Plugin aliases (`/knotica:*`) and the `knotica` CLI expose the same operations from two directions.

### Plugin aliases (`/knotica:*`)

| Alias | Purpose |
|-------|---------|
| `/knotica:setup` | Interactive first-run wizard — scaffold a vault, wire the server, pre-warm. |
| `/knotica:ingest <url> [topic]` | Fetch a source, place it by topic, write pages, log. |
| `/knotica:query <question> [topic]` | Answer a question grounded in curated topic pages. |
| `/knotica:lint [topic]` | Lint pages against the schema (links, structure, confidence, supersession). |
| `/knotica:curate [topic] [verdict]` | Curate an example into compile-ready training signal with a verdict. |
| `/knotica:status [topic]` | Show pages per topic, compile-ready count, lint state, unpushed commits. |
| `/knotica:doctor` | Run deterministic health checks; surface warnings and failures. |
| `/knotica:migrate [topic]` | Preview a schema migration (`--dry-run`), then apply. |
| `/knotica:loop <topic>` | Run one self-improvement loop tick (observe → gate → heal). |

### CLI subcommands (`knotica <cmd>`)

| Subcommand | Purpose |
|------------|---------|
| `knotica init` | Scaffold a vault, git init (+ optional remote), write config, register the server (`--desktop`), pre-warm. |
| `knotica mcp` | Serve the MCP server (`stdio` default; `--http` for browser dashboard). |
| `knotica doctor` | Deterministic mechanical health checks (`--quick`, `--json`, repair subcommand). |
| `knotica status` | Deterministic counts — pages/topic, compile-ready, last lint, unpushed (`--json`, `--topic`). |
| `knotica compile` | Phase 3a: clone → MIPROv2/bootstrap → branch `compile/<topic>/<sha>` (no auto-merge). |
| `knotica eval` | Headless golden eval on a clone (`--bootstrap` stages candidates). |
| `knotica datasets bootstrap-train` | Cold-start a topic's trainset from its own pages (LLM-grounded; curation displaces seeds). |
| `knotica loop` | Autonomous watcher: observe default-branch changes (debounced during ingests), auto-baseline (`--baseline-policy latest\|best`, `--rebaseline`), gate `loop/c/*`, arena-heal regressions. |
| `knotica migrate` | Schema-version migration; preview with `--dry-run` or `--check`. |
| `knotica okf` | Native [OKF](docs/okf.md) compatibility — `check`, `export`, `repair`. |
| `knotica prompt` | Render a vault-resolved operation prompt body to stdout. |

## Dashboard

One Preact artifact mounts two ways:

- **Claude Desktop / MCP Apps** — tool `open_dashboard` → `ui://knotica/dashboard`.
- **Browser / Claude Code Browser pane** — `knotica mcp --http --port 8765`.

See [`dashboard/README.md`](dashboard/README.md).

## For AI agents working on this repo

- **Read [`docs/PRE_PLAN.md`](docs/PRE_PLAN.md) first** — it is canonical; this README is a summary. `CLAUDE.md` lists the non-negotiable invariants (client-as-brain, stateless server, vault/code separation, per-op commits, clone-based loops).
- **Desktop users:** [`docs/CLAUDE_DESKTOP.md`](docs/CLAUDE_DESKTOP.md).
- **OKF compatibility:** [`docs/okf.md`](docs/okf.md).
- Repo layout: `src/knotica/` · `vault-template/` · `.claude-plugin/` + `commands/` + `hooks/` + `skills/` + `.mcp.json` · `dashboard/` · `tests/`.
- The wiki vault is **not** in this repo — user-configured path (dev default `~/dev/data/knotica`).
- Python 3.12+, uv-managed; `uv run pytest` for tests. Dashboard: `cd dashboard && npm run build`.

## Source material

Karpathy's [llm-wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) · [rohitg00's improvements](https://gist.github.com/rohitg00/2067ab416f7bbe447c1977edaaa681e2) · [autoresearch](https://github.com/karpathy/autoresearch) · [SIA](https://github.com/hexo-ai/sia) · [DSPy](https://github.com/stanfordnlp/dspy)

## License

MIT
