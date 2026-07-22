---
id: dec-draft-9e0a147d
title: Configurable eval cadence, td-011 re-arm, and a spend-gated dashboard eval trigger
status: proposed
category: architectural
date: 2026-07-22
summary: Global [loop] cadence/throttle (min-interval, quiet-window, thread-count) hooked into observe_default only; failed evals re-arm instead of consuming the cursor; a billed "run eval now" trigger gated by a two-phase decision envelope.
tags: [loop, cadence, eval, spend-safety, mcp, td-011, decision-envelope]
made_by: agent
agent_type: systems-architect
branch: worktree-eval-cadence-model-config
pipeline_tier: standard
affected_files:
  - src/knotica/core/loop.py
  - src/knotica/core/loop_state.py
  - src/knotica/core/loop_cadence_config.py
  - src/knotica/mcp_server/tools_dispatch_loop.py
  - src/knotica/mcp_server/tools_vault.py
  - src/knotica/dashboard/app.html
dissent: A billed action exposed as an MCP tool is agent-reachable by construction; the two-phase nonce prevents accidental billing but not a willfully-circumventing agent — CLI-only would be structurally stronger but fails the Desktop requirement.
---

## Context

The motivating incident: a first live E2E 429'd 25/25 golden questions against the
subscription rate window; the trustworthy-scalar guard refused a score; the loop wrote
`stage: failed` and — via `observe_default`'s failure handler calling
`mark_processed(default, head)` — **consumed the cursor**, so the failed content is never
re-scored unless new content lands or a cursor-free `knotica eval` is run (td-011).

Operators need to (a) throttle how often the observe leg evals (a daily batch, or quiet
hours, to live within a subscription rate window), (b) control eval concurrency (drop to 1
thread under rate pressure), and (c) force an eval on demand from Claude Desktop. All must be
additive and default-safe: `eval_min_interval_hours = 0` must reproduce today's
per-content-boundary scheduling byte-for-byte, and installation must gain no required step.

Constraints: candidate-branch (`loop/c/*`) gate evals must stay eager (exempt from cadence);
LoopState changes must be additive under `schema_version=1`; the client-as-brain and
stateless-server invariants hold (tools deterministic; config.toml + vault git are the only
state); and the user-runs-spend-commands convention makes any billed surface security-relevant.

## Decision

Add a **global `[loop]` config table** (`eval_min_interval_hours: float = 0.0`,
`eval_window: str | None`, `eval_num_threads: int = 4`) resolved by a new
`loop_cadence_config.py` mirroring `gapfill_config.py` (frozen dataclass, side-effect-free,
typed error on malformed values, missing table = defaults).

**Cadence hooks into `observe_default()` only** via a new `_cadence_hold(state, now)` guard in
the existing guard chain (after `_observation_hold`). It returns `None` — reaching no new logic
— when `eval_min_interval_hours == 0` and `eval_window is None`, preserving byte-identical
default-0 success-path scheduling. The candidate path (`poll_once`/`_process_candidate`) is a
separate method with separate call sites, so it stays eager by construction. A new wall-clock
injectable `now_fn: () -> datetime` (local tz) backs both interval-elapsed and window checks
(monotonic `self._clock` cannot express time-of-day nor survive a process restart); the existing
monotonic clock stays for the in-process debounce.

**td-011 is fixed in the same slice**: the failure handler stops calling `mark_processed`,
instead setting a new additive `pending_retry: bool` and leaving the cursor unadvanced, so the
content re-arms and is retried at the next eligible (cadence-throttled) tick. A new additive
`last_eval_started_at: datetime | None` anchors the interval and is set at eval start.

**Cadence is Desktop-controllable** via two new `loop` dispatcher actions:
`action=cadence` (deterministic read/write of the `[loop]` table, using `init.py`'s additive
config writer — agent-safe, not billed) and `action=run_eval` (the billed trigger). The
dashboard gains a cadence control and a "Run eval now" button beside the baseline-policy toggle.

