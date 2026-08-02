# Notes overlay — brief for Phase 4

**Status:** Phases 0–3 are complete, verified, and merged. Nothing below is started.

This lives in `docs/` rather than `.ai-work/` for the same reason its predecessor did: it carries
the measurements that gate the work, and `.ai-work/` is gitignored and worktree-local, so the
handoff would not survive a fresh checkout.

---

## Read this first, in this order

1. `docs/designs/notes-overlay/STEP1_ORPHAN_RATE.md` — **the most load-bearing document for
   Phase 4.** Its § "Measured on the real vault" supersedes the synthetic estimates above it in
   the same file, and its two recorded corrections explain why a naive reading of the instruments
   is wrong.
2. `.ai-state/decisions/<NNN>-close-block-id-spikes.md` — closes Spikes 3a/3b/2 and re-affirms
   `dec-058`. Its § Disconfirmation names the evidence a reversal needs; its § Consequences names
   the one thing Phase 3 surfaced and did **not** decide.
3. `.ai-state/decisions/058-notes-anchor-model.md` — § Disconfirmation. **Falsifier 1 is now
   closed; falsifier 2 is still open and is Phase 4's actual gate.** Read both.
4. `.ai-state/decisions/059-notes-eval-bridge.md` — the golden-promotion deferral, with a
   steelmanned runner-up that is unusually strong. Read § Falsifier before assuming the deferral
   holds.
5. `docs/designs/notes-overlay/SPIKE_2_CITATIONS.md` — why the citation path is closed on the MCP
   specification, so nobody re-opens it as "blocked on vendor".
6. `.ai-state/TECH_DEBT_LEDGER.md` — `td-024` … `td-027`, plus the two rows this phase should file
   (below).

---

## What Phase 3 established, and how it reshapes Phase 4

Phase 3 spent **zero billed budget** and closed three of its four items on evidence rather than
argument. The measured facts that matter here:

- **Ordinary knowledge rewrites orphan 7.7% of anchors** — below the 8–20% band `dec-058`
  accepted. Reversal trigger (b) is not met.
- **85% of observed orphaning came from one wholesale supersession**, which no anchoring mechanism
  survives.
- **Both bulk migrations are benign** (0.0% and 0.5%), including the hard-wrap reflow that should
  have been the worst case for verbatim quote matching.
- **Quote shape no longer decides recoverability** — flat across shapes on KB pages, so
  `dec-062`'s geometry fix did what it claimed.

### The consequence, item by item

**Adaptive threshold tuning has lost its motivation entirely — do not build it.** It existed to
answer what looked like a calibration problem. `dec-062` established the problem was *geometry*,
and Phase 3 measured the result: thresholds are not the binding constraint on anything. Ask the
charter question — *what measurement would make this unnecessary?* — and the answer is: the one
already in `STEP1_ORPHAN_RATE.md`. Close it, with an ADR, rather than carrying it as deferred.

**The persisted projection index is the one item with a live, unanswered gate.** `dec-058`'s
falsifier 2 — *"read-time resolution measured on a realistic vault costs enough to be
user-visible"* — is the only falsifier Phase 3 left open, and it is exactly this item's
justification. It is also **free to measure**. Do that before designing anything.

**Golden-set promotion is the only item that is genuinely a design question**, and it is a
one-way door: `freeze()`'s `verify_disjoint_from_trainset` makes trainset and golden mutually
exclusive per question, so every question already routed to `qa.jsonl` is permanently ineligible.
`dec-059` defers it on two independent grounds; its steelmanned runner-up argues the deferral
routes the system's only *real human questions* away from the set that decides whether the KB is
improving, leaving a closed loop grading itself. That tension is unresolved and is not an
implementer's call.

---

## Phase 4 — what to do, in order

### Step 1 (free, do this first) — measure read-time resolution cost

**This is the gate for the persisted index, and it costs nothing.**

`dec-058` accepted per-read resolution on the premise that it is cheap, and named the measurement
that would refute it. Phase 2 made `read_note` O(1), so the remaining exposure is **the drift
queue**, which resolves every anchor in a topic on every open by design.

Measure it on a seeded, isolated vault — never the live one. Report resolution wall-clock as a
function of note count and anchors-per-note, and specifically the drift-queue open at realistic
and pessimistic note densities. Then answer: at what note count does a drift-queue open cross a
user-visible threshold, and is that count reachable this decade?

The seam is already designed — a dot-prefixed path under `.knotica/` is invisible to
`iter_page_paths` and to the loop watch. **It is the cost that is unproven, not the design.** If
the measurement shows the drift queue stays comfortably fast at plausible densities, close the
index the same way Phase 3 closed the spikes: as "not needed", with the measurement recorded.

Do not design an index before this measurement exists.

### Step 2 — close adaptive threshold tuning

Write the ADR. It supersedes nothing (it was never its own decision) and re-affirms `dec-062`;
follow the same shape the Phase 3 closure used, including a Disconfirmation that names what would
bring it back. Do not spend implementation effort here.

### Step 3 — the golden-promotion decision (systems-architect, opus)

If revisited at all, this needs a `systems-architect` at opus — not an implementer — because the
question is whether `dec-059`'s two grounds still hold, not how to write a staging writer. The
mechanism is already scoped and small (`golden.staging.jsonl` reusing `_write_staging`'s shape,
behind the existing `golden_review` + `freeze()` human gate). **The mechanism is not the hard
part; the one-way door is.**

