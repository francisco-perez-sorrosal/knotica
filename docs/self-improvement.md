# Self-improvement

Knotica gets better at answering questions about a topic without touching a model weight. The
system's "weights" are its **prompts** and its **schemas** — ordinary files in the vault, under git.
This page covers what the loop measures, when it acts, what it changes, and what it costs.

- [The two loops](#the-two-loops) · [The cycle](#the-cycle-observe-gate-heal) · [Why a tick did nothing](#why-a-tick-did-nothing) · [Branch namespaces](#branch-namespaces) · [Baseline policy](#baseline-policy)
- [The arena](#the-arena) · [Compile](#the-compile-flywheel) · [The scalar](#what-the-scalar-measures) · [Cadence](#cadence-throttling-and-threads) · [What bills money](#what-bills-money) · [Running the loop](#running-the-loop)

## The two loops

| Loop | Changes | Driver |
|---|---|---|
| Inner | operation prompts (`.knotica/prompts/query.md`) and the compiled query program | DSPy compile (proactive), the arena (reactive) |
| Outer | topic schemas and page structure | SIA, human-reviewed, deliberately slow |

This page covers the inner loop — what `knotica improve loop` runs. A compile produces a JSON artifact; the
arena rewrites one markdown prompt. Both land as git branches you can inspect, revert, or delete.
Four engines share the word "loop". All four heal in the arena; what differs is what one invocation
does:

| Engine | Command | One invocation |
|---|---|---|
| Foreground watcher | `knotica improve loop --topic <t>` | observe, then gate one candidate — polling every **5.0 s**, and in watch mode behind the **20.0 s** debounce |
| OS-supervised daemon | `knotica service install` | observe, then gate one candidate — supervised every **30 s**, no debounce |
| Synchronous MCP tick | `loop action=run_once` | observe, then gate one candidate — once, no debounce |
| Forced MCP eval | `loop action=run_eval` | observe **only**, forced past the cadence hold |

`--no-arena` is the one off switch, and it belongs to `knotica improve loop` alone; under it a regression
records `"observation regression (arena disabled)"` and stops.

## The cycle: observe, gate, heal

One tick does three things in order. Any of them can be a no-op.

**1. Observe.** Has the default branch's *content* changed since the last observation? If so, clone
the vault at that commit, run the topic's frozen golden set against the clone, and compare the scalar
to the topic's baseline. The eval always runs on a throwaway clone — a guard refuses to start if the
clone destination resolves to the live vault root — and the loop never reverts default-branch
content. On success it fetches the clone tip onto `loop/r/<sha[:12]>` and merges it into the default
branch with a regular merge (this is how `metrics.jsonl` comes home; `--no-ff` is reserved for
compile promote), then decides. First match wins:

| Condition | Result |
|---|---|
| No baseline yet | Freeze the baseline here: `"first observation auto-froze baseline at X"` |
| The measuring instrument changed | Re-freeze here. **Not** a regression |
| `scalar > baseline` and policy is `best` | Raise the bar: `"new high-water baseline X (was Y)"` |
| `scalar >= baseline` | Pass, baseline unchanged |
| Otherwise | Regression — route to heal |

A fresh topic self-baselines on its first successful observation. There is no setup step.

**2. Gate.** Independently, the tick picks **at most one** unhandled `loop/c/*` tip and evaluates it
*at that branch's commit*. The branch name alone decides the route:
`loop/c/<topic>/source-<id8>` is a **source** candidate; anything else is a **prompt** candidate.

| Verdict | Prompt candidate | Source candidate |
|---|---|---|
| `scalar >= baseline` | keep: merge into default, delete the branch | keep, then stamp the linked suggestion `approved → ingested` |
| below baseline | try the arena; failing that, discard (delete the branch, default untouched) | refuse |

A refused source candidate is **renamed**, not deleted — `loop/c/<topic>/source-<id8>` becomes
`loop/x/<topic>/source-<id8>` — and a bounded diff of the top 10 regressed questions is committed to
`<topic>/.knotica/quarantine/source-<id8>.json`. The suggestion stays `approved`, so you can rework it.

> [!IMPORTANT]
> Source candidates are never raced through the arena, on either outcome. Content dilution is not a
> prompt problem, and a prompt variant that masks a dilution is exactly the reward-hacking hazard the
> gate exists to prevent.

**3. Heal.** A regression is first classified against the eval run's manifest. If **every** regressed
question is a knowledge cause, the arena is skipped and the failures are filed as gap records
(`"regression logged as N gaps; arena skipped"`) — racing prompt variants against a page that does
not exist is futile. Knowledge-cause verdicts are persisted to the live vault regardless of route;
[gap-fill](gap-fill.md) covers the diagnose → discover → approve → ingest spine that consumes them.
Otherwise the [arena](#the-arena) runs.

## Why a tick did nothing

Most ticks are no-ops by design. Each guard returns a readable reason, in this order.

| Guard | Fires when | Default |
|---|---|---|
| Cursor unchanged | HEAD equals the last observed commit | — |
| Bookkeeping-only diff | only `log.md` / `.knotica/` state moved | — |
| Ingest hold | an ingest session is active | stale after **600 s** |
| Quiet window | HEAD is not yet stable (watch mode only) | **20.0 s** |
| Failure retry hold | re-attempting the same content that just failed | **60 s**, or **3600 s** if blocked |
| Cadence hold | `eval_min_interval_hours` has not elapsed, or the clock sits outside `eval_window` | **0.0** and unset (both off) |

The bookkeeping filter is precise. `log.md` never counts. Anything under a `.knotica/` path segment
never counts — **except** `.knotica/prompts/`, which does, because a human prompt edit deserves a
fresh observation. Only the `page` and `source` families are scored, so hand-authoring a personal
note in `notes/` can never wake the loop or bill an eval (see [notes](notes.md)). An unclassifiable
path, or an unknown base commit from rewritten history, counts as content.

The quiet window is a debounce: a burst of commits — a multi-page ingest, a batch of edits —
coalesces into one eval at its natural boundary, and any change to HEAD restarts the 20-second timer;
`--once` forces it to `0.0`. The two retry floors key on the error's own contract: **60 s** for a
transient failure, **3600 s** when the error reports itself non-retryable (no frozen golden set, no
credential). If a blocked topic sits idle for an hour, that is why. Both are independent of cadence,
and both pace the **unattended** watcher only: `loop action=run_eval`'s confirmed leg clears them,
because the remedy for a blocked topic — freezing a golden set — is a `.knotica/` write that the
content-change check ignores by design, so no correct action would otherwise clear the floor before
it expired.

## Branch namespaces

Everything in flight is a branch. The set is declared once, in
[`branch_namespaces.py`](../src/knotica/core/branch_namespaces.py).

| Prefix | Created by | Means | Pruned by |
|---|---|---|---|
| `loop/c/` | a source-ingest publish, or any external producer | a proposed change awaiting the gate | deleted on keep and on discard; **renamed** to `loop/x/` on source refusal |
| `loop/r/` | every observation merge and every keep | audit pointer at the eval clone tip, carrying the metrics commit | newest 5, **merged pointers only** — an unmerged pointer is evidence of an interrupted run and is left alone |
| `loop/x/` | a source-gate refusal | a refused source candidate, kept as audit trail, invisible to the gate scan | newest 5 per topic |
| `loop/wip/` | an ingest session opening, on a private worktree | work in progress, invisible to the gate | renamed to its `loop/c/` name at publish; idle sessions swept after **24 h** |
| `compile/<topic>/` | `compile action=run` | a compile artifact awaiting human promotion | **never** auto-pruned |

`compile/*` is the one namespace that accumulates. Clear it with `branches action=delete`, which
refuses the default branch and the checked-out HEAD.

## Baseline policy

The baseline is the frozen scalar the gate defends, stored per topic in
`<topic>/.knotica/loop-state.json`. It is **`latest` by default**.

| Policy | Behavior |
|---|---|
| `latest` | The baseline tracks reality. It moves on exactly two events: the first-observation auto-freeze, and an instrument-change re-freeze. A better observation does **not** raise it |
| `best` | High-water mark. Any observation with `scalar > baseline` raises the bar to that scalar |

`loop action=rebaseline mode=best` re-picks the high-water mark from metrics history — but **refuses
to freeze a bar above the newest measurement** on the current instrument: such a bar fails every
candidate and arena variant by construction (the `baseline_unreachable` state `wiki_status` reports
and the Home attention inbox surfaces as a blocked row). The refusal names both scalars and points
at `mode=latest`. Drift *after* a legitimate freeze still requires a human rebaseline — lowering the
bar forgives a regression, so it is never automatic.

An **instrument change** means the harness fingerprint rotated. That fingerprint hashes the judge
prompt, the judge and worker model snapshots, the scalar formula version, and a runner config hash
folding the installed `dspy` version and the failure score — so a judge-prompt edit, a model
rotation, a formula bump, a `dspy` upgrade, or a `failure_score` change all rotate it. Thread count
is deliberately excluded; changing `--eval-threads` never invalidates a baseline.

The judge's **output budget** is deliberately outside that hash. A ceiling bounds how long a
response may be; it does not change what the judge writes within the bound, so raising it leaves
every score it ever produced still comparable. That matters because the budget was raised (512 →
2048) after real runs aborted on truncated judge responses — folding it in would have retired every
baseline in every topic for a change that alters no score.

On the first observation under a new instrument the old baseline is discarded and the new scalar
becomes the baseline. That auto-refreeze is **by definition not a regression**, so no arena race
fires: cross-instrument scalars are incomparable, and pretending otherwise would manufacture a false
alarm on every model rotation. For the same reason the [dashboard](dashboard.md) reads `unknown` —
not `fail` — when the last eval's harness version differs from the baseline's, or when no baseline
exists.

Three operator levers, none of which runs an eval:

| Lever | CLI | MCP |
|---|---|---|
| Switch policy | `knotica improve loop --baseline-policy latest\|best` | `loop action=baseline_policy policy=…` |
| Re-freeze from history | `knotica improve loop --rebaseline best\|latest` | `loop action=rebaseline mode=…` (default `best`) |
| Adopt HEAD as observed | `knotica loop --mark-observed` | — |

`--rebaseline` re-freezes from `metrics.jsonl`, restricted to records sharing the *newest* record's
harness version, and raises if the topic has no metrics history. `--mark-observed` is the recovery
escape hatch — advance the cursor, set stage `idle`, evaluate nothing — for an observation that was
interrupted and reconciled by hand.

## The arena

The reactive heal: when a prompt-recoverable regression lands, the arena races variants of the
topic's `query.md` and promotes a winner.

| Question | Answer |
|---|---|
| What races | Four bodies by default, derived from the topic's resolved `query.md`; override with `--arena-variants <JSON file>` (`[{id,label,body},…]`) |
| How variants are made | The shipped mutator appends a fixed text tweak, deterministically, with no model call |
| How a winner is picked | Highest score wins, and "clears" means meeting or beating the topic's eval baseline — but only if the scorer's scalars can be ranked against that baseline at all. See **Which scorer, and whether it can be compared** below |
| What lands | The winning body, written to `<topic>/.knotica/prompts/query.md` in its own commit — the only thing the arena mutates. It never touches page content |
| Where to look | `<topic>/.knotica/arena-state.json` (stages `idle`, `racing`, `promoting`, `completed`, `reverted`, `aborted`; variant statuses `pending`, `scored`, `winner`, `lost`) and the append-only `arena-history.jsonl`, via `arena action=status\|history` (`limit` defaults to 20). Each race and each variant also records `scorer_id`, `n_examples` and `golden_manifest_sha` |

After healing a failed gate candidate the wound `loop/c/*` branch is deleted, win or lose. After
healing an observation regression nothing is reverted — default-branch content is human-owned.

### Which scorer, and whether it can be compared

`[loop] arena_scorer` picks between two, and the difference is not a matter of accuracy — it is
whether the comparison the arena makes is meaningful at all.

| | `heuristic` (default) | `eval` |
|---|---|---|
| What it measures | Keyword matches in the prompt text. No model call, so it does not bill | The real golden-set harness, with only the `query.md` body swapped |
| Comparable to the gate baseline? | **No.** Different scale entirely | **Yes.** Same golden set, same judge, same scalar formula |
| What a race does | **Aborts** before scoring, stage `aborted`, with the reason on the record | Races normally |
| Cost | Free | One full eval **per variant** |

The default is deliberately inert. Ranking a keyword count against an eval-derived bar is not a
close contest, it is a category error — and it produced a real one: a race in which four variants
scored 0.79/0.80/0.81/0.82 was reverted for failing to clear a 0.9548 baseline, on a topic whose own
corpus scored 0.6562 on the same golden set. Every variant had beaten the live corpus. Nothing on
the record said which scorer ran, and `reverted` is also what a fair race nobody won looks like.

So the arena now refuses that comparison rather than losing it, and `aborted` is a distinct stage
from `reverted` for exactly that reason. Races recorded before this carry no `scorer_id` and are
reported `unverified: true` — they cannot be re-interpreted after the fact.

Switching to `eval` makes a win mean something and makes a race cost money; see
[configuration](configuration.md#arena_scorer-what-a-prompt-race-actually-measures).

## The compile flywheel

The proactive half: DSPy optimizes the query program against the topic's curated trainset. A compile
**refuses** unless all three gates hold.

| Gate | Threshold |
|---|---|
| `doctor --quick` reports no `FAIL`, and the vault worktree is clean | — |
| Query-style train examples in `qa.jsonl` | **≥ 30** |
| Records in the frozen golden set | **≥ 20** |

"Query-style" is exact: a `qa.jsonl` record whose verdict is `good` or `corrected` and whose query
does not start with `ingest `. Ingest curations do not count toward the 30.

Compile then clones, optimizes on the clone, post-evaluates, and branches. The optimizer tries
`MIPROv2(auto="light")`; **any** exception falls back to a bootstrap path, which is a valid route
rather than a failure. Bootstrap takes the topic's resolved `query.md` verbatim as the instructions —
the vault stays the single source of truth for prompt text — and attaches up to **8** few-shot demos
distilled from the trainset, preferring human-curated records over cold-start seeds. The fallback is
never silent: the artifact records `optimizer="bootstrap"` plus a `fallback_reason` naming the
exception. The post-eval gate then requires the compiled program to **strictly beat** the baseline; a
tie fails with `compiled_not_better`, and with no LLM client the compile refuses rather than
fabricating scores.

Compile produces `<topic>/.knotica/compiled/query_v1.json` and a manifest on branch
`compile/<topic>/<clone-head[:12]>`. **It never merges for you.** `compile action=promote` requires
that `branch` and defaults to `mode=dry-run`, which returns a plan; `mode=apply` takes the vault
lock, merges `--no-ff`, resolves conflicts on two known audit paths only (anything else aborts the
merge), and appends a compile metrics record so the promoted scalar shows on the loop chart. The
next `query` call then uses the compiled engine automatically — no second tool name, no engine field
in the envelope.

## What the scalar measures

Every eval produces one number in `[0,1]`, composed rather than measured. Per example:

```text
quality = clamp01( 0.7 * qa_accuracy + 0.3 * citation_validity )
```

`qa_accuracy` is the judge: a reference-based grader on `claude-sonnet-5` by default — overridable per
install via `[models].judge`, which rotates the harness fingerprint — drawn **3 times**
and scored by the **median** (the count is odd so the median is a real drawn sample; Sonnet 5 rejects
the `temperature` argument, so the samples are not temperature-pinned and the median is the
mitigation). `citation_validity` is deterministic with no model — if the golden reference carries
citations and the candidate cites nothing, that leg is `0.0`, not a vacuous `1.0`. Across the run
(`lint_violations` counts only findings attributable to the scored topic — its directory plus its
`sources/<topic>/` — never vault-level findings like `log.md` or the root schema; formula v2, the
same attribution rule `wiki_status` buckets with):

```text
lint_cleanliness = max(0, 1 - lint_violations / max(1, n_content_pages))
Q                = 0.85 * mean(quality) + 0.15 * lint_cleanliness
cost_factor      = clamp01( 1 - 0.3 * max(0, (T - T_target) / T_target) )
scalar           = clamp01( Q * cost_factor )
```

`T` is the median per-example total tokens for the run. `T_target` is computed on the topic's first
eval as `1.3 * T` and **frozen** into `<topic>/.knotica/eval.toml`, read back unchanged forever
after. The penalty is a hinge — under budget costs nothing — and multiplies rather than adds, so a
terse degenerate answer earns no bonus and a cheap low-quality answer cannot buy score.

Each run appends one line to `<topic>/.knotica/metrics.jsonl` — the scalar, its components, the
harness version, the example count, a `corpus_ref` of the form `git:<sha>` — pointing at
`<topic>/.knotica/eval-runs/gen-<N>/manifest.json`, which holds what the frozen record cannot: the
dataset hash, the weights actually used, exact token usage by model snapshot, `cost_usd`, the auth
mode, separate runner and judge cache hit rates, and per-example detail. One behavior surprises
people: an example whose prediction lacks usage accounting is an *instrument* failure — a malformed
runner response, an unparseable judge score — and the run **aborts loudly** rather than diluting the
scalar with a silent `0.0`.

## Cadence, throttling, and threads

The `[loop]` table in `config.toml` carries three keys. See [configuration](configuration.md).

| Key | Default | Effect |
|---|---|---|
| `eval_min_interval_hours` | `0.0` | Minimum hours between eval starts |
| `eval_window` | unset | Local-clock `"HH:MM-HH:MM"` range; an observation outside it holds |
| `eval_num_threads` | `4` | Bounded `1..8`. Only the MCP `loop action=run_eval` path reads it |

Both cadence knobs are resolved once, on the shared runner construction path, so both apply to all
four engines. `eval_window` accepts a midnight wrap — `22:00-06:00` means overnight, not never.

At all defaults the cadence check short-circuits before touching either knob. When it holds, it says
which one: `"cadence held: 0.42h since last eval start < 24h interval"`, or `"cadence held: outside
eval window 22:00:00-06:00:00"`. The candidate-gate path bypasses cadence entirely and is always
eager, and `loop action=run_eval` forces the observation. Forcing clears both **pacing** holds —
cadence and the retry floors — since both exist to pace the unattended watcher and `force` arrives
only from a two-phase, cost-quoted human confirm that cannot loop. The **ingest hold** and **quiet
window** still apply: those say the vault is mid-write, which no amount of human intent makes safe
to evaluate through.

Every eval resolves the `[models]` table: the watcher, the daemon, `loop action=run_once`, the
candidate gate, `loop action=run_eval`, and `knotica improve eval` all score with the operator's worker and
judge snapshots. The packaged defaults are `claude-haiku-4-5-20251001` (worker) and `claude-sonnet-5`
(judge). `[models].query` is a separate key naming the model behind the compiled query engine — its
packaged value is also `claude-sonnet-5`, not the Haiku worker — and it never folds into the harness
fingerprint, so editing it cannot disturb a baseline.

Worker and judge **do** fold into it. So setting either one is an instrument change: the next eval
rotates `harness_version` and auto-refreezes the baseline once, by design and not a regression. With
no `[models]` table — the default — the packaged snapshots apply and nothing rotates.

## What bills money

**Bills:**

- Any eval — the observation eval, a candidate-gate eval, `loop action=run_eval`,
  `loop action=run_once` with content pending, `knotica improve eval`. Roughly one worker call plus up to
  three judge samples **per golden question**, minus cache hits.
- `compile action=run` — the optimization plus the baseline-vs-compiled post-eval.
- Golden bootstrap, trainset bootstrap, and the best-effort trainset grower that runs after a source
  candidate passes the gate.
- The opt-in gap-discovery drain (external search API), capped at **5** gaps per regression. Off by
  default; it auto-enables only when a valid discovery key is configured. An explicit `config.toml`
  `false` always wins; an explicit `true` is honored only when a key resolves, and otherwise fails
  closed to off.

**Does not bill:** the arena at its default `heuristic` scorer (under `arena_scorer = "eval"` it
bills one full eval per variant); `set_baseline`, `baseline_policy`, `rebaseline`, `mark_observed`, and
cadence reads/writes; `branches action=scoreboard`, `arena action=status|history`,
`compile action=status`, `wiki_status`, `metrics_read`; `compile action=promote` and branch deletes
(git only); a warm-cache re-run on a frozen corpus; and any tick that returns "did nothing".

Two ceilings abort a run post-hoc, before any record is committed: **5,000,000 tokens** and
**$10.00** per run. The run cannot un-spend what it spent, but it refuses to commit the record.
Override with `--max-total-tokens` and `--max-usd`. Cost comes from a packaged table:

| Snapshot | Input $/Mtok | Output $/Mtok |
|---|---|---|
| `claude-sonnet-5` (judge) | 5.00 | 25.00 |
| `claude-haiku-4-5-20251001` (worker) | 3.00 | 15.00 |

A snapshot absent from that table contributes `$0` — so a `[models]` override to a snapshot the price
map does not know zeroes the USD figure for every eval, background ones included, and the token
ceiling becomes the only live guard. Under OAuth the cost figure is notional, which the manifest's
`auth_mode` field lets you detect.

Both caches — the runner's synthesis cache and the judge's score cache — sit *above* the usage
accounting, at `<tmp>/knotica-eval-cache/<corpus_sha>/`, namespaced per corpus and never in the
vault. A warm hit makes no API call, so it contributes zero to the billed total, the ceiling, and the
cost figure, while its replayed usage still feeds the token median. That is why a warm re-run
reproduces the scalar exactly yet passes a ceiling a cold run breached.

> [!IMPORTANT]
> `loop action=run_eval` and `loop action=run_once` never bill on a bare call. The first call mints a
> single-use nonce with a **300-second** TTL and returns a preview — estimated cost, the nonce, the
> TTL, plus (for `run_eval` only) the worker and judge snapshots and the thread count. Only a second
> call passing that nonce as `confirm` executes. The nonce file is deleted unconditionally on read,
> so a wrong `confirm` cannot probe a live nonce, and the two actions use separate files: a nonce
> minted by one can never confirm the other.

## Running the loop

```bash
knotica improve loop --topic quantum          # watch forever; Ctrl-C exits cleanly
knotica improve loop --topic quantum --once   # one tick; exit code 1 if an acted step failed its gate
```

`--topic` is required; everything else has a default. `--once`, `--set-baseline SCALAR`,
`--baseline-policy`, `--rebaseline`, and `--mark-observed` are mutually exclusive modes, and the last
four run and exit without evaluating anything. Tuning knobs: `--interval` (`5.0` s, floored at 0.2),
`--observe-quiet` (`20.0` s), `--eval-threads` (`4`, range 1–8), `--push REMOTE`, `--no-arena`,
`--no-observe`, `--branch-prefix` (`loop/c/`), and `--arena-variants`. The MCP `loop` dispatcher
exposes `run_once`, `set_baseline`, `baseline_policy`, `rebaseline`, `cadence`, and `run_eval`;
`branches`, `arena`, and `compile` cover the rest. Full parameter lists: [reference](reference.md).

`knotica service install` registers the loop with launchd (macOS) or systemd `--user` (Linux),
supervising every 30 seconds and re-resolving the topic set fresh each cycle, so a topic added after
install needs no reinstall. Each cycle is the same observe-then-gate tick the foreground watcher
runs, arena included; the one difference is the debounce, which the daemon does not apply.

Runtime liveness lives in gitignored files under `<vault>/.knotica/locks/`: a per-topic heartbeat, an
eval progress file, and the attempt clock the retry floors read. A runner counts as dead once its
last beat is older than three poll intervals — staleness, not absence, is the signal. The only
orchestration metadata the live vault commits is `loop-state.json`.
