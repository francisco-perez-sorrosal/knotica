# `commands/` — plugin slash commands

One file per `/knotica:*` alias, 15 in total. These are **prompts, not code**: the model reads the body and acts. Precision matters more than brevity.

## Anatomy

Frontmatter carries `description` (one line, shown in the picker), `argument-hint` (e.g. `<question> [topic]`), and `allowed-tools`. MCP tools are namespaced `mcp__plugin_knotica_knotica__*`; shell delegation is scoped narrowly (`Bash(knotica prompt:*)`), never a bare `Bash`.

Two body styles, both legitimate:

- **Shell delegation** — a single `` !`knotica ...` `` line that loads a vault-resolved prompt or runs a deterministic command, plus instructions for interpreting the result. Use when the CLI already does the work.
- **Tool-driving** — instructions that call MCP tools directly (`vault`, `note_capture`, `query`, `wiki_status`). Use when the command needs judgment or `AskUserQuestion`.

## Rules

- **Never hardcode a topic.** A command that bakes in a topic name works for exactly one vault. Take it as an argument or infer it from context, and fall back to `wiki_status(view="scope")`.
- **Describe what actually ships.** These files are user-facing documentation as much as instructions; a description that promises behavior the code does not implement is a defect, not a nicety.
- **Request Python dependencies by extra name.** Any command that registers a headless server must use `--from '<source>[evals]'` — hand-listing `anthropic` and `dspy` drops the `litellm<1.92` bound and breaks macOS installs, which is exactly what `pyproject.toml` warns about.
- **Never instruct raw `git` against the vault.** Use the deterministic tools (`branches action=promote`, `improve promote`, `knotica tend doctor repair`) so the operation stays flock-guarded and auditable. `git restore .` in particular is forbidden — it discards a user's uncommitted work.
- **Mutations stay user-gated.** Read, offer, then write. Never let a command silently commit to the vault.

## Related surfaces

`hooks/` registers one non-blocking `SessionStart` hook (config nudge, uvx check, background pre-warm, then schema/dirty-tree/attention nudges) — it must always exit 0 and never block a session. `skills/wiki-maintenance/` teaches *judgment* and deliberately never restates the four operation protocols, which live in the vault at `.knotica/prompts/` and are loaded via `read_protocol`.
