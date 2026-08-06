# Knotica

**An AI-maintained knowledge wiki that improves itself — without touching model weights.**

Knotica implements [Karpathy's llm-wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f): an LLM does the grunt work of knowledge management — summarizing, cross-referencing, filing, bookkeeping — over a plain-markdown wiki you read and edit in [Obsidian](https://obsidian.md). On top of the pattern, knotica adds a self-improvement stack: the wiki's *operating program* — its schemas and operation prompts — is evolved against an objective evaluation metric by [DSPy](https://github.com/stanfordnlp/dspy) (inner loop, prompts) and [SIA](https://github.com/hexo-ai/sia) (outer loop, structure).

The system's "weights" are its schemas and prompts. No model weights are ever modified.

## How it works

- **The intelligence is your Claude client.** For ingest, curation, lint, and exploratory Q&A, knotica's server exposes deterministic, stateless tools and your client's LLM does the thinking — no knotica-owned credentials required. Only *headless* paths run a server-side LLM and need a Claude token: the `query` tool, `compile`, `datasets` bootstrap, and the eval harness. (Gap-fill source discovery needs a search-provider key rather than a model token.)
- **Everything is a file in git.** Pages, schemas, prompts, curated examples, compiled artifacts, and eval metrics all live in the vault. One git commit per mutating operation — a full audit trail, nothing hidden.
- **Per-topic agents, earned divergence.** Each topic directory can carry its own schema overlay, prompt overrides, dataset, and metrics — created only when the topic's data justifies it.
- **The flywheel.** Curated question/answer examples accumulate per topic. At the compile threshold you compile a DSPy-optimized query program onto a review branch; after you merge it, `query` silently uses it. That is the *proactive* path.
- **The wiki defends itself.** A watcher evaluates new content against a frozen baseline. When a change makes answers worse, the arena races prompt variants to heal it — the *reactive* path. When the real problem is missing knowledge, the gap-fill pipeline diagnoses it, finds candidate sources, and asks you before ingesting anything.
- **Claims can be retracted.** A wiki that only accumulates is a wiki that rots. The [Memory Guillotine](docs/guillotine.md) puts a claim on trial, audits the evidence behind it, and recommends a verdict — as evidence for your review. It never rewrites your prose.
- **Your notes stay yours.** Personal marginalia live in `notes/<topic>/`, anchored to the exact passage that provoked them, and are **never scored** — not by lint, not by eval, not by the loop. When the wiki rewrites an anchored passage, the note follows it if it can and lands in a review queue if it cannot.

> [!IMPORTANT]
> **The vault is data; this repo is code.** The wiki lives in a **separate git repo** at a path you choose (dev default `~/dev/data/knotica`), never inside this repo. All vault access goes through the `VaultStore` abstraction — vault paths are never hardcoded.

## Install

Two channels, one MCP server behind both. Full walkthrough: **[`docs/install.md`](docs/install.md)**.

**Claude Code — plugin channel:**

```
/plugin marketplace add francisco-perez-sorrosal/bit-agora
/plugin install knotica@bit-agora
```

Then `/knotica:setup` to scaffold a vault and wire the server.

**Claude Desktop / CLI channel**, from a checkout of this repo:

```bash
make start                     # sync deps, install the CLI with the evals extra, restart the loop service
knotica init --desktop --yes   # scaffold a vault, write config, register the Desktop MCP entry
```

Then **fully quit and reopen Claude Desktop** (⌘Q — config is read at launch). Desktop specifics: [`docs/CLAUDE_DESKTOP.md`](docs/CLAUDE_DESKTOP.md).

**Prerequisites:** [uv](https://docs.astral.sh/uv/) (required — launches the server and powers the CLI), git (the vault is a git repo), Python 3.12+ (uv manages the interpreter), and a Claude client. [ripgrep](https://github.com/BurntSushi/ripgrep) is a *performance* dependency, not a requirement — search falls back to a pure-Python walk and returns identical results either way.

## First run

1. Finish setup (`/knotica:setup`, or `knotica init`).
2. Open the scaffolded folder as a vault in Obsidian. A fresh vault is scaffolded **bare** — no demo content.
3. Ingest something. In your client: *"Ingest `<url>` into a topic called `<name>`."*
4. Ask a grounded question, then curate the good answers to fuel the flywheel.
5. Optional: open the dashboard — ask your client to call `open_dashboard`, or run `knotica mcp --http` and browse `http://127.0.0.1:8765/`.

Want a worked example first? [`docs/tutorial.md`](docs/tutorial.md) walks a real paper end to end — ingest, ask, curate, compile, prove the improvement.

## Documentation

| I want to… | Read |
|---|---|
| Install, wire up a client, enable headless, run the daemon | [`docs/install.md`](docs/install.md) |
| Follow a worked example end to end | [`docs/tutorial.md`](docs/tutorial.md) |
| Understand how the wiki improves itself | [`docs/self-improvement.md`](docs/self-improvement.md) |
| Close knowledge gaps from diagnosis to gated ingest | [`docs/gap-fill.md`](docs/gap-fill.md) |
| Keep personal notes that are never scored | [`docs/notes.md`](docs/notes.md) |
| Retract or demote a claim that no longer holds | [`docs/guillotine.md`](docs/guillotine.md) |
| Configure models, eval cadence, vaults | [`docs/configuration.md`](docs/configuration.md) |
| Look up a CLI command, MCP tool, or vault path | [`docs/reference.md`](docs/reference.md) |
| Use the dashboard | [`docs/dashboard.md`](docs/dashboard.md) |
| Run knotica in Claude Desktop | [`docs/CLAUDE_DESKTOP.md`](docs/CLAUDE_DESKTOP.md) |
| Export to the Open Knowledge Format | [`docs/okf.md`](docs/okf.md) |
| Understand the code | [`docs/architecture.md`](docs/architecture.md) |
| Contribute | [`CONTRIBUTING.md`](CONTRIBUTING.md) |

## Command surface

Three surfaces, one engine: **14** `/knotica:*` plugin aliases in Claude Code, **15** `knotica` CLI subcommands, and **33** MCP tools (23 conversational, 9 action dispatchers, plus `open_dashboard`). Every command, tool, action, and flag is tabulated in [`docs/reference.md`](docs/reference.md).

The ones you will actually type:

```bash
knotica init      # scaffold a vault and wire a client
knotica status    # pages per topic, compile-ready count, lint state, unpushed commits
knotica doctor    # deterministic health checks
knotica mcp       # serve the MCP server (--http adds the browser dashboard)
knotica loop      # the autonomous self-improvement watcher
```

## For AI agents and contributors

- **Start with [`docs/architecture.md`](docs/architecture.md)** — the code-verified developer guide. The design canon (invariants, rationale, design target) is [`.ai-state/DESIGN.md`](.ai-state/DESIGN.md); decisions are ADRs in [`.ai-state/decisions/`](.ai-state/decisions/). [`CLAUDE.md`](CLAUDE.md) is the always-loaded index.
- **Repo layout:** `src/knotica/` (the package) · `dashboard/` (Preact source) · `vault-template/` (what `init` scaffolds) · `.claude-plugin/` + `commands/` + `hooks/` + `skills/` + `.mcp.json` (the plugin) · `tests/` · `docs/` · `scripts/` · `.ai-state/` (committed project intelligence).
- **The vault is not in this repo** — it lives at a path you configure.
- **Verification** — `make verify` is the canonical gate, in order: topology check → ADR health → architecture coverage → mypy → the full test suite → `ruff check` → `ruff format --check`. The three leading checks are cheap, and a failure there makes everything below it untrustworthy. For the inner loop, `make test-groups` lists the scoped groups and `make test-group GROUP=<id>` runs one in seconds.
- **ADR finalize hooks** promote `.ai-state/decisions/drafts/` to numbered records on `main`. Git cannot install a repo's own hooks on clone, so `.claude/settings.json` re-asserts them at session start. To install by hand — a fresh clone, a new machine, or working outside Claude Code:

  ```sh
  sh scripts/install_git_hooks.sh          # idempotent; PRAXION_ROOT overrides discovery
  ```

  It never replaces a hook it did not install, and reports rather than fails when praxion is absent.

## Source material

Karpathy's [llm-wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) · [rohitg00's improvements](https://gist.github.com/rohitg00/2067ab416f7bbe447c1977edaaa681e2) · [autoresearch](https://github.com/karpathy/autoresearch) · [SIA](https://github.com/hexo-ai/sia) · [DSPy](https://github.com/stanfordnlp/dspy)

## License

MIT