Required inputs before deciding: how many note-derived questions exist, what fraction are
`dispute`/`gap`/`question` intent, and how many have already been routed to the trainset and are
therefore permanently ineligible. That last number is the cost of continued deferral, and nobody
has measured it.

### Step 4 — the item Phase 3 surfaced and did not decide

**Supersession is an unmodelled event class.** A page replaced wholesale orphans every anchor into
it — correctly — but the review surface cannot distinguish "your passage was reworded" from "this
page was replaced". The anchor of record already carries everything a pointer to the replacing
page would need. It is cheap, unbuilt, and on the measured evidence worth more than either
remaining Phase 4 item. Scope it explicitly rather than letting it live only in an ADR's
Consequences section.

---

## Debt to file (not yet in the ledger)

- **`tests/test_search.py` exceeds 1000 lines** against the 800-line ceiling. The ratchet
  (`tests/test_file_size_ratchet.py`) scans `src/knotica` only, so no gate sees it. Same blind spot
  as `td-026` (the TypeScript dashboard), different direction.
- **No git `post-merge` hook exists in this repo**, so the ADR finalize protocol does not run
  automatically. Drafts are promoted by invoking `praxion/scripts/finalize_adrs.py` manually at
  merge-to-main. Every phase so far has depended on someone remembering.

---

## Traps — every one of these cost real time in Phase 3

- **`EnterWorktree` branches from `origin/main`, which is far behind local `main`.** A fresh
  worktree came up ~65 commits stale, with no `core/notes/` at all. **Check
  `git log --oneline HEAD..main` immediately after entering a worktree and reset onto local `main`
  if it is non-empty.** Local `main` has never been pushed.
- **Never read or write the live vault at `~/dev/data/knotica`.** A loop service watches it and a
  content commit costs billed eval spend. `scripts/measure_rewrite_severity.py` and
  `measure_real_rewrites.py` refuse any vault named in `~/.config/knotica/config.toml` *before*
  stat-ing it; clone with `git clone --no-hardlinks` and point them at the clone.
- **The full suite silently under-runs without the evals extra.** Without
  `uv sync --frozen --group dev --extra evals` you get **2276 passed / 17 skipped**; with it,
  **2392 passed / 0 skipped**. A green run with skips is not a green run.
- **Never run pytest concurrently with anything** in one worktree — parallel runs cause transient
  `evals/*` import-purity failures. The full suite takes ~10 minutes; plan around it.
- **`pgrep -f pytest` returns false negatives here.** Poll the output file for
  `[0-9]+ (passed|failed)`, or use `ps aux | grep '[p]ytest'`.
- **Verify external specifications firsthand, and check the revision is current.** The first MCP
  spec page fetched in Phase 3 was `2025-06-18`; the current revision is `2026-07-28`. A year-stale
  spec would have produced a confidently wrong answer.
- **Do not relay a search summary as a finding.** Phase 3's decisive evidence came from opening the
  cited issue, not from the snippet describing it.
- **Read the id-discipline gate's exit code directly:**
  `uv run --frozen python scripts/check_id_citation_discipline.py; echo $?`. `AC-NN` / `REQ-NN` in
  a docstring are violations.
- **The search `Cursor` type is shared by four surfaces** — search, notes read, drift queue,
  suggestions. Changing it touches all of them; new fields must be defaulted.
- **BM25 corpus statistics must be computed over the same family set being matched.** Counting one
  population and ranking another silently perturbs every score.
- **`families` is an opt-in allowlist, never an exclusion flag** (`dec-060`). The eval runner and
  query engine call `search()` without naming families, so the safe corpus must be what a caller
  gets for saying nothing.
- **File-size ceiling 800, hard.** The ratchet scans `src/knotica` only — `tests/` and
  `dashboard/` are invisible to it. Check headroom before adding.
- Always `uv run --frozen`; a bare `uv run` can rewrite `uv.lock`.

---

## Where things stand

- **Suite:** 2392 passed, 0 failed, 0 skipped (with the evals extra). `ruff` and `mypy` clean over
  186 source files; id-discipline gate exit 0.
- **Decisions:** `dec-056` … `dec-062` finalized, plus Phase 3's closure ADR.
- **Instruments, committed and reusable:** `scripts/measure_orphan_rate.py` (synthetic, with
  `--replicates`), `scripts/measure_rewrite_severity.py` (git-history severity), and
  `scripts/measure_real_rewrites.py` (real anchors, real rewrites, shipped resolver). Prefer the
  third — it needs no curve and no synthetic perturbation.
- **`core/loop.py` remains byte-identical to `main`** across all four phases.

## Recommended shape for the next session

| Work | Agent | Model | Effort |
|---|---|---|---|
| Step 1 read-latency measurement (design + interpretation) | `i-am:researcher` | opus | high |
| Golden-promotion decision, if revisited | `i-am:systems-architect` | **opus** | high |
| Supersession review affordance | `i-am:implementer` | sonnet | medium |
| Any change to `resolve.py` / `scoring.py` / `candidates.py` / `cursor.py` | `i-am:implementer` | **opus** | high |
| Orchestration | main session | Opus 5 | xhigh |

Phase 4 is **gate-first, like Phase 3**: two of its three original items should probably close on
measurement rather than ship. Every item must answer *"what measurement would make this
unnecessary?"* before it is built. Francisco runs all billed commands himself.
