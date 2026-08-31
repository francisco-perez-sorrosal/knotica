---
description: "[Setup] Enable, disable, or check headless (server-side LLM) mode for knotica in Claude Code."
argument-hint: "[on|off|status]"
allowed-tools:
  - mcp__plugin_knotica_knotica__*
  - Bash(claude mcp:*)
---
Manage headless mode ($1 = on|off|status; default status). Headless is what powers the MCP
tool `query`, `improve action=compile`, and the loop's eval scoring — not Arena's healing race, which is a
deterministic mutator with a keyword scorer and makes no model call. It needs the `evals`
extra (`anthropic`, `dspy`) and LLM credentials. The plugin's default `knotica` server is lean
(no `evals` extra) and stays that way for ingest / client-as-brain use, which needs neither.

- **on**: register a user-scoped `knotica` server that carries the `evals` extra — user scope
  outranks the plugin's server, so it cleanly overrides the lean default (no merge). Run:
  ```
  claude mcp add --scope user knotica -- uvx --from 'git+https://github.com/francisco-perez-sorrosal/knotica[evals]' knotica mcp
  ```
  Then explain: credentials come from the shell environment the server inherits
  (`CLAUDE_CODE_OAUTH_TOKEN` preferred, else `ANTHROPIC_API_KEY` as a metered fallback) — no
  file to edit. This takes effect on the **next server reconnect or new session** — dependencies
  are chosen at process launch, so a running lean server can't gain `dspy` in place.

- **off**: remove the override so the plugin's lean server resumes. Run:
  ```
  claude mcp remove --scope user knotica
  ```
  Same reconnect-required caveat applies.

- **status** (or no argument): call the `vault` MCP tool with `action=status` and report the
  `headless` block — `deps_installed`, `credential_mode`, `ready` — plus its `detail` message.
  Then point to `/knotica:headless on` or `off` to change it.
