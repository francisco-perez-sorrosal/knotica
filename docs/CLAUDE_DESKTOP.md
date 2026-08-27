# Knotica on Claude Desktop

Claude Desktop is one of knotica's two install channels — see [install](install.md) for
prerequisites, the `evals` extra, obtaining a credential, and enabling headless mode in Claude
Code. This page covers what's specific to Desktop: registering the server, the credentials shape
in its config file, restart and log semantics, and Desktop's own rough edges.

This guide targets **Claude Desktop Chat**, and the same MCP config shape on claude.ai where MCP
Apps are enabled. Using Claude Code instead? Its MCP surface is identical, but install and
headless setup go through the plugin channel — see [install](install.md).

- [What you get in Desktop](#what-you-get-in-desktop)
- [Register with Claude Desktop](#register-with-claude-desktop)
- [Headless credentials in Desktop's config](#headless-credentials-in-desktops-config)
- [Large ingests: use Claude Code](#large-ingests-use-claude-code)
- [Everyday prompts in Desktop Chat](#everyday-prompts-in-desktop-chat)
- [Troubleshooting](#troubleshooting)
- [Related docs](#related-docs)

## What you get in Desktop

| Surface | How it appears |
|---|---|
| MCP tools | The same full set any client gets — `query`, `curate_example`, `write_page`, `open_dashboard`, the action dispatchers, … See [reference](reference.md) for the complete list |
| MCP prompts | `ingest`, `query`, `lint`, `curate` exist server-side, but Desktop has no prompt-picker UI — call `read_protocol` instead (see [Everyday prompts](#everyday-prompts-in-desktop-chat)) |
| Dashboard (MCP App) | Ask Claude to call `open_dashboard` — renders inline when the host supports MCP Apps; see [dashboard](dashboard.md) |
| Vault | The same git repo any channel uses (default `~/dev/data/knotica`) — open it in Obsidian to read pages directly |

## Register with Claude Desktop

### Option A — `knotica init --desktop` (recommended)

```bash
uv tool install --from '.[evals]' knotica
knotica init --vault ~/dev/data/knotica --desktop
```

`--desktop` adds one stage to the wizard [install](install.md) describes: it patches Claude
Desktop's config with the right launch command — already carrying the `evals` extra, whether that
resolves to `uvx` or, from a local checkout, `uv run`. The wizard then pre-warms that launch tool
(it always does, with or without `--desktop`), which can take 20-30 seconds on the first call. The
patch is conservative about what it touches; see [install](install.md) for the mechanics.

Then fully quit and reopen Desktop — see [Restart and verify](#restart-and-verify).

### Option B — manual config

Edit the file yourself (macOS only; override the location with `$KNOTICA_DESKTOP_CONFIG`):

`~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "knotica": {
      "command": "/ABS/PATH/TO/uvx",
      "args": [
        "--from",
        "/ABS/PATH/TO/knotica/repo[evals]",
        "knotica",
        "mcp"
      ]
    }
  }
}
```

> [!WARNING]
> Desktop launches servers with a minimal `PATH`. A bare `"uvx"` resolves to nothing — always use
> the absolute path from `command -v uvx`. Keep the `[evals]` suffix on `--from`; dropping it
> installs a server with no LLM SDK, and every headless call then fails at first use.

Fully restart Desktop after saving. Prefer not to hand-edit? `knotica desktop install` writes the
entry for you without scaffolding a vault or touching `config.toml` — it picks the shape from the
source: a checkout gets `uv run --directory <repo> --extra evals knotica mcp`, anything else gets
`uvx --refresh --from '<source>[evals]' knotica mcp`. See install.md's command-comparison table.

## Headless credentials in Desktop's config

The MCP tool **`query`**, plus `compile action=run`, `knotica improve eval`, and the loop's evals, run
**headless** (server-side LLM); the arena itself makes no model call — see [install](install.md)
for which paths need one and how to obtain `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`.
Ingest, curate, lint, and exploratory Q&A need none of this.

Desktop, unlike Claude Code's stdio servers, does not inherit your shell environment — a
credential has to live in the server entry itself:

```json
"env": {
  "CLAUDE_CODE_OAUTH_TOKEN": "YOUR_TOKEN_HERE"
}
```

Merge that into the `mcpServers.knotica` object from Option A or B above (`ANTHROPIC_API_KEY`
works the same way, as the metered fallback). Never put a credential in `config.toml` or the
vault. `knotica init --desktop` and `knotica desktop install` both carry a prior `env` block over
untouched on every re-run, so you only write this once.

### Restart and verify

1. **Fully quit** Claude Desktop (Cmd-Q, not just the window) and reopen it — MCP config is read
   only at launch.
2. In Settings → Developer (or MCP), confirm a server named **`knotica`** shows as connected.
3. If it doesn't, check `~/Library/Logs/Claude/mcp*.log` for the launch error.
4. From a terminal, `knotica desktop status` prints exactly what Desktop has registered right now
   — command, args, and `env` key **names** only, never values. Useful when the running server
   doesn't match what you just edited.

## Large ingests: use Claude Code

Desktop's MCP transport can drop a large mutation mid-flight — ingesting a long PDF through many
`write_page` calls may stall partway. Nothing in knotica caps the payload size; the ceiling is
Desktop's transport, not the server. If an ingest stalls, finish or retry it from **Claude Code**
instead — same MCP surface, larger payloads.

## Everyday prompts in Desktop Chat

Claude Desktop does not surface MCP prompts (`ingest`, `query`, `lint`, `curate`) as slash
commands. Ask for the tool by name instead, or for a multi-step operation ask Claude to call
`read_protocol` first so it follows the vault's own guide rather than improvising:

| Goal | What to tell Claude |
|---|---|
| Follow the ingest protocol | "Load the knotica ingest protocol, then ingest `<url>` into `<topic>`." |
| One-shot answer | "Call knotica `query` with topic `<topic>` and question `<question>`." |
| Health check | "Run knotica `vault_health action=doctor` (quick) and summarize failures." |
| Open the dashboard | "Call `open_dashboard` for topic `<topic>`." |

Every tool, argument, and dispatcher action: [reference](reference.md).

## Troubleshooting

| Symptom | Fix |
|---|---|
| Server never connects | Confirm the absolute `uvx`/`uv` path in Desktop's config; fully quit and reopen Desktop; check `~/Library/Logs/Claude/mcp*.log` |
| `NOT_CONFIGURED` for `query` / compile / eval | Add a credential to `mcpServers.knotica.env`; fully restart Desktop — see [install](install.md) |
| `NOT_CONFIGURED` for eval dependencies | Run `knotica desktop install` from the repo, or append `[evals]` to the config's `--from` source by hand; fully restart Desktop |
| Dashboard opens as plain text | The host doesn't support MCP Apps — open the `knotica mcp --http` URL it returns instead |
| Ingest stalls partway | Desktop's transport dropped the mutation — finish or retry from Claude Code |

## Related docs

- [README](../README.md) — install channels and command surface
- [install](install.md) — prerequisites, the `evals` extra, credentials, headless in Claude Code
- [tutorial](tutorial.md) — the Agent Workflow Memory walkthrough, end to end
- [self-improvement](self-improvement.md) — gate policy, the arena, cadence, billed triggers
- [gap-fill](gap-fill.md) — the suggestion queue
- [configuration](configuration.md) — `config.toml`, `[models]`, `[loop]`
- [dashboard](dashboard.md) — the six process lanes and the HTTP mount
- [reference](reference.md) — every command, tool, and dispatcher action
