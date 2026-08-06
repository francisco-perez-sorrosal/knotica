---
id: dec-076
title: A vault dispatcher that switches the active KB at runtime, and what that does to the stateless invariant
status: accepted
category: architectural
date: 2026-07-28
summary: A ninth operator dispatcher, `vault`, owns the config-level knowledge-base surface — list / status / use / add / create. It never touches vault contents and never makes a git commit; `action=use` flips `default_vault` in ~/.config/knotica/config.toml and takes effect on the next call with no restart, because config is resolved per call. This narrows the stateless-server invariant from "the active vault is fixed for the process" to "the active vault is resolved per call and a tool may change what the next call resolves to", and `action=status` exists so the model answers "which KB am I on?" from the live resolution rather than from an assumption.
tags: [mcp, dispatcher, config, multi-vault, stateless-server, tool-surface, honesty]
made_by: agent
agent_type: orchestrator
branch: main
pipeline_tier: standard
affected_files:
  - src/knotica/mcp_server/tools_dispatch_vault.py
  - src/knotica/mcp_server/server.py
  - src/knotica/core/config_write.py
  - src/knotica/core/vault_scaffold.py
re_affirms: dec-004
dissent: A tool that changes which vault every subsequent call resolves against is a session-scoped mode switch in all but name, and the server's whole design premise is that it has no session. The honest alternative is that switching belongs to the CLI and the config file, where a human does it deliberately between sessions, rather than to a surface a model can invoke mid-conversation on its own initiative.
---

## Context

`config.toml` has supported several named vaults under `[vaults.<name>]` with a `default_vault`
pointer since `dec-004`, and `core.config.resolve(vault=…)` has always taken an optional per-call
override. What did not exist was any way to *see* or *change* that state from a client: a user with
three knowledge bases configured had to hand-edit a TOML file to move between them, and a model had no
way to answer "which KB am I on?" except by assuming.

The assumption is the dangerous half. Every mutating tool takes an optional `vault` argument that
defaults to empty, meaning "the active one". A model that guesses wrong writes a page into the wrong
knowledge base, and the write succeeds — it is a perfectly valid operation against the wrong target.
Nothing in the surface made the active vault *observable*, so nothing made that class of error visible
before it landed.

This decision was made and shipped in `021f8f0` (2026-07-28). It is recorded here retroactively: the
record was missing entirely (`td-043`), and the reasoning below is reconstructed from that commit and
from the code, not re-derived.

## Decision

1. **A ninth operator dispatcher, `vault`**, with `_ACTIONS = (list, status, use, add, create)`. It
   joins the two-tier topology of `dec-045` rather than adding flat tools: five related operator
   actions over one domain is exactly the shape that section decided a dispatcher owns.
2. **Config-level only.** Every action reads or writes `~/.config/knotica/config.toml` through
   `core.config_write`. None of them touches vault contents, takes the vault flock, or makes a git
   commit — so this dispatcher sits entirely outside the single-mutation-path invariant (`dec-008`)
   rather than being an exception to it.
3. **`action=use` flips `default_vault` and takes effect immediately**, with no restart, because config
   is resolved per call (`dec-004`). It **rejects an unknown name** before writing, so the operation can
   never leave a config that resolves to `NOT_CONFIGURED` — a write that bricks the next call is worse
   than a refused switch.
4. **`action=status` reports the *live* resolution**, not a cached or assumed one: the active vault's
   name and path, every configured vault, headless-LLM readiness (deps present, credential mode), and a
   misconfiguration list. The credential *value* is never returned, only which mode resolved — the
   `dec-014` never-log rule applied to a reporting surface.
5. **`action=create` scaffolds a bare vault** — constitution and an optional first topic, no demo
   content — and registers it. `action=add` registers an existing path. Both accept `make_default`.
6. **The tool description carries the honesty instruction**, not just the schema: *"never assume the
   active vault, it is resolved per call and can be switched at any time."* Under `dec-042`'s
   four-layer routing model this is the tool-description layer doing its job — the guard rides on the
   tool a model is about to call, where it is read, rather than in instructions it may not have.

## Considered Options

### A. A `vault` dispatcher (chosen)

- Matches `dec-045`'s topology: an operator domain with several actions gets one entry point.
- Puts the switch and the observation behind the same tool, so a model that can change the active vault
  can always also report it.

### B. Flat tools (`vault_list`, `vault_use`, …)

- Five more flat tools against a conversational core `dec-041` and `dec-057` are already trying to keep
  small. Rejected on the same grounds as the original consolidation.

### C. Fold into `vault_health`

- Superficially attractive — both are "vault-level" — but `vault_health` operates *on the contents of a
  vault* (doctor, repair, lint, okf, metadata tree) and this operates on *which vault is selected*. The
  two answer different questions and would share only the word "vault". Rejected.

### D. CLI-only switching (the dissent's position)

- `knotica init` and `knotica desktop` already write config, so the machinery exists and switching could
  have stayed there. It keeps the MCP surface honestly stateless and makes switching a deliberate human
  act between sessions.
- Rejected because it does not solve the observability half. A Desktop user has no terminal in the
  conversation, and the failure this addresses is a model writing into the wrong KB — which needs the
  *model* to be able to check, not the human to have checked earlier.

## Consequences

**Positive.** "Which KB am I on?" became answerable from ground truth. Multi-KB users switch in
conversation. `create` gives a first-run path that does not require the wizard.

**Negative, and load-bearing.** The stateless-server invariant now needs a more careful statement. It
was read as "no session state, so every call resolves the same way given the same arguments". That is
still true *per call* — there is no session, and nothing is cached — but a tool can now change what the
next call resolves to. The state was always in `config.toml`; what changed is that the surface can write
it. A reader who takes "stateless" to mean "the active vault cannot change under me" is now wrong, and
`§ 7` of `DESIGN.md` states the narrower version.

**Also negative:** this is the one dispatcher with no payload-helper module — it builds its payloads
inline — and it carries an `mcp_server → evals` import edge for the credential-name constants used by
`action=status`. Both are small and both are documented, but they are asymmetries against every other
dispatcher.

## Disconfirmation

- **Falsifier.** A wrong-vault write that happens *because* of this tool rather than despite it — a
  model calling `action=use`, not switching back, and a later write landing in the wrong KB. That would
  mean the switch created more misdirected writes than the observability prevented, and switching
  belongs back in the CLI (Option D).
- **Steelmanned runner-up.** Option D is genuinely strong: a stateless server whose active target cannot
  be moved by its own tool surface is easier to reason about, and a human editing config between
  sessions is a slower, more deliberate act than a model calling a tool mid-conversation. The counter is
  narrow but decisive — it leaves the model unable to *check*, and checking is what prevents the error.
- **Reversal trigger.** Split the surface if the failure appears: keep `list` and `status` on the MCP
  dispatcher, move `use`, `add`, and `create` to the CLI. The read half carries the observability
  benefit and none of the switching risk, so the split is available at any time without losing what this
  decision was for.
