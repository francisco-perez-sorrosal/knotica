---
id: dec-072
title: An observation attempt that records nothing new costs the vault no commit
status: accepted
category: architectural
date: 2026-08-05
summary: The loop suppresses a loop-state write whose content would repeat what is already stored -- same content, and every LoopState field equal outside a named timing deny-list -- and moves the retry clock to a gitignored runtime marker so suppression cannot turn commit spam into eval spam.
tags: [loop, eval, retry, vault-hygiene, audit-trail, idempotence, runtime-state]
made_by: agent
agent_type: implementer
branch: feat-test-topology
pipeline_tier: standard
affected_files:
  - src/knotica/core/loop_attempt.py
  - src/knotica/core/loop.py
  - tests/test_loop_attempt.py
  - tests/test_loop_noop_attempt_characterization.py
dissent: "The identity is derived from the persisted document rather than from the attempt itself, so it can only ever suppress what LoopState happens to model. A failure whose distinguishing detail does not survive `str(exc)` -- two different upstream faults that stringify identically -- is now invisible in history where it previously produced a (redundant-looking but distinct-in-time) commit per attempt. The audit trail stops being a complete log of what the runner did and becomes a log of what it concluded, and nothing warns a reader of the difference."
---

## Context

`dec-068` recorded a live vault at **14,845 commits, of which ~14 were content**. The rest were
`observing …` / `observation eval error …` pairs, roughly one attempt per minute per topic for five
days, because two topics had no frozen golden set.

That ADR paced the known instance down roughly 60× by reading the raised error's own `retryable`
contract: `GoldenSetMissingError` is `NOT_CONFIGURED`, hence non-retryable, so it now holds on an
hour floor rather than a minute. Its own `dissent` field named what it had not fixed:

> "the loop still writes two commits per attempt, so the same class of bug returns at 1/24th the
> rate the moment another permanent failure appears that the error contract does not mark
> non-retryable. Suppressing no-op bookkeeping commits outright would have fixed the family rather
> than this instance."

That is exactly the residual defect. A permanent failure raised as a bare `Exception` publishes no
`retryable` claim and is treated as transient **by design** -- misclassifying a real transient
would stall genuine recovery for an hour -- so it still paces at 60 s and reproduces the original
growth at full speed. The structural cause is untouched by pacing: the attempt sequence writes
`evaluating` before the eval and `failed` after, both differ from the stored state, and nothing
asks whether either write carries information.

The reason it was deferred rather than bolted on is that the two writes are halves of **one**
attempt. Suppressing either alone still leaves unbounded growth at half the rate, so the fix needs
a notion of identity spanning the pair.

## Decision

A loop-state write is suppressed when the attempt is **materially identical** to the one already on
record. Two attempts are materially identical when both hold:

1. **They evaluated the same content** -- compared with the existing
   `LoopRunner._content_changed_since`, never by sha equality. The loop's own bookkeeping commits
   move the default branch's HEAD between attempts even when nothing a human wrote has changed, so
   sha equality would never converge.
2. **Every `LoopState` field is equal** outside a named deny-list of `updated_at` and
   `last_eval_started_at` (which time-stamp an attempt rather than describe one) and
   `candidate_sha` (covered by rule 1, and better left at the *first* head that exhibited the
   failure).

Rule 2 is deliberately a **deny-list, not an allow-list**. Anything that is not explicitly a
timestamp counts as information, so a field added to `LoopState` later participates automatically.
The asymmetry is the point: an identity that is too narrow costs a redundant commit, while one that
is too broad silently swallows a *new* failure -- and a swallowed new error is a debugging
catastrophe where a redundant commit is merely noise. `tests/test_loop_attempt.py` carries a
coverage guard that fails when a `LoopState` field is added without being classified.

Applied to the failure pair this yields: a first failure on new content still writes both halves
(nothing on record said an eval was in flight against that head, and that is what a reader has to
go on if the process dies mid-eval); an identical re-attempt writes nothing; a re-attempt that
fails *differently* writes once; a re-attempt that succeeds is recorded in full.

The logic lives in a new `src/knotica/core/loop_attempt.py` rather than in `core/loop.py`, which is
already 300+ lines over the project ceiling and tracked as `td-008`. The retry-pacing hold moved
there with it -- it is the same concern and now shares the same clock -- and `core/loop.py` shrank
by 23 lines net.

**The retry clock moves out of git.** Pacing previously read `LoopState.last_eval_started_at`,
which only advances when a state write happens. Suppressing the write would therefore have released
the retry floor on every tick, trading cheap commit spam for far more expensive **eval** spam (a
git clone plus LLM calls, every 5-30 s instead of every 60 s). The clock is now a gitignored runtime
marker under `.knotica/locks/`, alongside the heartbeat and progress files, advanced on every
attempt whether or not that attempt is recorded; `last_eval_started_at` remains the fallback for a
fresh machine or a cleared runtime directory.

