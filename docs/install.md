# Install

Knotica ships through two channels. Pick the one that matches the client you use — or install both, they share one vault and one `config.toml`.

- [Prerequisites](#prerequisites)
- [Channel 1: the Claude Code plugin](#channel-1-the-claude-code-plugin)
- [Channel 2: the CLI and Claude Desktop](#channel-2-the-cli-and-claude-desktop)
- [Which command should I run](#which-command-should-i-run)
- [Lean vs headless: the evals extra](#lean-vs-headless-the-evals-extra)
- [Headless credentials](#headless-credentials)
- [Enabling headless in Claude Code](#enabling-headless-in-claude-code)
- [The loop daemon](#the-loop-daemon)
- [Multiple knowledge bases](#multiple-knowledge-bases)
- [Makefile targets](#makefile-targets)

## Prerequisites

| Requirement | Needed for | Notes |
|---|---|---|
| Python 3.12+ | Everything | `requires-python = ">=3.12"` |
| `uv` (with `uvx`) | Everything | Every launch path shells out to `uv` or `uvx`; setup fails with a named error if neither is on `PATH` |
| `git` | Everything | The vault is a git repo; every mutating operation writes one commit |
| Claude Code or Claude Desktop | Everything | One MCP client. Claude Code gets the plugin channel; Desktop gets the CLI channel |
| `ripgrep` (`rg`) | Performance only | Without it, search falls back to a pure-Python walk that reads every markdown file in scope on each call — identical results, one to two orders of magnitude slower. Fine at hundreds of pages; install it beyond that |
| Obsidian | Optional | The vault is plain markdown in a directory. Obsidian is the recommended reader, not a dependency |
| `gh` | Optional | Only for `--remote gh-private` |

## Channel 1: the Claude Code plugin

Knotica is distributed through the external `bit-agora` marketplace. This repo is a plugin, not a marketplace of its own.

```
/plugin marketplace add francisco-perez-sorrosal/bit-agora
/plugin install knotica@bit-agora
```

Then run the first-run wizard, `/knotica:setup`. It asks four questions — vault path (default `~/dev/data/knotica`), an optional initial topic, remote (`none` or `gh-private`), and whether to patch the Claude Desktop config — then runs `knotica init` with your answers and pre-warms the environment so the first real call is fast.

`knotica init` runs these stages in order: resolve the MCP launch source; copy the packaged vault template and make the first git commit; create a private GitHub remote if you asked for one (best-effort — a failure warns, never aborts); write `~/.config/knotica/config.toml`; register the server with `claude mcp add knotica` (skipped with a note if the `claude` CLI is absent); patch the Claude Desktop config if `--desktop` was passed; pre-warm the launch command.

Two behaviours are worth knowing up front. **Every scaffolded vault is bare** — the template's demo content is stripped, so you start with a constitution and, optionally, one empty topic. And **scaffolding never clobbers**: an existing knotica vault's contents are never overwritten, while a non-empty directory that is *not* a knotica vault is refused outright. Re-running `init` on a live vault is still not a no-op — it seeds the `--topic` overlay if that topic is missing, and it commits anything currently uncommitted under the message `Initialize knotica vault`.

> [!NOTE]
> The plugin ships a **lean** MCP server — `.mcp.json` launches `uvx --from ${CLAUDE_PLUGIN_ROOT} knotica mcp` with no `evals` extra. That is enough for ingest, lint, curate, notes, and search. See [Enabling headless in Claude Code](#enabling-headless-in-claude-code) for the rest.

The plugin also registers one `SessionStart` hook. It never blocks the session: it nudges you if knotica is unconfigured, if the vault schema is behind the plugin, if the vault has uncommitted changes, or if a topic needs attention.

## Channel 2: the CLI and Claude Desktop

From a clone of this repo:

```bash
git clone https://github.com/francisco-perez-sorrosal/knotica
cd knotica
make start
```

`make start` runs `make install` (sync the venv, then install the global CLI, both with the `evals` extra) and restarts the loop daemon if one is already registered. It never *registers* the daemon — see [The loop daemon](#the-loop-daemon).

To install only the global CLI:

```bash
uv tool install --from '.[evals]' knotica
```

> [!WARNING]
> Use `--from '.[evals]'`, never `--from .`. Dropping the extra installs a CLI with no LLM SDK, and every headless path then fails at first use with a "the eval dependency group is not installed" error.

Then scaffold the vault and wire Claude Desktop:

```bash
knotica init --vault ~/dev/data/knotica --desktop
```

Finish by **fully quitting** Claude Desktop (Cmd-Q, not just closing the window) and reopening it. MCP config is read at launch.

The Desktop config lives at `~/Library/Application Support/Claude/claude_desktop_config.json` — macOS only; override with `$KNOTICA_DESKTOP_CONFIG`. The patch is deliberately conservative: it merges only the `knotica` key, carries a prior `env` block over verbatim (that is where Desktop MCP credentials live), backs up to `<path>.bak` only when something will actually change, no-ops when the new entry is identical, and refuses to write at all if the existing file is invalid JSON.

Desktop-specific setup, verification, and log locations: [CLAUDE_DESKTOP](CLAUDE_DESKTOP.md).

## Which command should I run

Setup and maintenance are different commands. The distinction matters more than it looks.

| Command | Scaffolds a vault | Writes `config.toml` | Touches Desktop config |
|---|---|---|---|
| `knotica init` | Yes | Yes — sets `default_vault` | No |
| `knotica init --desktop` | Yes | Yes — sets `default_vault` | Yes |
| `knotica desktop install` | No | No | Yes |
| `knotica desktop status` | No | No | Read-only |
| `make desktop` | No | No | Yes (wraps `desktop install`) |

> [!IMPORTANT]
> `knotica init` always registers its vault under the name `main` **and always as `default_vault`**. So reaching for `knotica init --desktop` to repair a stale Desktop entry silently switches which knowledge base is active. Use `knotica desktop install` — it manages the Desktop entry alone and cannot change the active vault.

`knotica desktop status` is read-only and safe to run any time. It prints the registered command, its args, and the `env` key **names** — never their values.

## Lean vs headless: the evals extra

The base package depends on `mcp` and `pydantic` and nothing else. It carries **no LLM SDK**, on purpose: `uvx --from … knotica mcp` stays small and credential-free, which is all the client-as-brain paths need. The `evals` extra adds `anthropic>=0.116`, `dspy>=3.2`, and `litellm<1.92`.

> [!IMPORTANT]
> Always request the extra **by name**. Hand-listing the three packages drops the `litellm<1.92` bound. From 1.92.0 litellm publishes manylinux and Windows wheels only — there is no macOS wheel at any version — so a macOS install falls back to building the sdist and needs a Rust toolchain. `1.91.4` is the last pure-Python wheel.

| Surface | Command |
|---|---|
| Repo venv | `uv sync --extra evals` |
| Global CLI | `uv tool install --from '.[evals]' knotica` |
| Claude Desktop (published source) | `uvx --from '<source>[evals]' knotica mcp` |
| Claude Desktop (local checkout) | `uv run --directory <repo> --extra evals knotica mcp` |
| Published package | `pip install 'knotica[evals]'` |
| Claude Code | `/knotica:headless on` (hand-lists `anthropic` + `dspy`, not the extra) |

You do not write the two Desktop rows by hand — `knotica init --desktop` and `knotica desktop install` pick the right shape. A source checkout (a directory containing `pyproject.toml`) takes the `uv run` branch; anything else takes `uvx --refresh --from '<source>[evals]'`.

<details>
<summary>If Desktop fails with <code>Group `evals` is not defined</code></summary>

Configs written before the extra migration carry `--group evals`, which no longer exists. <!-- allow-stale-invocation --> Re-run `knotica desktop install` to rewrite the entry, then fully quit and reopen Desktop.
</details>

## Headless credentials

"Headless" means a server-side LLM call. Most of knotica does not need one.

| Path | Needs a server-side LLM |
|---|---|
| Ingest (`read_protocol` → `store_source` / `write_page`) | No |
| Lint, curate, notes, status, doctor, vault reads | No |
| Exploratory Q&A via `search` / `read_page` | No |
| Arena races | No — deterministic mutator, keyword scorer, no model call |
| MCP `query` tool | Yes |
| `compile action=run` | Yes |
| `knotica improve eval` | Yes |
| The loop's evals | Yes |
| Gap-fill candidate gating | Yes |
| `datasets` bootstrap | Yes |

Two environment variables, checked in this order:

1. **`CLAUDE_CODE_OAUTH_TOKEN`** — preferred. A Claude subscription bearer token, no metered spend. Get one by running `claude setup-token` from a machine with Claude Code installed.
2. **`ANTHROPIC_API_KEY`** — used only when the OAuth token is absent, and knotica emits a loud metered-spend warning when it falls back. With neither set, it raises a typed `NOT_CONFIGURED` error naming both variables, before any SDK import or network call.

> [!WARNING]
> Both are read from the **process environment only**. Never put a credential in `~/.config/knotica/config.toml` or in the vault.

Claude Desktop does not inherit your shell, so a Desktop credential goes in the `mcpServers.knotica.env` block; `knotica desktop install` carries that block over untouched on every re-run. The loop daemon has the same problem for a different reason — launchd and systemd start it with a near-empty environment — so at startup it loads `~/.config/knotica/.env` for keys not already set. That is the only `.env` the daemon trusts; a vault-adjacent one is deliberately ignored.

## Enabling headless in Claude Code

Run `/knotica:headless on`. It registers a **user-scoped** server with the headless dependencies:

```bash
claude mcp add --scope user knotica -- uvx --from 'git+https://github.com/francisco-perez-sorrosal/knotica[evals]' knotica mcp
```

Like every other install path, it requests the extra **by name**, so the `litellm<1.92` bound comes along. Note the quotes — unquoted brackets are a zsh glob.

User scope outranks the plugin's server, so it replaces the lean default outright — there is no merge. `/knotica:headless off` runs `claude mcp remove --scope user knotica` and the lean default resumes.

> [!NOTE]
> Both take effect on the **next server reconnect or new session**. Dependencies are chosen at process launch, so a running lean server cannot gain `dspy` in place. Credentials are likewise read at launch, from the environment the server inherits.

`/knotica:headless status` (or no argument) reports `deps_installed`, `credential_mode`, `ready`, and a detail line covering four states:

| Deps | Credential | Reported as |
|---|---|---|
| `anthropic` + `dspy` | Set | headless ready |
| Missing | None | lean mode — ingest / client-as-brain only |
| Missing | Set | credential set but `anthropic`/`dspy` not installed |
| Installed | None | `anthropic`/`dspy` installed but no credential |

Lean mode is not a misconfiguration. A credential with no deps is.

## The loop daemon

The autonomous self-improvement watcher runs as an OS service. **Registering it is deliberately a separate act from installing knotica**, because the daemon runs billed evals on a schedule. `make start` restarts a registered daemon so it picks up new code; it never registers one.

Register it with `make daemon-install`, then confirm with `make daemon-status`. Both wrap `knotica service`; the full set is in [Makefile targets](#makefile-targets).

| Platform | Unit path | Status |
|---|---|---|
| macOS (launchd) | `~/Library/LaunchAgents/com.knotica.loop.plist` | Live-verified |
| Linux (systemd user) | `~/.config/systemd/user/knotica-loop.service` | Code-complete but untested against live systemd |

Any other platform is refused with a named error. `make daemon-restart` is launchd-only; on Linux run `systemctl --user restart knotica-loop`.

Install rolls back the partially-written unit if registration fails, so a failed install never leaves a zombie service; uninstall is a clean no-op when nothing is registered. The watched topic set is never baked into the unit — the daemon re-reads config and re-lists topics every supervision cycle (30 seconds by default).

What the loop actually does: [self-improvement](self-improvement.md).

## Multiple knowledge bases

`config.toml` holds any number of vaults; exactly one is active.

```toml
schema_version = 1
default_vault = "main"

[vaults.main]
path = "~/dev/data/knotica"

[vaults.research]
path = "~/dev/data/research-kb"
```

Switch with `/knotica:use <name>` in Claude Code, or drive the `vault` MCP tool directly — `action=list`, `status`, `use`, `add`, or `create`. `add` registers an existing directory without scaffolding it; `create` scaffolds a new one; `use` flips `default_vault` and refuses a name that is not already configured. Writes are additive and atomic, so every sibling vault and unrelated table survives.

Every configuration key, with its type and default: [configuration](configuration.md).

## Makefile targets

Three targets cover a first run — `make start` (code), `make init` (a knowledge base), `make dashboard` (the UI). The rest are for when something is already running.

| Target | What it does |
|---|---|
| `make help` | List the targets (the default goal) |
| `make start` | `install` + `daemon-restart` — the one command after a clone or a pull |
| `make init` | `knotica init --desktop --yes` — scaffold your first knowledge base and register it with Claude Desktop |
| `make dashboard` | Serve the dashboard and the HTTP MCP transport in the foreground; `PORT=8765` by default, Ctrl-C to stop |
| `make dashboard-stop` | Kill whatever holds the dashboard port |
| `make dashboard-restart` | `dashboard-stop` + `dashboard` — how a code change reaches a running dashboard |
| `make ps` | Which components are running, and the reminder that a running process keeps the code it started with |
| `make creds` | Which credential headless work would select, printing no secret |
| `make install` | `uv sync --extra evals`, then `uv tool install --from '.[evals]' knotica --force` |
| `make verify` | The canonical gate, in order: topology check, ADR health, architecture coverage, mypy, pytest, `ruff check`, `ruff format --check` |
| `make test-groups` | List the test groups |
| `make test-group GROUP=<id>` | Run one group; accepts `ARGS="-x -q"` |
| `make doctor` | `knotica tend doctor --quick` against the active knowledge base |
| `make desktop` | `knotica desktop install` — no vault or config changes |
| `make daemon-install` | Register the loop service |
| `make daemon-restart` | Restart it so it runs freshly installed code (launchd only) |
| `make daemon-status` | Install state and per-topic runner liveness |
| `make daemon-uninstall` | Deregister and remove the unit |
| `make daemon-logs` | Tail `~/Library/Logs/knotica/loop.err.log`, then `loop.out.log` |
| `make clean-tool` | Remove the globally installed `knotica` CLI |

Next: walk a vault end to end in the [tutorial](tutorial.md), or read the [reference](reference.md) for the full CLI and MCP tool surface.
