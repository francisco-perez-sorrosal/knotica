# Roadmap — the self-improving memory core

<!-- Plan of record for turning knotica's careful but human-heavy loop into one autonomous core that
     runs both topic wikis and agent memory, and that can later be deployed and driven by a remote
     personal agent. Tool-agnostic on purpose: plain Markdown, readable and updatable with or
     without any pipeline. The design canon stays .ai-state/DESIGN.md; decisions stay ADRs. This
     file says what is built in which order, how each step is proven, and where it stands. -->

| Field | Value |
|---|---|
| **Status** | Plan of record. No step started |
| **Created** | 2026-11-22 |
| **Baseline** | knotica 0.4.0 at `d93b1b3` — 21 MCP tools, 123 finalized ADRs, 3,589 tests |
| **Origin** | The proposal "Knotica: a self-improving memory core" (2026-11), reviewed against the code and its four source posts on 2026-11-22 — see [§ 6](#6-what-changed-from-the-proposal) |
| **Evidence base** | Private vault `meta-memory` (repo `francisco-perez-sorrosal/knotica-meta-memory`), first topic `memory-core`: a knotica knowledge base about building this feature, updated at the end of every step |

## 1. Goal

Today knotica improves itself only while a person keeps feeding it: ten kinds of step need a human
(ingest, curation, golden review, compile promotion, re-baselining, source approval, the ingest
handshake, guillotine verdicts, note drift, billed confirms). The goal is one core in which a person
owns **direction** (the charter and the goal list) and the **instrument** (judge, rubric, golden,
canary), and everything between the two runs on its own, measured, budgeted and reversible. The same
core serves two profiles — a topic wiki and an agent's memory — and can run locally or behind an
authenticated remote endpoint that a personal agent operates.

Done means all of these hold on the evidence base:

- Human touches per merged change fall from the baseline recorded in step 0.2 to four kinds only:
  charter, instrument, escalations, audits.
- The canary scalar stays flat or rises while the main scalar rises.
- Goals close without a human in the loop, and audited merges agree with the human at least 90% of
  the time.
- A remote personal agent can read, write and operate a vault through an authenticated endpoint,
  restricted to an allowlist of principals.

Non-goals: changing model weights; a multi-tenant hosted product; letting the system edit its own
instrument; replacing Obsidian as the reading surface.

## 2. How to use this file

- **The tracker in [§ 3](#3-progress-tracker) is the single place progress is recorded.** The PR that
  lands a step updates its row in the same change.
- **One branch and one pull request per step**, against `main`. A step that moves an invariant starts
  with its ADR draft and the `DESIGN.md` amendment, before code.
- **Step ids are stable.** Never renumber; a new step takes the next free number in its stage. Code
  and tests never cite a step id — the id-citation gate treats them as ephemeral. They cite ADRs.
- **Changing the plan is a pull request against this file** plus an entry in
  [§ 4](#4-decisions). Reordering inside a stage is free; moving a step across a dependency named in
  its block is not.
- **Each step begins by re-reading its block** and restating its falsifier. A step whose falsifier
  fires is recorded as such and the plan is revised, not the measurement.

## 3. Progress tracker

Status values: `planned` · `in progress` · `landed` · `dropped`.

| Id | Step | Proposal | Bills | Status | PR | ADRs |
|---|---|---|---|---|---|---|
| 0.1 | Portable baseline | — | no | planned | | |
| 0.2 | Meta-memory knowledge base, version 0 | — | first scalar only | planned | | |
| 1.1 | Principals, audit trail, human-touch metric | PR 1 | no | planned | | |
| 1.2 | Capabilities, access seam, portability ratchet | — | no | planned | | |
| 1.3 | Noise floor of the scalar | — | yes | planned | | |
| 1.4 | Canary holdout | PR 2 | yes | planned | | |
| 1.5 | Retrieval diagnostics | — | no | planned | | |
| 2.1 | Goals, progress, hot layer, charter | PR 3 | goal checks only | planned | | |
| 2.2 | Session protocol | PR 4 | no | planned | | |
| 3.1 | Deterministic floors and content candidates | PR 6 (half) | no | planned | | |
| 3.2 | Real arena | PR 5 | yes | planned | | |
| 3.3 | Budgets and adaptive depth | — | no | planned | | |
| 3.4 | Worker seam and the headless-client worker | PR 7 | yes | planned | | |
| 3.5 | Rubric evaluator | PR 6 (half) | yes | planned | | |
| 3.6 | Scale benchmark | — | no | planned | | |
| 4.1 | Autonomy levels and escalation | PR 8 | yes | planned | | |
| 4.2 | Sampled audits and circuit breaker | PR 9 | no | planned | | |
| 4.3 | Screener-assisted instrument review | PR 10 | yes | planned | | |
| 5.1 | Episodes and capture | PR 11 | no | planned | | |
| 5.2 | Profiles and memory tools | PR 14 | replay grading only | planned | | |
| 5.3 | Dream passes and claim status | PR 12, PR 13 | yes | planned | | |
| 5.4 | Outer loop | PR 15 | yes | planned | | |
| 6.1 | Ref-targeted transactions and the version-control port | — | no | planned | | |
| 6.2 | Remote spike on Wasmer Edge | — | no | planned | | |
| 6.3 | Edge version-control adapter | — | no | planned | | |
| 6.4 | Stateless runtime state | — | no | planned | | |
| 6.5 | Authentication and authorization | — | no | planned | | |
| 6.6 | Jobs and the worker plane | — | yes | planned | | |
| 6.7 | Deploy and connect agents | — | no | planned | | |

"Bills" means the step cannot be demonstrated without an LLM credential
(`CLAUDE_CODE_OAUTH_TOKEN` preferred, `ANTHROPIC_API_KEY` as the metered fallback).

## 4. Decisions

### Made

| Date | Decision |
|---|---|
| 2026-11-22 | The worker is a **headless client first**: a non-interactive Claude Code (or other agent) session that fulfils the existing `handoff` stage through the MCP surface, behind a `Worker` seam. A server-side generator may follow as a second adapter. Client-as-brain is not amended |
| 2026-11-22 | The evidence base is the private repo `knotica-meta-memory`, vault name `meta-memory`, first topic `memory-core`, built with knotica 0.4.0 as it is so the starting state is on record |
| 2026-11-22 | One branch and one pull request per step |
| 2026-11-22 | The LLM credential is supplied on request, at the first step that bills |
| 2026-11-22 | This file is the plan of record and lives at the repository root |
| 2026-11-22 | The remote target is Wasmer Edge, driven by a remote personal agent, behind authorization. Everything runs locally first; the architecture is kept deployable from step 1.2 on ([§ 8](#8-deployment-readiness-rules)) |

### Open

| Id | Question | Recommendation | Needed by |
|---|---|---|---|
| O-1 | Install Praxion on this machine, and by which route? | Yes — at least a checkout that `scripts/install_git_hooks.sh` can find, so ADR drafts get numbered | 1.1 (first ADR) |
| O-2 | Where does the actor live: a slot in the `log.md` line, or git commit trailers? | Trailers — the log grammar is round-trip validated and OKF-governed | 1.1 |
| O-3 | Is `HOT.md` a scored file? | Yes if the answer path reads it; then edits go through the gate | 2.1 |
| O-4 | Do golden and canary freezes keep a human signature when a screener pre-sorts? | Yes — the instrument stays out of the improver's reach | 4.3 |
| O-5 | Who is the first consumer of the memory profile? | Claude Code sessions through the plugin hooks, then any agent over MCP | 5.1 |
| O-6 | Are episodes captured on every `query`, or only on an explicit call? | Explicit and batched — `query` commits nothing today and should stay a read | 5.1 |
| O-7 | Where does claim status live: page frontmatter or inline markup? | Decide with a spike on the guillotine's passage model | 5.3 |
| O-8 | Edge version-control adapter: forge HTTP API or a pure-Python git? | Decide from the measurements of step 6.2 | 6.3 |
| O-9 | Which authorization server? | A hosted one that supports both client-ID metadata documents and dynamic registration | 6.5 |

## 5. Where knotica stands

Verified against the code on 2026-11-22. The design canon is
[`.ai-state/DESIGN.md`](.ai-state/DESIGN.md); the code-verified guide is
[`docs/architecture.md`](docs/architecture.md).

**Already in place, and the reason this is feasible:** one mutation path (`VaultTransaction`), git
as the only durable state, a frozen held-out golden set with a content-addressed manifest, a hashed
judge prompt, candidate branches, a gate that keeps or discards, and a stateless server whose lean
launch path needs only `mcp` and `pydantic`.

**Missing for autonomy:**

- No worker acts without a chat: ingest and lint are client-driven.
- No goal list says what done means; the loop only defends a baseline.
- No handoff file carries state between sessions.
- No policy decides when a change may merge on its own.
- The reactive heal is inert by default: the arena's default scorer is a keyword heuristic that
  aborts the race, and its default mutator appends a fixed sentence
  (`src/knotica/core/arena.py`).
- The outer loop is planned only; `src/knotica/agent/` does not exist.
- Nothing is an agent memory: only the `page` and `source` families are scored, and there is no
  episodic store.

**Missing for remote operation:** the HTTP transport exists (`knotica mcp --http`) but binds to
`127.0.0.1`, allows every origin, and has no authentication. The write path depends on the `git`
binary, `fcntl.flock`, git worktrees on local disk and runtime files under `.knotica/locks/`
([§ 11](#11-remote-target)).

## 6. What changed from the proposal

The proposal holds up: every repository path it cites exists, every file it calls new is absent, and
of 38 claims about its four source posts none is contradicted. Three are overstated — the memory
post says memory is only "occasionally" a prediction; it floats the volatility and cadence idea as an
open question; and the harness-design author later dropped sprint contracts and per-sprint evaluation
with a stronger model. The roadmap differs from it in these ways:

1. **Margins are expressed in noise units.** On a 20-question golden set one answer flipping moves
   the scalar by about 0.03, more than the proposal's 0.01 and 0.02 margins. Step 1.3 measures the
   noise before any threshold is set.
2. **The canary is a separate frozen set**, not a slice of golden, which would make both noisier.
3. **Goal pass rate is treated as coverage, not generalization.** The generator sees the goals. A
   closed goal proposes a sibling held-out question so the instrument grows with the wiki, and the
   meaning of "answers correctly" for a goal with no reference answer is fixed by an ADR.
4. **The instrument keeps a human signature.** The proposal's own layer table reserves the
   instrument for the human, while its PR 10 lets a screener review golden candidates. Here the
   screener pre-sorts and the human signs the freeze (open decision O-4).
5. **The worker is a client first**, which leaves client-as-brain intact.
6. **Four things the proposal describes but never schedules get steps:** budgets, the charter
   planner, claim status, and a third candidate kind — today any candidate that is not a source is
   raced through the arena when it fails.
7. **Pruning gets a deterministic floor.** A small golden set cannot see a deleted claim, so a
   candidate fails if a cited source drops out without a guillotine trial.
8. **Episodes are captured explicitly**, not on the query path (open decision O-6).
9. **Contract negotiation is deferred and the reviewer starts advisory.** The goal record is the
   contract; the source post reports the evaluator talking itself into approving work.
10. **The actor becomes a principal with capabilities**, because the remote target needs both.
11. **A remote stage is added** for Wasmer Edge, authorization and a worker plane.
12. **Two measurement steps are added** — retrieval diagnostics and a scale benchmark — so quality
    as the vault grows is measured rather than assumed.

## 7. Quality bar

A step is done when all of these hold.

1. **Decision first.** Any change to a seam, an invariant or a record schema starts as an ADR draft
   under `.ai-state/decisions/drafts/`. If an invariant moves, `DESIGN.md` § 7 is amended before
   code. Each ADR names the measurement that would refute it.
2. **Tests carry the behavior.** New behavior is specified by tests; every new invariant gets a
   fitness test that was seen to fail; no test touches the network (`FakeLLMClient`, a scripted fake
   worker); every new test file is registered in `.ai-state/TEST_TOPOLOGY.md`.
3. **The gate is green.** `make verify` locally and CI on the pull request; the 800-line file
   ceiling holds; the dashboard artifact is rebuilt when its source changes.
4. **Docs match the code.** `DESIGN.md` § 3 inventory and `docs/architecture.md` for structure;
   `docs/reference.md` for every verb and flag, each with its default; the feature page for the
   mechanism.
5. **Off by default.** Anything that raises autonomy or spend ships disabled and is switched on per
   topic.
6. **Evidence on the evidence base.** Before and after are measured on `meta-memory`; a measurement
   an ADR names as its falsifier is written under `.ai-state/measurements/`; a retrospective is
   stored in the knowledge base.
7. **Reversible.** One pull request is one revertable unit; every vault effect is a commit or a
   branch.
8. **Deployable.** The rules in [§ 8](#8-deployment-readiness-rules) hold.
9. **Tracked.** The tracker row changes in the same pull request.

## 8. Deployment-readiness rules

Everything runs locally first. These rules keep the architecture deployable while it does, and apply
to all new code from step 1.2 on. Step 1.2 turns D2, D3 and D6 into tests.

| Rule | Statement |
|---|---|
| D1 | **No new local state.** Whatever must outlive a call is a commit or a ref in the vault, or goes through the runtime-state seam of step 6.4 |
| D2 | **No new process or OS coupling.** No new use of `subprocess`, `fcntl`, the home directory or the temp directory outside the modules that use them today |
| D3 | **Every verb is classified.** Its capability class is declared once in `src/knotica/core/process_model.py`; an unclassified verb fails the build, and access is denied by default |
| D4 | **Every commit names its principal** |
| D5 | **Nothing on the MCP surface outlives a call.** Long or billed work is requested and then observed, never awaited. Target: a call returns within 60 seconds |
| D6 | **The lean path stays lean.** Importing the server pulls in `mcp` and `pydantic` only, and runs on Python 3.12 and 3.13 |
| D7 | **Tools are sufficient.** No capability is reachable only through MCP prompts, resources or the MCP App, because a personal agent may support tools alone |
| D8 | **Time, randomness and identity are injected**, so audits and samples are reproducible |

## 9. Metrics

The baseline column is filled in at step 0.2 and never rewritten; later values go in the knowledge
base.

| Metric | Measures | Direction | First available |
|---|---|---|---|
| Human touches per merged change | The headline goal | down | 1.1 (hand count at 0.2) |
| Main scalar, with its noise band | Answer quality on the golden set | up | 0.2, band at 1.3 |
| Canary scalar | Improvement that is not overfitting | flat or up | 1.4 |
| Retrieval recall at k | Whether the right pages are found as the vault grows | flat or up | 1.5 |
| Goal pass rate | Coverage of the charter | up | 2.1 |
| Tokens per answered question | Memory efficiency | down | 0.2 |
| Spend per merged change | Cost of autonomy | bounded | 3.3 |
| Audit agreement | Whether autonomy is trustworthy | at least 90% | 4.2 |
| Rollback rate after merge | Bad merges that got through | down | 4.2 |
| Verified-claim share, staleness | Knowledge health | up, down | 5.3 |
| Wiki-to-source size ratio | No map as large as the territory | under the profile cap | 3.1 |

## 10. Steps

Each block gives why the step exists, what lands, what you will see on the evidence base, and the
measurement that would prove the step wrong.

### Stage 0 — Ground

No behavior changes.

#### 0.1 Portable baseline

- **Why.** Two tests are tied to the machine they were written on, so `make verify` cannot be
  relied on everywhere. `test_uvx_missing_skips_the_nudge_entirely` strips every `PATH` directory
  holding `uvx`, which removes `sh` when uv is system-installed; it fails every time on such a
  machine. `test_watch_mode_observes_once_the_quiet_window_elapses` replaces `time.sleep` on the
  shared module, so the standard library's own subprocess polling is counted as loop ticks; it is
  timing-dependent, and on Fedora it failed five runs and passed one on 2026-11-22.
- **Lands.** Both tests made independent of machine layout and timing.
- **See.** `make verify` green on Fedora and in CI.

#### 0.2 Meta-memory knowledge base, version 0

- **Why.** The starting state has to be on record before anything changes, and every later step
  needs somewhere real to be demonstrated.
- **Lands.** The private repo and vault; topic `memory-core`; sources ingested by a normal
  client-driven session — the proposal, its four source posts, the LLM-wiki gist, the review, this
  roadmap and the three research reports of 2026-11-22; pages; a golden set that the owner reviews
  and freezes; the first eval.
- **See.** A browsable vault, a baseline scalar, and a hand count of every human touch it took.
- **Needs.** The LLM credential for the golden bootstrap and the first eval; the owner's review of
  the golden candidates.

### Stage 1 — Measure and identify

#### 1.1 Principals, audit trail, human-touch metric

- **Why.** Less human work cannot be shown until it is counted, and the remote target needs to know
  who acted.
- **Lands.** A `Principal` — kind (`human`, `agent`, `gate`, `policy`), id, and surface — resolved at
  the adapter edge and carried into `VaultTransaction`; recorded on every vault commit (open
  decision O-2); a classification of which operations are human decisions; the metric in
  `src/knotica/core/metrics.py` and the status payload.
- **See.** Touches per merged change in `knotica status` and the `home` inbox, computed over the
  evidence base's own history. Commits older than this step read as `unknown`.
- **Limit, stated honestly.** Locally the principal is self-declared; it becomes authenticated only
  in step 6.5.
- **Falsifier.** If the metric cannot separate a human-driven ingest from a worker-driven one on
  real history, the classification is wrong.

#### 1.2 Capabilities, access seam, portability ratchet

- **Why.** Every verb added from here on should be born classified, and the instrument guardrail
  should be mechanical rather than a convention.
- **Lands.** A capability class per verb in the process model — `read`, `write`, `operate` (bills or
  runs a loop), `admin` (vault registry and configuration), `instrument` (golden, canary, rubric,
  judge, lowering a baseline). One access check at the seam every tool call already passes through
  (`src/knotica/mcp_server/vault_ctx.py`). The local policy grants the owner everything; principals
  of kind `agent` or `policy` are never granted `instrument`. A ratchet test freezes today's set of
  modules that use `subprocess`, `fcntl`, home or temp directories and `.knotica/locks/`. A check
  that the server import path stays lean and runs on Python 3.13.
- **See.** A census test listing every verb with its class; a refusal envelope exercised by a test
  policy; the ratchet in `make verify`.
- **Falsifier.** A verb that needs two classes depending on its arguments and cannot be expressed
  through the process model's existing discriminator.

#### 1.3 Noise floor of the scalar

- **Why.** Every later threshold — canary alarm, merge margin, escalation band — is meaningless
  below the instrument's resolution.
- **Lands.** A repeated-eval mode that bypasses the caches and records the spread and the
  per-question flip rate; the scalar reported with its band; thresholds configured as multiples of
  the measured spread.
- **See.** "0.74 ± x" for the evidence base, and an ADR that replaces the proposal's fixed margins.
- **Falsifier.** A spread under 0.005 at 20 questions would mean the fixed margins were fine.

#### 1.4 Canary holdout

- **Why.** Once the arena selects prompts by golden score, golden is no longer an unbiased measure.
- **Lands.** A second frozen set with its own manifest (`split: canary`), disjoint from golden and
  from the trainset, written from sources rather than from pages, loadable only by the canary eval
  (fitness test), frozen only with the `instrument` capability. An alarm when the main scalar rises
  and the canary falls by more than the noise band.
- **See.** Two lines on the chart and an attention row when they diverge.
- **Falsifier.** If canary and golden never diverge across the arena runs of step 3.2, the canary is
  not buying anything and its eval cost should be cut.

#### 1.5 Retrieval diagnostics

- **Why.** As more information enters the vault, answers fail for three different reasons —
  the knowledge is absent, the right page is not retrieved, or the synthesis is wrong. Only the first
  is fixed by ingesting more.
- **Lands.** Recall at k and reciprocal rank per golden question, computed from the retrieval trace
  the eval manifest already records against the pages each golden answer cites. Diagnostics only: no
  change to the scalar or the harness fingerprint, so no baseline re-freezes.
- **See.** The share of failures by cause over time.
- **Falsifier.** If recall is at ceiling on every topic, the diagnostic can wait for step 3.6.

### Stage 2 — Harness

Still client-driven. No invariant changes.

#### 2.1 Goals, progress, hot layer, charter

- **Why.** A goal list turns the loop from defending a baseline into closing named goals; a handoff
  file lets a fresh session get its bearings; a small injected layer carries what every session
  must know.
- **Lands.** `<topic>/.knotica/goals.json` as a versioned record family, with one operation that
  flips `passes` and that only the gate principal may call. `progress.md`, one entry per session.
  `HOT.md` with a hard cap (open decision O-3). A charter step, client-driven: the owner writes a few
  sentences, the client drafts goals and coverage areas, the owner approves the list. The goal check
  defined by an ADR: answered through the real `query` path, citing a source that matches
  `must_cite`, with full citation validity. A rule that a closed goal proposes a sibling held-out
  question into the instrument's staging area.
- **See.** A goal pass rate starting at 0 of N; the hot layer printed at session start.
- **Falsifier.** If goals close while the canary does not move, goals are being answered by rote and
  the check is too weak.

#### 2.2 Session protocol

- **Why.** The two harness posts name the failures an unattended run hits: doing too much at once,
  declaring victory early, leaving half-done state. The cure is a protocol backed by tools, not more
  prompt.
- **Lands.** A deterministic `begin` that returns bearings — the tail of `progress.md`, recent
  commits, failing goals by priority, open gaps, the hot layer, budget left, a quick health check —
  and exactly one recommended work item. A deterministic `end` that refuses an unclean exit, appends
  the progress entry and makes one commit. The claim on a work item is the existence of its
  work-in-progress branch, so two sessions cannot take the same item and no new local state is
  needed.
- **See.** One `progress.md` entry per working session on the evidence base — the build journal of
  this feature.
- **Falsifier.** Sessions that routinely need information the bearings do not carry.

### Stage 3 — Generate and evaluate

#### 3.1 Deterministic floors and content candidates

- **Why.** The cheapest reliable checks should run first and always, and candidates that carry
  content must never be raced through the prompt arena.
- **Lands.** A third candidate kind in `src/knotica/core/branch_namespaces.py`, for work that
  originates from a goal or a dream pass. Floors as pure functions over the candidate: grounding of
  new claims, verbatim quote fidelity against `sources/`, structure (topic lint not worse),
  compression (pages over sources, under the profile cap), and source preservation (no cited source
  lost without a guillotine trial). The gate verdict becomes a record listing each floor.
- **See.** A refused candidate names the floor it failed. No model call, so no cost.
- **Falsifier.** Floors that refuse candidates a human audit would have accepted more than one time
  in ten.

#### 3.2 Real arena

- **Why.** The only reactive heal knotica has does nothing by default.
- **Lands.** A model-backed mutator that reads the regressed examples and chooses to refine or to
  pivot. The eval scorer when a credential and budget allow. The winner confirmed on the canary. A
  configuration switch that also covers the daemon, which today has none (td-050).
- **See.** A regression planted in the evidence base's `query.md` heals without a human, with the
  diff and its rationale in the arena history.
- **Falsifier.** If model-written variants do not beat the fixed tweak over ten planted regressions,
  the mutator is not worth its cost.

#### 3.3 Budgets and adaptive depth

- **Why.** Every autonomous step bills, and nothing in the proposal's roadmap stops a runaway.
- **Lands.** Ceilings per session and per day in tokens and dollars, on top of the existing per-run
  ceilings. Spend is derived from committed eval manifests and metrics, so the ledger needs no local
  file. Adaptive depth: floors always; a scoped pre-check on the questions whose retrieval trace
  touches the changed pages plus a random sample; the full eval before a merge.
- **See.** Spend and remaining budget in the status payload, and a hard stop demonstrated.
- **Falsifier.** A scoped verdict that disagrees with the full verdict on more than one candidate in
  twenty, replayed over history.

#### 3.4 Worker seam and the headless-client worker

- **Why.** This is the step that removes the human from ingest.
- **Lands.** A `Worker` protocol in core with no model imports. The first adapter launches a
  non-interactive Claude Code session restricted to the knotica MCP tools — no shell, no web access;
  source text is fetched beforehand by `store_source` — under a spend cap, following the session
  protocol and the vault's own ingest prompt. A scripted fake worker for tests. Level L1: a content
  candidate merges only when every floor passes with margin; otherwise it is escalated. An ADR that
  states autonomous clients are sanctioned workers.
- **See.** One goal on the evidence base closed with zero human touches, and the metric of step 1.1
  dropping.
- **Remote note.** A second adapter for another agent — omp in print mode, for example — is the same
  seam.
- **Falsifier.** Worker candidates that pass the floors but that audits reject more than one time in
  ten.

#### 3.5 Rubric evaluator

- **Why.** Floors cannot judge whether an answer addresses its goal.
- **Lands.** A packaged, hashed rubric with its own fingerprint, separate from the harness
  fingerprint so existing baselines do not re-freeze. A control set of known-good and known-bad
  answers that the evaluator must sort correctly before its verdict counts. Calibration examples
  drawn from the owner's own corrected answers. A diff reviewer that starts advisory.
- **See.** Control-set accuracy, and evaluator agreement with human audits.
- **Falsifier.** A reviewer whose advisory findings do not predict audit disagreements should not be
  promoted to a gate.

#### 3.6 Scale benchmark

- **Why.** Search is a live BM25 pass with no index, and the eval grows with the golden set. Both are
  fine today; neither has a measured limit.
- **Lands.** Synthetic vaults at 100, 1,000 and 10,000 pages; latency of the read tools and of
  search; eval cost growth; and thresholds, recorded in an ADR, at which an index or hybrid retrieval
  is introduced behind the existing `SearchBackend` seam.
- **See.** A measurement report and a go or no-go on a retrieval upgrade.

### Stage 4 — Autonomy

#### 4.1 Autonomy levels and escalation

- **Lands.** A level per topic, L0 to L4 as in the proposal, earned from audit records and never
  assumed. Escalation rules in noise units: a criterion near its floor, judge samples that disagree,
  a retraction or deletion, a contradiction with a high-confidence page, a low-reputability source,
  any instrument change. At L2 a discovered source above the reputability floor is approved without a
  human.
- **See.** The `fill` lane running end to end at L2 on the evidence base.

#### 4.2 Sampled audits and circuit breaker

- **Lands.** A share of autonomous merges sent to the `home` inbox for review, sampled
  deterministically from the commit id so an audit is reproducible. Disagreement lowers the level.
  The rollback-rate metric.
- **See.** Audit rows; a level dropping after planted disagreements.

#### 4.3 Screener-assisted instrument review

- **Lands.** An independent screener pre-sorts golden, canary and trainset candidates; the owner
  approves batches instead of every item (open decision O-4). Auto-accepted trainset rows are tagged
  and never promoted to golden.
- **See.** The human time to grow the instrument falls while its size grows.

### Stage 5 — Memory

#### 5.1 Episodes and capture

- **Lands.** An unscored, append-only, secret-scrubbed `episodes` family: experience as it happened —
  task outcomes, corrections, session summaries. Capture is explicit and batched (open decision
  O-6). The plugin gains a hook that records a session summary. A retention rule: episodes age out
  after they are distilled.
- **See.** The sessions that build this feature land in the evidence base as raw experience.
- **Care.** Episodes can hold private text and are pushed to the vault's remote.

#### 5.2 Profiles and memory tools

- **Lands.** A `Profile` protocol with two implementations, `wiki` and `memory`: inputs, planner
  goals, rubric, dream passes, retention, hot-layer cap. Tools `remember`, `recall` and
  `hot_context`. Grading for the memory profile by replay: build memory from episodes up to a point
  in time and test it on what came after.
- **See.** A memory-profile topic beside the wiki one in the evidence base.

#### 5.3 Dream passes and claim status

- **Lands.** Consolidation passes, each emitting a content candidate through the gate, in this
  order: link repair, driven by lint's unresolved links; verify, which re-grounds claims against
  sources and episodes and records a status and an evidence pointer (open decision O-7); dedupe;
  promote and demote for the hot layer, verified claims only; prune. Guillotine trials for
  retractions. A budgeted scheduler: after sessions and nightly.
- **See.** Dangling links and stale claims fixed overnight as reviewable candidates.
- **Falsifier.** Passes whose candidates the gate refuses more often than it keeps.

#### 5.4 Outer loop

- **Lands.** Reflection on gate failures proposes changes to `ingest.md` and to the topic schema,
  rebuilds on a clone and goes through the gate. L4: every such change needs the owner's approval to
  land. This is the `src/knotica/agent/` package `DESIGN.md` already lists as planned.

### Stage 6 — Remote

Runs after the local stages, except step 6.2, which is a spike worth doing early. The target design
is [§ 11](#11-remote-target).

#### 6.1 Ref-targeted transactions and the version-control port

- **Lands.** The write path restated as "commit these changes onto this ref, given this base
  commit", with the current behavior as its first adapter (the `git` binary, `flock`, a worktree).
  A conformance suite any second adapter must pass. No behavior change.
- **Amends.** The "one git commit per mutating operation, flock-guarded" invariant becomes "one
  commit per mutating operation, serialized" — by `flock` locally, by ref compare-and-swap remotely.

#### 6.2 Remote spike on Wasmer Edge

- **Why.** Several facts about the platform are unknown and decide the design of step 6.3.
- **Lands.** A time-boxed experiment, not a feature: the lean server booted on Python 3.13 on the
  platform; read tools against a snapshot; a commit and a push attempted two ways, through a forge's
  HTTP API and through a pure-Python git; request timeout, memory ceiling and cold start measured.
- **See.** A measurement report and the answer to open decision O-8.
- **Needs.** A Wasmer account and CLI. May run any time after step 1.2.

#### 6.3 Edge version-control adapter

- **Lands.** The second adapter chosen in step 6.2, with compare-and-swap commits and bounded retry;
  a snapshot cache keyed by commit id, which is safe because a commit id is immutable; search through
  the pure-Python path that already exists.

#### 6.4 Stateless runtime state

- **Lands.** Confirmation tokens signed by the server and bound to the principal, the action, the
  vault and its head commit, replacing the nonce files. Ingest sessions held as remote branches
  rather than local worktrees. No use of `.knotica/locks/` on the control plane. Switching the
  default vault disabled in remote mode, where every call names its vault.

#### 6.5 Authentication and authorization

- **Lands.** The server as an OAuth 2.1 resource server: protected-resource metadata, a 401
  challenge that points to it, audience validation, no token passthrough. A second credential type
  for agents without a browser: pre-issued tokens, stored hashed, revocable. Identity mapped to a
  principal, deny by default; grants per vault and capability held in deployment configuration,
  never in the vault. Origin and host checks in place of the wildcard; rate limits per principal.
- **See.** A principal outside the allowlist receives no data; a read-only principal cannot write; an
  agent principal cannot touch the instrument.

#### 6.6 Jobs and the worker plane

- **Lands.** Every `operate` verb becomes request-then-observe in remote mode. Workers that reach the
  vault only through its git remote: the local daemon, and scheduled or dispatched CI runs on the
  vault repository. Budgets per principal.
- **See.** An eval requested by a remote agent, executed by a worker, observed through the status
  payload.

#### 6.7 Deploy and connect agents

- **Lands.** The app configuration, secrets and domain; a runbook; connections from Claude Code, omp
  and Grok Bot; the dashboard on the remote mount.
- **See.** A personal agent that checks the `home` inbox on a schedule and acts on it.

## 11. Remote target

Researched on 2026-11-22. Facts marked *unverified* were not found in documentation and are settled
by step 6.2.

### Four planes

| Plane | Does | Runs on | Requires |
|---|---|---|---|
| Control | MCP tools, authorization, dashboard | Wasmer Edge, or any ASGI host | `mcp` and `pydantic`; no `git` binary; no local state |
| Data | The vaults | A git forge, private repositories | It is the source of truth and, through ref compare-and-swap, the lock |
| Worker | Evals, gate, arena, compile, worker sessions, dream passes | The owner's machine and CI on the vault repository | git, Python, an LLM credential |
| Agents | Reading, writing, operating | Claude, omp, Grok Bot | HTTPS and a bearer credential |

Workers and the control plane never share a filesystem. They meet only in git: candidates are
branches, results are commits. That is already an invariant — loops work on a clone and return
branches — so the remote design extends the current one rather than replacing it.

### What the platform allows

| Fact about Wasmer Edge | Consequence |
|---|---|
| Python apps were announced as beta and no general-availability notice was found; the builder targets Python 3.13 only | The lean path is tested on 3.13 (rule D6) |
| A FastMCP server over stateless Streamable HTTP under uvicorn is an officially exemplified shape | The transport needs no change |
| Native wheels come from a platform index; `pydantic-core` and `jiter` are present; an exact pin with no matching wheel fails the build | Native pins on the lean path are kept loose and checked in the spike |
| There is no official `git` binary, and running child processes has open defects | The control plane cannot shell out to git (steps 6.1, 6.3) |
| `flock` is very likely unavailable | Exclusion is by ref compare-and-swap |
| Instances are ephemeral, scale out and scale to zero; no single-instance cap is documented | No local state on the control plane (rule D1, step 6.4) |
| Volumes are shared read-write, single-region, and documented as unsuited to single-writer use | The vault is not kept on a volume |
| Scheduled jobs need the paid plan and default to a 180-second timeout | Long work runs on the worker plane (step 6.6) |
| Outbound HTTPS works; there is no built-in access control | Authorization is in the app (step 6.5) |
| *Unverified:* request timeout, memory ceiling, Python cold start, whether a pure-Python git runs there | Step 6.2 |

The documentation page first cited for this target (`docs.wasmer.io/runtime/python/`) describes
embedding the runtime inside a Python program. Deploying Python apps is documented under
`docs.wasmer.io/edge`.

**Fallback.** Any container host with a disk runs the same server with the current adapter
unchanged. If step 6.2 shows the platform cannot carry the vault layer, only the host changes.

### Authorization

- **Standard.** The MCP specification's current revision is 2026-07-28; the SDK line knotica uses
  (`mcp` 1.x) implements 2025-11-25. That is the target for now. The 2.x SDK is reported to drop
  the `mcp.server.fastmcp` module the server is built on — knotica already bounds the dependency
  below 2 — so that migration is its own piece of work and is tracked as a risk.
- **Two ways to obtain a credential, one way to present it.** Every request carries
  `Authorization: Bearer`. Interactive users obtain a token through the authorization-code flow with
  PKCE at an external authorization server. Agents without a browser use a pre-issued token.
- **What the authorization server must support.** Client-ID metadata documents, which the
  specification now prefers, and dynamic client registration, which it deprecates but which omp and
  Grok Bot use today. Redirects for Claude's hosted clients, for loopback on any port (Claude Code,
  omp) and for Grok Bot's callback addresses, including its custom-scheme one.
- **Where the allowlist lives.** In two places: at the authorization server, so a stranger gets no
  token; and in the server, as a deny-by-default map from verified identity to principal and grants,
  resolved per call. Token scopes are an upper bound, never the check itself.
- **What an agent can never do.** Hold the `instrument` capability.

### What personal agents need

| Agent | Connects from | Credential | Design consequence |
|---|---|---|---|
| Claude Code | The user's machine | OAuth, or a static header | Either path works |
| Claude hosted clients | Anthropic's cloud | OAuth; static headers are a limited beta | The endpoint must be public |
| omp | The user's machine | OAuth with dynamic registration, or a header read from the environment | A scheduled run uses print mode and a header |
| Grok Bot | Its vendor's cloud, address ranges unpublished | OAuth with dynamic registration, a static client id and secret, or headers | The endpoint must be public; no reliance on network location; calls return within two minutes; routines can poll the `home` inbox |

Lowest common denominator: one public HTTPS Streamable HTTP endpoint, tools only, bearer
credential, short calls. Rules D5 and D7 exist because of this table.

## 12. Risks

| Risk | Mitigation |
|---|---|
| Cost. A full harness run in the source post cost about twenty times a solo run | Budgets (3.3), adaptive depth, floors before any model call |
| Goodhart. A small golden set is a narrow target | Canary (1.4), goals as coverage only, held-out growth rule (2.1) |
| Judge leniency between models of one family | Control set (3.5), audits (4.2), a different model for the evaluator where possible |
| Silent knowledge loss from pruning | Source-preservation floor (3.1), guillotine trials (5.3) |
| A wrong entry in the hot layer is carried into every session | Verified claims only, hard cap, higher audit rate for promotions |
| Prompt injection through ingested sources | The worker has no shell and no web tool; its writes are confined to a candidate branch |
| Auto-ingested web sources and licensing | Reputability floor, human approval below L2 |
| Episodes holding private text | Unscored, scrubbed, explicit capture, private remote |
| The platform cannot carry the vault layer | Early spike (6.2), container fallback |
| The 1.x SDK line ends before the remote stage ships | Keep the `mcp<2` bound; schedule the migration as its own step when 6.5 starts |
| Unattended prompt mutation with no off switch (td-050) | The switch lands with step 3.2, before the arena is made real |

## 13. Sources

- The proposal: <https://knotica-a-self-improving-memory-core-wmed.pages.grok.bot/>
- Its sources: [The components of agent memory](https://www.anthropic.com/research/components-of-agent-memory) ·
  [Effective harnesses for long-running agents](https://www.anthropic.com/research/effective-harnesses-for-long-running-agents) ·
  [Harness design for long-running application development](https://www.anthropic.com/research/harness-design-long-running-apps) ·
  [When AI builds itself](https://www.anthropic.com/institute/recursive-self-improvement) ·
  [LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
- MCP authorization: <https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization> ·
  <https://claude.com/docs/connectors/building/authentication> · <https://code.claude.com/docs/en/mcp>
- Wasmer Edge: <https://docs.wasmer.io/edge> ·
  <https://docs.wasmer.io/edge/learn/supported-frameworks-and-languages> ·
  <https://docs.wasmer.io/edge/learn/volumes>
- Personal agents: <https://docs.x.ai/grok-bot/computer-and-apps> · <https://omp.sh/docs/mcp>

The full research reports of 2026-11-22, with a quotation for each fact, are ingested into the
evidence base at step 0.2.