It cannot live in process memory instead: `service.manager._default_run_topic` builds a **fresh**
`LoopRunner` on every supervision cycle, so in-process state would be empty on every tick in the
primary production path.

## Considered Options

### Suppress the write when the attempt records nothing new (chosen)

- Makes every commit in the vault mean something: the history becomes a log of conclusions, each
  one distinct from the last.
- Bounded by construction rather than by a tuned interval -- growth is now proportional to content
  changes and to *changes in verdict*, both of which are genuinely interesting.
- Costs a second, gitignored state location for the attempt clock, and a notion of identity that
  must be kept honest as `LoopState` grows.

### Make the pacing handle bare exceptions instead

The runner-up, and the option `dec-068` already partly took. Escalate the retry floor on repeated
identical failures (60 s → … → a day), so a permanent failure of *any* class decays toward
harmless regardless of what the error claims about itself.

- Simplest possible change; no second state store; no identity to maintain.
- Preserves a property this decision gives up: the git history remains a complete log of *what the
  runner did*, not only of what it concluded.
- But it does not fix the family either -- it re-prices it. Even at a one-day floor a permanently
  failing topic writes 2 commits/day/topic forever, and the escalation counter itself needs a
  durable home, which is the same second-state-store cost this option was supposed to avoid.
- Rejected because a no-information write is wrong at *any* rate. Pacing bounds how often it
  happens; only identity can stop it happening.

### Suppress only the `failed` half of the pair

- A one-line change with no new module and no clock relocation.
- Halves the growth and leaves it unbounded, which is the same defect at 1/2 the rate -- precisely
  the "instance not family" mistake this ADR exists to stop repeating.

### Rewrite history to repair already-bloated vaults

Ruled out of scope by `dec-068` and left ruled out here. The 14.8k commits stand. Rewriting a
user's wiki history to reclaim disk is a disproportionate remedy for a defect that is now fixed
going forward.

## Consequences

**Positive.** A permanently failing topic stops growing vault history entirely -- measured in
`tests/test_loop_noop_attempt_characterization.py` as ten re-attempts across ten fresh runners
adding zero commits while all ten still run the eval. Every commit that does land is distinct from
its predecessor. Crash safety improves as a side effect: a crash during a *suppressed* attempt
leaves `pending_retry=True` with the failing head still recorded, so the restarted runner is paced
by the floor, where a crash during the old pre-eval write left `stage=evaluating` with
`pending_retry=False` and no floor at all.

**Negative.** The vault history no longer records every attempt, only every conclusion -- the
`dissent` above. Runner liveness therefore depends entirely on the gitignored runtime files
(`loop_heartbeat`, `loop_progress`), which are machine-local: a vault inspected from another machine
can no longer tell a live-but-stuck runner from a dead one by reading git alone. And pacing now has
two possible clock sources, with the runtime marker preferred; a marker written by a clock of
different awareness (naive vs aware) is skipped rather than guessed at, which is correct but is a
seam a reader has to know about.

**Scope caveat.** Suppression applies only to the `observe_default` failure path. The success path,
the arena resolutions, `set_baseline`, and `rebaseline` are untouched -- each of those writes
carries a new scalar, decision, or operator intent by construction.

## Disconfirmation

**Falsifier.** A diagnosis that was delayed because git recorded nothing between a first failure and
a much later one -- someone needing to know *how many times* the loop tried, or *when it last
tried*, and finding the answer only in a machine-local file that was gone. That would show the
audit trail's completeness was load-bearing in a way this decision assumed it was not.

**Steelmanned runner-up.** Escalating the retry floor is the honest competitor. Its case: it keeps
one state store, needs no identity definition (nothing to get subtly wrong, nothing to re-audit when
`LoopState` grows), and preserves an audit trail that answers "what did the runner do" and not only
"what did it conclude" -- which is the question you actually ask at 3am. Bounded growth is not
unbounded growth, and 2 commits/day/topic would have been a defensible price for that property. The
counter is that the escalation counter needs durable storage anyway, so the simplicity advantage is
smaller than it first appears, and that a write which by construction carries no information is
hard to defend at any price.

**Reversal trigger.** Revisit if either holds: (a) the runtime marker proves unreliable in practice
-- a cleaner, a container restart, a synced vault -- and the fallback to `last_eval_started_at`
lets eval cost climb where commits used to; or (b) an incident shows attempt-level history was
needed. Either would argue for recording attempts again, at an escalating floor, and accepting
bounded growth as the price of a complete log.

## Prior Decision

`dec-068` (*Pace a failed observation eval by the error's own retryable contract*) stands unchanged
and is not superseded. Its floors are still the pacing mechanism -- `retry_hold` reads
`retry_floor_seconds` exactly as before -- and its config-wiring fix is untouched. This decision
generalises what `dec-068` fixed for one error class to every error class, along the axis its own
`dissent` field identified. The two compose: `dec-068` decides *how often* a failing topic
re-attempts, this one decides *whether* the attempt is worth recording.
