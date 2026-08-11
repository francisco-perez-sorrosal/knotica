---
id: dec-draft-af563364
title: CLI subcommands nest one level under their lane, flattened to two levels unless ambiguous
status: proposed
category: architectural
date: 2026-08-10
summary: M1's CLI rename nests the nine process subcommands under six lane commands via the existing configure(subparsers) protocol — renaming no module and no test file — and deletes each command's own group level wherever its verb is unique within the lane, so the worst depth is two rather than four and the daily cost is one extra word on five commands.
tags:
  - cli
  - swimlanes
  - rename
  - ergonomics
  - interface-design
made_by: agent
agent_type: interface-designer
branch: worktree-process-swimlanes
pipeline_tier: full
dissent: Six new lane modules and a permanent-in-practice deprecation shim table are real added surface for a CLI whose only user already knows every command by heart; the collision-only minimal rename would have cost one module rename and left the daily muscle memory untouched.
affected_files:
  - src/knotica/cli/__init__.py
  - src/knotica/cli/datasets.py
  - src/knotica/cli/compile.py
  - src/knotica/cli/gapfill.py
  - docs/architecture.md
  - docs/reference.md
  - .ai-state/DESIGN.md
  - .ai-state/TEST_TOPOLOGY.md
---

## Context

M1 of the process-swimlanes redesign renames the CLI subcommand set so every entry point speaks in
lanes. The interface design's § 6.1 had proposed delivering the CLI lane projection through `--help`
**grouping** only, at explicitly "zero rename cost, zero blast radius"; the user overturned that and
decided M1 renames the subcommands. The old→new mapping then existed in **no input document**, which
blocked the implementation-planner's CLI step (Q5/B5).

The naming decision is bounded by three things. `src/knotica/cli/__init__.py::COMMAND_NAMES` is the
single declaration of the subcommand set, and `import_module(f"knotica.cli.{name}")` ties each name to a
module — so a *module* rename ripples into the topology check (whose `cli-surface` selectors enumerate
nine `tests/test_cli_<name>.py` files individually), architecture coverage (seven cited
`src/knotica/cli/*.py` paths), and the new surface gate, all of which run *before* the test suite.
Second, ergonomics are the point of the whole project: `knotica improve datasets bootstrap-train` is
four levels for something that is two today, on commands the user runs daily. Third, the lane is now
**Fill**, not Repair, precisely because `repair` was already taken as a nested verb in two places.

Two premises turned out to be softer than assumed, and they decide the shape:

- **Nesting renames nothing.** `configure(subparsers)` takes an `_SubParsersAction` positionally and is
  agnostic to depth. Passing a lane's sub-subparsers registers the identical module one level deeper
  with **zero edits to that module**, so no file is renamed, no test file is renamed, and the topology
  selectors and architecture path citations are untouched.
- **`argparse` `aliases=` cannot carry this deprecation.** Aliases are siblings within one subparsers
  action; `knotica eval` → `knotica improve eval` crosses levels. The old names need hidden top-level
  shim parsers that warn and re-dispatch.

Separately verified: nothing installed on the user's machine hard-codes a renamed command —
`service/manager.py:92` writes `(sys.executable, "-m", "knotica.service")` into the launchd plist and
systemd unit, not `knotica loop`.

## Decision

**Nest the nine process subcommands under six lane commands, at two levels, flattening each command's
own group level wherever its verb is unique within the lane.**

1. **Top-level becomes 12**, chunked in `--help` as six lanes (`home`, `learn`, `answer`, `improve`,
   `fill`, `tend`) and six unlaned (`init`, `desktop`, `mcp`, `service`, `status`, `prompt`).
2. **Flatten-unless-ambiguous.** `improve eval|loop|compile|promote|bootstrap-train|freeze`,
   `fill discover`, `tend doctor|migrate|guillotine` are two levels — the `datasets`, `compile` and
   `gapfill` group levels are deleted because the lane already implies them. The group level survives in
   exactly two low-frequency places: `tend doctor repair` and `tend okf check|export|repair`, because
   flattening either produces `tend repair` — the same collision that forced Repair → Fill — and
   `tend check` / `tend export` are too generic to be self-documenting.
3. **`status` and `prompt` stay unlaned**, as the CLI counterparts of the flat tier-1 MCP primitives:
   `status` is a whole-vault read, `prompt` renders any of the four operation protocols. Forcing either
   into a lane is the same category error as `learn(action="search")`.
4. **`home` is the attention inbox**, replacing `status --nudge` — which is retained **permanently**,
   not as a deprecation, because `hooks/session_start.sh` is a shipped artifact and breaking it for a
   rename would be gratuitous. `home` exits `0` unconditionally; emptiness is empty stdout.
