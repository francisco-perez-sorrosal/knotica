---
id: dec-120
title: The repo's own id-citation checker outranks the plugin copy, and a missing checker fails loudly
status: accepted
category: configuration
date: 2026-08-31
summary: The pre-commit gate resolves this repo's scripts/ checker first, falls back to the plugin, and exits non-zero when neither resolves.
tags: [gates, pre-commit, id-citation, worktrees, tech-debt, td-054, td-062]
made_by: agent
agent_type: implementer
branch: main
pipeline_tier: standard
affected_files:
  - scripts/check_id_citation_discipline.py
dissent: A per-repo checker copy can drift from the plugin's, so two Praxion projects can enforce subtly different rules under the same rule name.
---

## Context

td-054 recorded that the inbound id-citation gate scanned 0 files inside a
Praxion worktree — which Standard/Full tiers mandate. Repairing it surfaced a
larger fault in the same gate, in this repo, in **every** checkout:

- The installed `.git/hooks/pre-commit` resolved the plugin path from the key
  `i-am@bit-agora`. The plugin has since been renamed `praxion@bit-agora`, so
  `PLUGIN_ROOT` resolved to the empty string.
- The hook's next line was `[ ! -f "$CHECK" ] && exit 0`.

Together: the hook ran, found nothing, and exited 0 in silence. Both the
ruff-pin check and the id-citation check had been dead for as long as the
plugin has carried its new name. This is the gate-liveness "existence is not
operation" clause: the hook was installed, executable, and inert.

Two checkers exist and have diverged (this repo's 393-line copy vs the
plugin's 383-line one, with different exempt sets), so repairing the hook
forces a choice about which copy is canonical for this repo's gate.

## Decision

The hook resolves the checker as: **this repo's `scripts/check_id_citation_discipline.py`
first, the plugin's copy second, and a hard failure third.** The plugin-root
lookup tries the current key and then the legacy one.

This repo's copy is canonical for this repo's gate. It is the copy the
project's own tests exercise, the copy `td-062`'s section-citation pattern
lands in, and the copy that carries this project's `BASELINE_EXEMPT_PATHS`
decontamination backlog — none of which the plugin copy knows about.

## Considered Options

### A. Keep preferring the plugin copy (fix only the key)

- Pro: one implementation shared across every Praxion project; no drift.
- Con: this repo's project-specific exempt sets and baseline backlog live in
  the copy that would *not* run, so the gate would enforce a different rule
  than the one `make verify` and the repo's tests describe.
- Con: a project-local pattern extension (td-062's section citations) could
  never gate until it round-tripped through a plugin release.

### B. Prefer the repo copy, fall back to the plugin (chosen)

- Pro: the gate that runs is the gate the repo tests and documents.
- Pro: works in a fresh clone with no plugin installed.
- Con: the two copies drift; a fix made in one is not automatically in the
  other. Mitigated by fixing the worktree bug in the plugin's source too.

### C. Delete the repo copy and vendor nothing

- Rejected: `make verify` and the repo's own tests would lose their checker,
  and CI (no plugin installed) would lose the gate entirely.

## Consequences

Positive: the gate runs, and the copy that runs is the one under this repo's
tests. A missing checker now blocks the commit with a message naming the
cause, so this failure cannot recur silently.

Negative: two implementations of one rule now exist by design. Divergence is
a standing risk; the upstream fix for the worktree bug was applied to the
plugin's source repo as well so the divergence is narrowed, not widened.

## Disconfirmation

**Falsifier.** A commit lands carrying an ephemeral-id citation while the hook
reports success — meaning the resolution order picked a checker whose pattern
set or exempt set is not the one this repo believes it is enforcing.

**Steelmanned runner-up.** Option A is right if the checker is genuinely a
shared, versioned tool and per-project exempt sets belong in per-project
config the shared tool reads. That is the better long-run shape; it is not
today's shape, because this repo's copy already carries logic (baseline
backlog, section-citation pattern) with nowhere else to live.

**Reversal trigger.** If the plugin's checker grows per-project configuration
(an exempt-paths file it reads from the repo), delete this repo's copy and
revert to preferring the plugin.
