---
description: Interactive first-run wizard — scaffold a knotica vault, wire the MCP server, pre-warm.
allowed-tools:
  - AskUserQuestion
  - Bash(knotica init:*)
  - Bash(uvx:*)
  - Bash(gh:*)
---
Guide the user through knotica setup. Plugins have no install-time interactivity,
so gather the choices here, then drive the deterministic `knotica init` CLI.

1. Use AskUserQuestion to collect:
   - **Vault path** — filesystem location for the new Obsidian vault (default `~/dev/data/knotica`).
   - **Initial topic** — an optional first topic to seed (default: none).
   - **Remote** — `none` (local only) or `gh-private` (create a private GitHub repo via `gh`).
   - **Desktop** — whether to also patch the Claude Desktop config.

2. Run the scaffold with the collected answers, mapping each to its flag:
   `knotica init --vault <path> [--topic <name>] --remote <none|gh-private> [--desktop] --yes`
   (`--yes` because every value was already gathered above). If the user chose
   `gh-private`, `init` invokes `gh` itself to create the private remote.

3. Foreground pre-warm the environment so the first real call is fast:
   !`uvx --from "${CLAUDE_PLUGIN_ROOT}" knotica --version`

4. Report the summary `init` printed and the next step: open the vault folder in
   Obsidian, then try `/knotica:ingest <source-url>`.
