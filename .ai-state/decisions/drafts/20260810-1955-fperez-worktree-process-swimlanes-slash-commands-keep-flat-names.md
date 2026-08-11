---
id: dec-draft-c5bf05c8
title: Slash commands keep their flat names; the lane is carried in the description
status: proposed
category: behavioral
date: 2026-08-10
summary: The `/knotica:*` aliases are not regrouped into `commands/<lane>/` subdirectories — the lane is expressed in each command's picker description instead, because a directory-derived rename has no deprecation path and a shipped hook emits two of the names.
tags: [swimlanes, slash-commands, plugin, published-surface, deprecation, picker-ux]
made_by: agent
agent_type: orchestrator
branch: worktree-process-swimlanes
pipeline_tier: full
affected_files:
  - commands/
  - hooks/session_start.sh
  - docs/reference.md
dissent: If the subdirectory is provably a display label only, this declines a free legibility win and leaves the six-lane vocabulary unexpressed in the one surface a user picks from most often.
---

## Context

The locked design regroups every entry point by lane, `/knotica:*` aliases included. The mechanism
assumed for slash commands is a directory move — `commands/doctor.md` → `commands/tend/doctor.md` —
and the pipeline scheduled a probe to answer whether that changes the *published* name
(`/knotica:tend:doctor`) or is only a grouping label in the picker (`/knotica:doctor`, unchanged).
The probe's answer was costed as changing the work by an order of magnitude.

The probe requires reading a live `/` picker. Two other evidence sources were tried instead. The
official `plugin-dev` skill shipped by Anthropic states *"subdirectory name becomes namespace"* and
shows `commands/review/security.md` invoking as **`/security`** with `(plugin:plugin-name:review)`
as a display label — i.e. label-only. But the same document shows the un-namespaced case invoking
as a bare `/security`, while knotica's commands demonstrably invoke as `/knotica:setup`. The doc's
model of the prefix does not match observed reality, so its model of the namespace cannot be relied
on either. Grepping the installed Claude Code binary for the naming logic returned bundler
internals, and no installed plugin anywhere on this machine uses a command subdirectory, so there
is no precedent to read.

The question is therefore **open**, and the decision has to be made under that uncertainty.

## Decision

`commands/` stays **flat**. Every `/knotica:*` alias keeps the name it ships with today. The lane is
carried in each command's `description` frontmatter — the text the picker renders — so the six-lane
vocabulary is visible where a user chooses, without any published name changing.

This satisfies the locked intent (every entry point reads as lane-organised) while declining the
specific mechanism (a directory that renames). It is the same move the architecture checkpoint
already made for the MCP surface when it replaced a flat lane-prefixed rename with the tiered lane
surface on the finding that **a lane is a facet, not a partition** — a directory is a partition, and
these fourteen commands do not partition cleanly by lane any better than the verbs did.

## Considered Options

### Adopt subdirectories now, betting on label-only

Cheap if right: fourteen `git mv`s, no body edits, no trigger loss.

Wrong, it breaks the published surface with **no deprecation path**. Concretely, and each verified:
`hooks/session_start.sh:53` prints *"Run /knotica:setup"* and `:84` prints *"Run /knotica:migrate"* —
a shipped hook instructing users to type commands that no longer exist; `docs/reference.md`'s alias
table and `docs/install.md` go stale; the referential-integrity gate resolves `/knotica:<alias>`
against `commands/*.md` and would report all fourteen as dead. Unlike the CLI half of this redesign,
where hidden shim parsers carrying `help=argparse.SUPPRESS` can warn on the old form, **there is no
mechanism to ship a deprecated slash-command alias** — the old name simply stops existing.

### Defer until the probe runs

Correct but costs a round-trip, and holds Step 37 open for a decision whose downside is asymmetric
regardless of the answer.

### Keep flat, express the lane in the description (chosen)

Zero published-surface change, so no deprecation is needed and nothing can break. All fourteen
commands already carry a `description`, verified. Grouping is delivered as text rather than as
structure.

## Consequences

**Positive.** No breaking change to a surface with no deprecation path. The shipped hook keeps
working. Step 37 collapses from a rename to a description edit, and stops depending on the probe.
The gates written this pass keep resolving. The decision is reversible in one commit if the probe
later proves label-only.

**Negative.** If the subdirectory *is* label-only, this leaves a free grouping win on the table —
the picker groups by description text rather than by an actual namespace, which is weaker. It also
means the six lanes are expressed differently across entry points: nested for the CLI, dispatchers
for MCP, prose for slash commands. That asymmetry is real and is the price of not betting.

## Disconfirmation

**Falsifier.** A live `/` picker showing `commands/tend/doctor.md` invoking as `/knotica:doctor`
with an unchanged name. That would make the subdirectory free, and this decision merely cautious.

**Steelmanned runner-up.** Adopt subdirectories. The official documentation does say label-only, and
it is the only first-party statement on the question; a directory namespace is a stronger grouping
than description text, and the redesign exists precisely to make process structure visible in the
surface. If the docs are right, this decision trades a real win for an unrealised risk.

**Reversal trigger.** The probe running and reporting label-only — at which point the move is
fourteen `git mv`s, one `rglob` in two gates, and this record is superseded.
