---
description: Switch or inspect the active knotica knowledge base (vault).
argument-hint: "[vault-name]"
allowed-tools:
  - mcp__plugin_knotica_knotica__*
---
Switch or inspect the active knowledge base (vault). The active vault is resolved per call,
so a switch takes effect immediately — no restart.

- If a vault name was given ($1): call the `vault` tool with `action`=use and `name`=$1.
  Report the new active KB (name + path) and its readiness. If `ready` is false, warn that
  the path is not an initialized vault yet and point to `knotica init --vault <path>`
  (or `vault action=add` to register an existing one).
- If NO name was given: call `vault` with `action`=status and summarize the live active KB,
  anything in its `misconfig` list, and headless readiness; then call `vault` with
  `action`=list and show the configured vaults so the user can pick one to switch to.

Never assume which vault is active — always read it from `vault action=status` first.
