# KTK — where the process-swimlanes work stands, and how to resume it

**Written 2026-08-10.** Read this first if you are picking the swimlanes redesign back up. It is a
handoff, not a plan: the plan is 1 400 lines and lives elsewhere, and this tells you where.

Delete this file when M1 ships.

---

## 1. What we are trying to do

Knotica's surfaces are shaped like **tools**, not like **processes**. The dashboard has tabs called
Vault, Ask, Loop, Sources, Notes, Arena, Ingest, Datasets; the MCP server has 35 registrations; the
CLI has 15 subcommands. A user who wants to answer *"my wiki was wrong about X — fix it"* has to know
which four of those to visit and in what order.

The redesign replaces that with **six process lanes** — **Home, Learn, Answer, Improve, Fill, Tend** —
declared once in code and *projected* onto every entry point, so one process is followable end to end
in one place. Shared objects are projected per-lane, never duplicated; a lane may not terminate in
another lane.

Four things about the shape, each decided and recorded:

- **A lane is a facet, not a partition** (`dec-088`). A flat lane-prefixed rename was falsified:
  eleven verbs span multiple lanes, and `notes action=promote` routes into four of them on its
  `target` argument alone. There is no owning-lane partition to be had.
- **The MCP surface becomes tiered** (`dec-094`): a flat conversational core that keeps its names,
  plus six lane dispatchers whose action tables are generated from one `LANE_MEMBERSHIP` declaration.
  35 registrations collapse to ~21.
- **The CLI nests two levels** (`dec-095`): `improve loop`, `fill discover`, `tend doctor`. Old names
  survive as hidden shim parsers.
- **Slash commands do NOT move** (`dec-099`). `commands/` stays flat and the lane goes in each
  command's picker description — because a directory-derived rename has no deprecation path and a
  shipped hook prints two of the names.

`Fill` is called Fill and not Repair on purpose: `repair` is already a published action belonging to
**Tend**, and shipping both would put one word in two lanes in the surface a model routes on.

## 2. Where everything lives — read this before looking for anything

> [!IMPORTANT]
> **The pipeline state for this task is NOT in `main`.** `.ai-work/` is gitignored, and `main`'s copy
> holds ten *other* task directories but not `process-swimlanes`. Everything below is in the worktree.

```
/Users/fperez/dev/knotica/.claude/worktrees/process-swimlanes/
└── .ai-work/process-swimlanes/
    ├── IMPLEMENTATION_PLAN.md        ← the 45-step plan. Steps 22-42 are M1.
    ├── WIP.md                        ← step-by-step status; read the top first
    ├── LEARNINGS.md                  ← every gotcha this pipeline paid for
    ├── TELEMETRY_BASELINE_PROTOCOL.md ← the blocking step's runbook
    ├── SYSTEMS_PLAN.md · INTERFACE_DESIGN.md · CONTEXT_REVIEW.md
    └── PROGRESS.md · TEST_RESULTS.md · traceability.yml
```

The branch `worktree-process-swimlanes` is **fully merged into `main`** (0 commits ahead). The
worktree still exists because M1 continues there. The **code** is all on `main`.

Decisions are in `main` at `.ai-state/decisions/` — this pipeline added **dec-087 … dec-099**.

## 3. What is done

**M0 is code-complete.** Steps 1–19 and 45; Step 20 is running; Step 21 waits on it.

| | |
|---|---|
| Dashboard | vitest runner + CI; `?pane=` routing extracted to a pure `resolvePane` |
| Two-phase billing | `confirm`/`confirm_nonce` threaded through `loopRunOnce`; the second gate front door deleted; the logic extracted into one `TwoPhaseAction` primitive |
| Gap lifecycle | a merged gate verdict closes the gap its candidate was found for, in **one** transaction — atomicity asserted from both directions |
| **Telemetry** | **9/35 → 35/35 registrations**, recorded *after* the handler so the outcome is the real error code |
| Gates added to `make verify` | **surface consistency** — the reference doc vs the registry, *and* (as its third check) referential integrity: every published tool name resolved against what is registered. The chain is now eight steps |

`make verify` on `main`: **exit 0, 2935 passed.**

