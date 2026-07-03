# Knotica

**An AI-maintained knowledge wiki that improves itself — without touching model weights.**

Knotica implements [Karpathy's llm-wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f): an LLM does the grunt work of knowledge management — summarizing, cross-referencing, filing, bookkeeping — over a plain-markdown wiki you read and edit in [Obsidian](https://obsidian.md). On top of the pattern, knotica adds a self-improvement stack inspired by [autoresearch](https://github.com/karpathy/autoresearch)'s methodology: the wiki's *operating program* (schemas + operation prompts) is evolved against an objective evaluation metric by two nested loops — [DSPy](https://github.com/stanfordnlp/dspy) optimizing prompts (inner), [SIA](https://github.com/hexo-ai/sia) evolving structure (outer).

## How it works

```
┌─────────────────────────────────────────────────────────────┐
│  You + Claude (Code / Desktop)          Obsidian (viewer)   │
│        │  /knotica:ingest · query · lint · curate    ▲      │
│        ▼                                             │      │
│  knotica MCP server (deterministic tools, stateless) │      │
│        ▼                                             │      │
│  The vault: plain markdown + wikilinks + git ────────┘      │
│    SCHEMA.md (constitution) · topics/ · sources/ · log      │
│        ▲                                                    │
│  Self-improvement loops (clone → evolve → branch → review)  │
│    DSPy: prompts    SIA: schemas/structure                  │
└─────────────────────────────────────────────────────────────┘
```

- **The intelligence is your Claude client** — knotica's server is deterministic and stateless; it needs no API keys of its own.
- **Everything is a file in git**: pages, schemas, prompts, curated examples, eval metrics. One commit per operation. Nothing hidden.
- **Per-topic agents, earned divergence**: each topic directory carries its own schema overlay, prompt overrides, dataset, and metrics — created only when the topic's data justifies it.
- **The flywheel**: as you use the wiki, curated query/answer examples accumulate per topic; they become the training and evaluation fuel for the improvement loops.

## Install (planned — pre-implementation)

Primary channel, in Claude Code:

```
/plugin marketplace add francisco-perez-sorrosal/knotica
/plugin install knotica@knotica
/knotica:setup        # guided: vault path, first topic, git remote
/knotica:ingest <url-or-file>
```

Fallback CLI channel (also covers Claude Desktop): `uv tool install` from this repo, then `knotica init`.

## Status

**Pre-implementation.** The converged design lives in [`docs/PRE_PLAN.md`](docs/PRE_PLAN.md) — the single authoritative source for architecture, phases, and decisions. Current phase: kickoff of Phases 0–1 (vault template + core/MCP/plugin).

## For AI agents working on this repo

- **Read [`docs/PRE_PLAN.md`](docs/PRE_PLAN.md) first** — it is canonical; this README is a summary. `CLAUDE.md` lists the non-negotiable invariants (client-as-brain, stateless server, vault/code separation, per-op commits, clone-based loops).
- Repo layout: `src/knotica/` (package: `core/`, `store/`, `search/`, `cli/`, `mcp/`, `programs/`, `agent/`, `evals/`) · `vault-template/` (scaffolded into user vaults) · `.claude-plugin/` + `commands/` + `hooks/` + `skills/` + `.mcp.json` (the Claude plugin layer) · `tests/`.
- The wiki vault is **not** in this repo — it lives at a user-configured path (dev default `~/dev/data/knotica`) and is its own git repo.
- Python 3.12+, uv-managed; `uv run pytest` for tests.

## Source material

Karpathy's [llm-wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) · [rohitg00's improvements](https://gist.github.com/rohitg00/2067ab416f7bbe447c1977edaaa681e2) · [autoresearch](https://github.com/karpathy/autoresearch) · [SIA](https://github.com/hexo-ai/sia) · [DSPy](https://github.com/stanfordnlp/dspy)

## License

TBD
