---
description: Create and initialize a new knotica knowledge base (vault) — asks for path, name, and topic.
argument-hint: "[name]"
allowed-tools:
  - AskUserQuestion
  - mcp__plugin_knotica_knotica__*
---
Create and initialize a new knowledge base (vault) — the CLI/agent equivalent of the
dashboard's "New KB" button. Gather the same fields that form collects, then scaffold.

1. Collect the parameters (use AskUserQuestion for anything the user hasn't already given):
   - **Path** — where the vault lives on disk (required), e.g. `~/dev/data/<name>`.
   - **Name** — the vault's config name (optional; default to the path's last segment, or `$1`).
   - **Topic** — an optional first topic to seed (blank to start empty).
2. Call the knotica `vault` tool with `action`=create, `name`, `path`, `topic` (when given),
   and `make_default`=true (switch to the new KB). The vault is scaffolded **bare** — the
   constitution plus your topic only, with no demo content.
3. Report the new active KB (name + path) and its readiness. Then offer next steps: open the
   folder as a vault in Obsidian, or ingest a first source into the topic.

To point at an *existing* vault instead of creating one, use `/knotica:use` (switch active) or
ask for `vault action=add` (register a path without scaffolding).