**Telemetry is the load-bearing one.** It did not land as the ~30-module sweep the plan costed.
`RecordingServer` subclasses `FastMCP` and overrides `call_tool` — the one method every tool call
passes through — so coverage is a property of the server, not a convention thirty modules must
remember, and a new tool cannot be registered without being measured. It **must** be a subclass:
`FastMCP.__init__` binds `self.call_tool` into the low-level handler, so a post-construction
monkeypatch is never reached. Measured — a patched attribute intercepted **zero** client calls.

**Three live defects were found by the new gates and fixed:** a `fix=` string telling users to run
`golden action=freeze` (an action `golden` does not have); a failure with no payload being recorded
as `ok`; and a torn sink line aborting an entire baseline read.

## 4. The one thing blocking everything

**Step 20 — the pre-rename telemetry baseline window.** It is a **hard gate on M1**: the rename
rewrites the whole tool surface at once, so this is the only "before" that will ever exist.

Four of five M1 entry conditions are already met (Steps 13, 16, 18 and the Q5 mapping). Only the
window remains.

```bash
uv run python scripts/summarize_telemetry.py ~/knotica-telemetry/pre-rename
```

**Closed when all three floors clear: 200 dispatch records, 5 sessions, 3 days.** Below any of them
the tool withholds a verdict and says which axis is short.

Both clients are already wired — the sink path is in Claude Desktop's `mcpServers.knotica.env` and in
`~/.claude.json`'s local-scope knotica registration (`.bak` beside each). Nothing else is needed;
just use knotica normally. **Do not pad it with synthetic calls** — the distribution is the point, and
a synthetic "before" compared against a real "after" measures the wrong thing entirely.

> [!WARNING]
> **The window is a WALL-CLOCK gate.** "Step 20 started" does not satisfy the M1 entry gate; "the
> window closed with all three floors met" does. Steps 31 and 38 must not run hours after it opens.

**If the floors prove unreachable** at your real usage rate, the right move is to lower them and state
plainly what the smaller sample can bound — not to pad, and not to stall M1 forever. The thresholds
are constants in `scripts/summarize_telemetry.py` with their derivations, and 20 tests cover them;
change the constants and the tests together.

## 5. What happens after the window closes

Step 21 (M0 integration checkpoint), then M1 — Steps 22–42, shaped **add-then-remove** (`dec-098`) so
no intermediate commit ever holds a half-renamed surface:

1. **Declare** — `core/process_model.py` as the single source of the lane model (23, `tier: H`),
   stage predicates, served live on `wiki_status`.
2. **Add** — register the six lane dispatchers *additively* from `LANE_MEMBERSHIP`, then prove payload
   equivalence action-by-action against the flat tools they replace (29–30, `tier: H`).
3. **Remove** — delete the ~21 operator flat tools as a pure deletion (31, **one-way door**).
4. **Reshape prose** — collapse the description corpus into six action tables (38, the plan's **one
   non-revertible step**).
5. **Project** — CLI nesting with hidden shims (43), `--help` chunking (39), `docs/reference.md` (40),
   slash-command descriptions (37 — now cheap, see `dec-099`).
6. **Ship** — one `feat!:` commit with a `BREAKING CHANGE:` footer carrying the full mapping table.

Steps 23, 29, 31, 38 and 43 carry `tier: H` or `review: force`, so each fires an intra-step review.

## 6. Traps — each of these cost real time; do not rediscover them

- **ADR fragment filenames need `-HHMM`.** The schema is `<YYYYMMDD-HHMM>-<user>-<branch>-<slug>.md`.
  Four drafts written without the time were silently skipped by finalize, which reported "nothing to
  do" and exited 0.
- **Drafts are exempt from checks that finalized records must pass.** `re_affirmed_by` reciprocity and
  `affected_files` resolution are enforced only after promotion — so a bad draft passes *every*
  pre-merge gate and fails on `main` seconds later. Check both before merging.
- **Ten tool names are ordinary English** (`vault`, `loop`, `golden`, `arena`, `notes`, `query`,
  `search`, `compile`, `branches`, `datasets`) **and so is the project name.** Any gate that matches
  names in prose must require **code position** — a backtick span, or bare text in a shell script.
  Ignoring this produced 17 and then 18 false findings in two separate gates.