5. **`learn` and `answer` carry no verbs** and print delegation guidance to stdout, exit `0` — present
   and delegating is more honest than absent, and it makes the client-as-brain handoff visible at the
   CLI. Not `EXIT_MISUSE`: asking where a lane's commands are is not misuse.
6. **Lane membership is read from `core/process_model.py`**, not from a second tuple. `COMMAND_NAMES`
   declares the top level; the single process-model declaration supplies which commands each lane
   registers — the same declaration the MCP lane dispatchers and the dashboard rails project from.
7. **Deprecation shims are hidden top-level parsers** in one named `DEPRECATED_TOP_LEVEL` table with
   `help=argparse.SUPPRESS`, warning via `Console.warn()` to **stderr only** (stdout carries data only,
   so a warning cannot corrupt a `--json` pipe), in the project's three-part grammar:
   `knotica: 'eval' has moved. Run: knotica improve eval. The old name still works and will be removed in a future release.`

## Considered Options

### A. Minimal rename — only what genuinely collides or misleads

Rename `gapfill` → `fill`, add `home`, leave the other thirteen names alone. Pros: one module rename,
one test-file rename, no new modules, zero disturbance to daily muscle memory. Cons: leaves `eval`,
`loop`, `compile`, `datasets`, `doctor`, `okf`, `migrate` and `guillotine` tool-shaped, making the CLI
the one surface that contradicts the process model every other surface now projects — the exact defect
the project exists to remove. Rejected on coherence, not on cost.

### B. Naive full lane nesting — `knotica <lane> <group> <verb>`

Pros: mechanically uniform; every existing module registers unchanged. Cons:
`knotica improve datasets bootstrap-train` is **four** levels for a command that is two today, and the
`datasets` / `compile` group names carry no information once the lane is named. Rejected on ergonomics
alone, in a project whose stated goal is reducing cognitive load.

### C. Lane nesting, two levels, flatten-unless-ambiguous (**chosen**)

Pros: worst depth two (three in two low-frequency places); daily cost is +1 word on five commands and
**zero** on the compound ones (`knotica improve bootstrap-train` and `knotica improve promote` are the
same length as today); **no module renames, no test-file renames**, so the topology gate and the
architecture-coverage path citations are untouched; hyphenated compound verbs at level 2 are already the
project's convention. Cons: six new modules in `DESIGN.md` § 3a; a flattening rule with two documented
exceptions is less mechanical than B.

### D. Lane-prefixed flat names — `knotica improve-loop`, `knotica tend-doctor`

Pros: exactly one level; no lane modules at all; trivially aliasable. Cons: fifteen-plus top-level
entries with no grouping, so Hick's-Law breadth gets *worse*, and `--help` cannot chunk them without the
hyphen doing the work a subcommand should do. Rejected.

## Consequences

**Positive**

- The CLI matches the process model without renaming a single module or test file, so three of the four
  `make verify` gates that run before the test suite need only a prose edit.
- Top-level breadth drops 15 → 12 and becomes chunkable into two labelled groups — a discoverability
  win independent of the rename.
- The deprecation shims are invisible in `--help` from day one, so the help surface is clean immediately
  rather than after the removal release.
- `learn` and `answer` give the two client-driven lanes a CLI presence, which no current command does.

**Negative**

- Six new modules to write, test and inventory; the `cli-surface` topology selector needs one added test
  file.
- +1 word on `eval`, `loop`, `compile`, `doctor` and `migrate` — the five most-typed commands.
- The shim table is the kind of compatibility layer that becomes permanent by default; `dec-050` removed
  the last one only because it had provably zero consumers.
- Two documented exceptions to the flattening rule mean the shape cannot be derived mechanically from
  the lane model; a future command has to be judged against the ambiguity test by hand.
- Every `main([...])` invocation in nine CLI test files changes, even though no file is renamed.

## Disconfirmation

**Falsifier.** The user reaching for the old names habitually and the stderr warning firing routinely
weeks after the rename — which would show the +1 word is not absorbed and the shims are load-bearing
rather than transitional. The shims log to stderr, so this is directly observable without new
instrumentation.

**Steelmanned runner-up.** Option A is stronger than its rejection admits. The CLI has exactly one
user, who already knows all fifteen names; "the CLI contradicts the model" is a coherence argument about
a surface nobody discovers by browsing. `--help` grouping plus renaming only `gapfill` → `fill` would
have delivered the lane vocabulary *where it is read* — the help text — at one module rename, no new
modules, no shim table, and no disturbance to muscle memory built over the project's life. The
coherence gain is real but is paid for entirely by the person who least needs it.

**Reversal trigger.** Revisit if (a) the shims are still firing after two releases, (b) a seventh lane
or a new command forces a third exception to the flattening rule — at which point the rule is not a rule
and B's uniformity is worth its depth, or (c) `core/process_model.py` does not in fact land as the lane
membership source, in which case a second registry appears and the "one declaration, three projections"
justification for the lane modules collapses.
