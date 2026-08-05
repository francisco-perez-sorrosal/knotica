---
id: dec-draft-b7e05d13
title: The stateless-server invariant is scoped, not widened, to exclude daemon runtime markers
status: proposed
category: behavioral
date: 2026-08-05
summary: "`.knotica/locks/` holds gitignored loop-daemon runtime markers that are neither session nor durable state; the 'vault and config.toml are the only state' sentence is restated once in PRE_PLAN.md and the other three sites point at it."
tags: [invariants, stateless-server, loop-daemon, documentation, single-source-of-truth]
made_by: agent
agent_type: orchestrator
branch: feat-test-topology
pipeline_tier: standard
affected_files:
  - docs/PRE_PLAN.md
  - CLAUDE.md
  - .ai-state/DESIGN.md
---

## Context

`td-031` moved the loop's failed-attempt retry clock out of git and into a gitignored marker under
`.knotica/locks/`, beside the existing heartbeat and eval-progress files. Its own ADR named the cost
plainly — "a second, gitignored state location for the attempt clock".

Meanwhile the sentence "the vault (git) and `config.toml` are the **only** state" is asserted in four
places: `CLAUDE.md:10`, `.ai-state/DESIGN.md` twice (the overview and the locked-invariants list),
and `docs/PRE_PLAN.md`'s stateless-server corollary. None was amended.

A sentinel audit found **no behavioural violation** — the invariant is scoped to the MCP *server*'s
session state, the retry clock belongs to the loop *daemon* (a separate process), and the precedent is
documented in always-loaded text (`CLAUDE.md` already says the loop "heartbeats to `.knotica/locks/`").
What is true is narrower and still worth fixing: the carve-out exists **only** inside one draft ADR's
Consequences prose, so a reader of any of the four sentences gets a claim the codebase no longer
satisfies literally.

## Decision

The invariant is **restated with its scope made explicit, not widened**. `.knotica/locks/` markers are
outside the claim rather than an exception to it, because:

- they belong to the daemon, not the server, and the server/daemon line is already drawn in
  `PRE_PLAN.md` for `dec-044`;
- nothing reads them to decide what is *true* — each is a liveness or pacing signal that a fresh
  process recomputes or safely ignores, so deleting the directory costs at most a re-derived interval;
- none is committed.

The full statement lives **once**, in `docs/PRE_PLAN.md`'s stateless-server corollary — the
authoritative design document per `CLAUDE.md`. The three other sites keep the invariant in their own
words and carry a short pointer instead of restating the paragraph.

## Considered Options

### Name the markers as a genuine exception to the invariant

Rejected. "Exception" implies the invariant was weakened, and it was not — the markers were never
inside its scope. Recording an exception would invite the reasonable inference that a *second*
exception is equally available, which is exactly the erosion the invariant exists to prevent.

### Amend all four sentences with the same carve-out

Rejected on the same ground the architecture-doc gate rests on (`dec-draft-9a3f24c7`): a claim
published four times is a claim that drifts three ways. Four independently-worded carve-outs would
reproduce the defect being fixed, one abstraction level up.

### Leave it — the code is right and the sentinel found no violation

Rejected. The literal reading of all four sentences is now false, and the audit trail for *why* lives
in one draft ADR's Consequences prose, which is the least discoverable place in the repo. The cost of
leaving it is that the next agent to hit this re-derives the server/daemon distinction from scratch —
or, worse, "fixes" the code to match the documentation.

## Consequences

**Positive.** The literal reading is true again. The server/daemon boundary is stated where a reader
looking for design rationale will find it, and the pointer-not-copy shape means a future change to the
scope is a one-file edit.

**Negative.** `CLAUDE.md` is always-loaded, so its pointer costs tokens on every session for a nuance
most sessions never need — accepted, at roughly a line, because a *wrong* always-loaded invariant is
more expensive than a slightly longer right one. And the reader of `DESIGN.md`'s locked-invariants
list must now follow a link to get the whole rule.
