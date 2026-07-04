# Knotica

**An AI-maintained knowledge wiki that improves itself — without touching model weights.**

Knotica implements [Karpathy's llm-wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f): an LLM does the grunt work of knowledge management — summarizing, cross-referencing, filing, bookkeeping — over a plain-markdown wiki you read and edit in [Obsidian](https://obsidian.md). On top of the pattern, knotica adds a self-improvement stack inspired by [autoresearch](https://github.com/karpathy/autoresearch)'s methodology: the wiki's *operating program* (schemas + operation prompts) is evolved against an objective evaluation metric by two nested loops — [DSPy](https://github.com/stanfordnlp/dspy) optimizing prompts (inner), [SIA](https://github.com/hexo-ai/sia) evolving structure (outer).

## How it works

- **The intelligence is your Claude client.** Knotica's server exposes deterministic, stateless tools; it holds no session state and needs no API keys of its own. The client's LLM does all cognitive work (ingest, query, lint, curate) guided by the vault's schemas and prompts.
- **Everything is a file in git.** Pages, schemas, prompts, curated examples, and eval metrics all live in the vault. One git commit per mutating operation — a full audit trail, nothing hidden.
- **Per-topic agents, earned divergence.** Each topic directory can carry its own schema overlay, prompt overrides, dataset, and metrics — created only when the topic's data justifies it.
- **The flywheel.** As you use the wiki, curated query/answer examples accumulate per topic and become the training and evaluation fuel for the self-improvement loops.

> [!IMPORTANT]
> **The vault is data; this repo is code.** The wiki lives in a **separate git repo** at a user-configured path (dev default `~/dev/data/knotica`), never inside this repo. All vault access goes through the `VaultStore` abstraction — vault paths are never hardcoded.

## Prerequisites

- **[uv](https://docs.astral.sh/uv/)** — required; launches the MCP server and powers the CLI.
- **git** — the vault is a git repo; one commit per mutating operation.
- **[ripgrep](https://github.com/BurntSushi/ripgrep)** — used for full-text search.
- Python 3.12+ (uv manages the interpreter).

## Install

Knotica ships through two channels. Both back the same MCP server.

### Channel 1 — Claude Code plugin (recommended)

```
/plugin marketplace add francisco-perez-sorrosal/knotica
/plugin install knotica@knotica
```

### Channel 2 — CLI (also covers Claude Desktop)

```
uv tool install --from . knotica    # from a repo checkout
knotica init                          # scaffold a vault, wire the server
```

`knotica init` scaffolds the vault, runs `git init` (optionally creating a private `gh` remote), writes `~/.config/knotica/config.toml`, registers the MCP server, and pre-warms the environment. Pass `--yes` for a non-interactive run.

## First run

After installing via the plugin channel:

1. `/knotica:setup` — guided wizard: vault path, first topic, optional git remote, pre-warm.
2. Open the scaffolded folder as a vault in Obsidian.
3. `/knotica:ingest <url-or-file>` — fetch a source, place it by topic, write pages, log the operation.
4. `/knotica:query <question>` — get an answer grounded in the curated topic pages.

## Command surface

Eight `/knotica:*` plugin aliases and the `knotica` CLI expose the same operations from two directions.

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

The operation aliases (`ingest`, `query`, `lint`, `curate`) inject the vault-resolved prompt body so the vault's `prompts/` files stay the single source of truth for both the UX surface and the DSPy/SIA substrate.

### CLI subcommands (`knotica <cmd>`)

| Subcommand | Purpose |
|------------|---------|
| `knotica init` | Scaffold a vault, git init (+ optional remote), write config, register the server, pre-warm. |
| `knotica mcp` | Serve the MCP server over stdio (JSON-RPC on stdout; logs on stderr). |
| `knotica doctor` | Deterministic mechanical health checks (`--quick`, `--json`, `--fix`). |
| `knotica status` | Deterministic counts — pages/topic, compile-ready, last lint, unpushed commits (`--json`, `--topic`). |
| `knotica migrate` | Schema-version migration; preview with `--dry-run` or `--check`, apply with `--yes`. |
| `knotica prompt` | Render a vault-resolved operation prompt body to stdout (backs the operation aliases). |

## Status

Phases 0–1 (vault template + core / MCP / plugin layer) are implemented; the self-improvement loops (DSPy, SIA) land in later phases. The converged design lives in [`docs/PRE_PLAN.md`](docs/PRE_PLAN.md) — the single authoritative source for architecture, phases, and decisions. Developers should start with the [architecture guide](docs/architecture.md).

## For AI agents working on this repo

- **Read [`docs/PRE_PLAN.md`](docs/PRE_PLAN.md) first** — it is canonical; this README is a summary. `CLAUDE.md` lists the non-negotiable invariants (client-as-brain, stateless server, vault/code separation, per-op commits, clone-based loops).
- Repo layout: `src/knotica/` (`core/`, `store/`, `search/`, `cli/`, `mcp_server/`) · `vault-template/` (scaffolded into user vaults) · `.claude-plugin/` + `commands/` + `hooks/` + `skills/` + `.mcp.json` (the Claude plugin layer) · `tests/`.
- The wiki vault is **not** in this repo — it lives at a user-configured path (dev default `~/dev/data/knotica`) and is its own git repo.
- Python 3.12+, uv-managed; `uv run pytest` for tests.

## Source material

Karpathy's [llm-wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) · [rohitg00's improvements](https://gist.github.com/rohitg00/2067ab416f7bbe447c1977edaaa681e2) · [autoresearch](https://github.com/karpathy/autoresearch) · [SIA](https://github.com/hexo-ai/sia) · [DSPy](https://github.com/stanfordnlp/dspy)

## License

MIT