**The billed trigger is gated by a two-phase decision envelope.** Phase 1 (`run_eval` with no
nonce) returns a decision-envelope payload (action, topic, resolved worker/judge, thread count,
estimated spend, a short-TTL confirm nonce) and bills nothing; phase 2 (`run_eval confirm=<nonce>
num_threads=N`) runs one `observe_default(force=True)` bypassing cadence at the requested thread
count. The action carries a `billed`/destructive annotation and is kept out of routine agent use
by the existing detection→`wiki_status` steering. CLI (`knotica eval`) remains the sanctioned,
stronger-guarantee spend path.

## Considered Options

### Option A — Global `[loop]` cadence + observe-only guard + two-phase billed trigger (chosen)

- **Pros**: One idiom (`gapfill_config.py`); one guard in one method; candidate path untouched
  by construction; byte-identical default-0 by early-return; td-011 fixed with two additive
  fields; billed action cannot bill in a single reflexive call; installation unchanged.
- **Cons**: An MCP-exposed billed action is agent-reachable; grows the already-oversized
  `loop.py` (td-008); at interval 0 a persistently-failing topic re-attempts every watcher tick.

### Option B — Per-topic cadence (in loop-state)

- **Pros**: Per-topic quiet hours; state travels with the vault.
- **Cons**: No per-topic config precedent anywhere; a new resolver pattern for a need the brief
  does not state; more surface, more tests, for speculative value (violates Incremental Evolution).

### Option C — Dedicated retry-backoff field for td-011 (separate from cadence)

- **Pros**: Explicit exponential backoff for persistent failures.
- **Cons**: A second throttle concept beside cadence; more state and logic than the problem
  needs; the cadence guard + watcher tick already bound retries. Deferred as the reversal path.

### Option D — Move the billed trigger off the shared MCP surface (CLI-only or HTTP-endpoint-only)

- **Pros**: Structurally un-pressable by a stdio-MCP agent; matches user-runs-spend-commands.
- **Cons**: Fails the explicit Desktop-MCP-App requirement (the iframe reaches tools via
  postMessage, not a separate HTTP origin); the user asked for an in-dashboard trigger.

## Consequences

**Positive**: Directly addresses the 429 incident (daily batch + quiet window + 1 thread);
td-011 resolved with minimal additive state; Desktop can throttle and force evals; default
install and behavior unchanged; candidate-gate eagerness preserved without special care.

**Negative**: `loop.py` grows further past its 800-line ceiling (td-008, tracked separately —
not bundled here); the billed trigger's agent-non-pressability is layered mitigation, not a
hard structural guarantee; the Sonnet-5-judge determinism change (companion ADR
`dec-draft-01a7689b`) interacts with cadence only via the shared eval path.

## Disconfirmation

- **Falsifier**: If a byte-identical test shows any scheduling difference at
  `eval_min_interval_hours=0`, or if telemetry shows an agent completing the two-phase billed
  flow unbidden, the design is wrong.
- **Steelmanned runner-up (DI)**: Option D (CLI/HTTP-only trigger). The user-runs-spend-commands
  convention is already load-bearing and documented; billed spend genuinely should not sit on the
  same tool surface an agent drives. Keeping the trigger off stdio MCP is the only *structural*
  (not merely procedural) guarantee, and the dashboard could emit the exact `knotica eval`
  command for the human to run — honoring both the "dashboard affordance" and "human runs spend"
  goals without a new billed tool. The chosen option accepts a weaker guarantee to satisfy the
  literal "pressable from the dashboard in Desktop" requirement.
- **Reversal trigger**: If dispatch telemetry ever records a `run_eval confirm=` completion not
  preceded by a human dashboard gesture, or persistent-failure retry storms appear at interval 0,
  move the trigger to Option D and/or add Option C's dedicated backoff.

## Prior Decision

None superseded. Re-affirms the two-tier tool-surface dispatcher ADRs (dec-039..047) by
extending the `loop` dispatcher with new actions rather than adding thin tools.