- **The pre-commit id-citation gate is dead inside worktrees** (`td-054`): the plugin's checker
  excludes `/.claude/worktrees/` by matching the *absolute* path, so a worktree's own run drops every
  file. Run it by hand before committing there:
  `uv run python scripts/check_id_citation_discipline.py --files $(git diff --cached --name-only)`
- **Never export `KNOTICA_TELEMETRY_DIR` globally.** Measured: two dispatcher test files alone wrote
  records into an ambient sink; across 2 935 tests every `make verify` would poison the window.
- **Measure a step's premise before executing it.** Step 17's Done-when was a metric that turned out
  to be measuring the wrong thing; the work it demanded was already done and doing it would have
  damaged the docs.
- **`tests/test_hooks_session_start.py` fails on a cold tree**, deterministically, not just under
  load (`td-055`). It is green in CI only because CI is Linux. Not a regression.

## 7. Debts and open questions

- **The `tier: H` intra-step reviews were self-reviews, not independent ones.** This pipeline ran with
  no subagents. Batch F's was run adversarially and found two real defects, but an independent reader
  never saw any of this work. M1's five RISKY steps deserve better.
- **`dec-099` (slash commands stay flat) amends a decision `@fperezsorrosal` locked.** It is now
  finalized, so overriding it means a supersession rather than a draft edit. Its reversal trigger is
  written into the record: a live `/` picker showing a subdirectory command invoking unchanged.
- **Step 15's host axis is unobserved.** The SDK question is settled (the pinned `ext-apps` 1.7.4
  declares `message`, `openLinks`, `updateModelContext`, `sampling`, so RISK-07 is falsified), but
  what a *host* advertises at runtime needs Claude Desktop. Verdict recorded as **progressive
  enhancement only**, which the fallback already assumed — nothing is blocked.
- **`main` is 1 commit ahead of `origin`** and there is no open PR; `CONTRIBUTING.md` prefers one.

---

## 8. The resume prompt

Paste this into a fresh session started in `/Users/fperez/dev/knotica`:

```text
Resume the process-swimlanes redesign. Read KTK_NEXT_STEPS.md at the repo root first
for context — it explains the goal, what is done, and where the pipeline state lives.

The pipeline documents are NOT in this checkout. They are in the worktree:
  .claude/worktrees/process-swimlanes/.ai-work/process-swimlanes/
Read WIP.md (top first), then IMPLEMENTATION_PLAN.md for the step you are on, and
skim LEARNINGS.md — it records every gotcha this pipeline already paid for.

FIRST, check the one thing that gates everything:

    uv run python scripts/summarize_telemetry.py ~/knotica-telemetry/pre-rename

- If it reports "BELOW THE COMPARABILITY FLOOR", Step 20's window is still open and
  ALL of M1 is blocked. Do not start M1. Report the shortfall and stop. If the floors
  look unreachable at the real usage rate, propose recalibrating them (constants plus
  their tests live together in scripts/summarize_telemetry.py) rather than padding the
  window or stalling.
- If all three floors are met, the window is closed. Record the report in
  LEARNINGS.md under "## Step 20 — pre-rename baseline", then proceed to Step 21
  (M0 integration checkpoint) and then M1, which begins at Step 22.

M1 is add-then-remove (dec-098): declare the process model, register the six lane
dispatchers ADDITIVELY, prove payload equivalence per action, and only then remove
the flat tools. Steps 23, 29, 31, 38 and 43 are tier:H or review:force and each
fires an intra-step review. Step 31 is a one-way door and Step 38 is the one
non-revertible step in the plan — do not run either until the window has CLOSED, and
not hours after it opened; it is a wall-clock gate, not a step dependency.

Work in the worktree, not in main. Run `make verify` before every commit — it is now
an eight-step chain; surface-consistency is one step and carries the
referential-integrity check inside it. Inside a
worktree the pre-commit id-citation hook is dead (td-054), so run it by hand:
  uv run python scripts/check_id_citation_discipline.py --files <changed files>

Ask me before merging to main or pushing.
```
