---
type: guide
title: Start Here
description: "Welcome — this vault is an AI-maintained knowledge wiki."
---
# Start Here

Welcome — this vault is an **AI-maintained knowledge wiki**. You read and explore it in
Obsidian; an AI assistant (via the knotica MCP tools) does the maintenance: ingesting sources,
answering questions with citations, and improving its own query prompts over time.

## How the vault is organized

- **Topics live at the root** — each topic is a folder of pages (e.g. an `agentic-systems`
  topic built around a paper on Agent Workflow Memory). A fresh vault starts **empty**;
  you create topics as you ingest sources and ask questions.
- **[[SCHEMA]]** is the constitution: page conventions, linking rules, and the frozen record
  formats. Each topic may add its own `SCHEMA.md` overlay that extends the root.
- **[[index]]** is the global catalog of topics and pages.
- **[[log]]** is the append-only operation log — one entry per change.
- **`sources/`** holds immutable raw sources (papers, articles), one folder per topic.
- **`<topic>/.knotica/`** holds datasets (`qa.jsonl`, `golden.jsonl`), prompts overrides,
  loop/compile state, and compiled artifacts after a successful merge.

## How changes happen

Every operation that changes the vault makes **exactly one git commit** and appends one
[[log]] entry — the vault's full history is in `git log`, and any change can be audited or
rolled back. Compiles and evals always run on a **clone** and return a branch for you to merge.

## First steps

### In Obsidian

1. Open this folder as a vault (if you haven't already).
2. As you create topics and ingest sources, browse `<topic>/` and `sources/<topic>/` to
   see the pages and their raw sources.

### In Claude Desktop (Chat)

1. Confirm the **knotica** MCP server is connected (Settings → MCP / Developer).
2. Create a topic and ingest a source into it — e.g. ask Claude: *Ingest this URL into a
   new topic called `<topic>`.*
3. Ask a grounded question via the `query` tool (needs LLM credentials in Desktop MCP
   `env` — see repo `docs/CLAUDE_DESKTOP.md`).
4. When an answer is good, save it with `curate_example` (verdict `good`) — that fuels compile.
5. Full install + compile prove walkthrough (worked example: ingesting an Agent Workflow
   Memory paper into an `agentic-systems` topic): see the repo’s `docs/CLAUDE_DESKTOP.md`.

### In Claude Code

1. `/knotica:ingest <url>` — fetch a source, place it by topic, write pages.
2. `/knotica:query <question>` — grounded answer with citations.
3. `/knotica:status` / `/knotica:doctor` — progress and health.

## Self-improvement (short)

1. **Curate** ~30 query-style good examples for a topic.
2. **Compile** (`compile_run` or `knotica compile`) → merge the `compile/<topic>/…` branch.
3. **Ask the same question again** — `query` uses the compiled engine silently.
4. If a loop candidate fails the gate, **Arena** races prompt variants (reactive heal).
