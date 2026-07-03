# Start Here

Welcome — this vault is an **AI-maintained knowledge wiki**. You read and explore it in
Obsidian; an AI assistant (via the knotica tools) does the maintenance: ingesting sources,
answering questions with citations, and keeping the structure clean.

## How the vault is organized

- **Topics live at the root** — each topic is a folder of pages (e.g. `agentic-systems/`).
  The template ships with a small, clearly-marked demo sample in the `agentic-systems` topic
  so you can see a completed ingest and a populated graph; delete it whenever you like.
- **[[SCHEMA]]** is the constitution: page conventions, linking rules, and the frozen record
  formats. Each topic may add its own `SCHEMA.md` overlay that extends the root.
- **[[index]]** is the global catalog of topics and pages.
- **[[log]]** is the append-only operation log — one entry per change.
- **`sources/`** holds immutable raw sources (papers, articles), one folder per topic.

## How changes happen

Every operation that changes the vault makes **exactly one git commit** and appends one
[[log]] entry — the vault's full history is in `git log`, and any change can be audited or
rolled back.

## First steps

1. Open this folder as a vault in Obsidian (if you haven't already).
2. In your AI client, ingest a paper or article — e.g. `/knotica:ingest <url>` in Claude Code.
3. Ask questions with `/knotica:query` — answers cite the vault pages they used.
4. When an answer is good (or you correct it), save it as a curated example when prompted —
   these examples make the wiki's operations better over time.
5. Run `/knotica:lint` occasionally to keep the vault consistent, and `/knotica:status` to see
   how the wiki is growing.
