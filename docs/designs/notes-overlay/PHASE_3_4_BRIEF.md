# Notes overlay — brief for Phases 3 and 4

**Status:** Phases 0, 1 and 2 are complete, verified, and merged. Nothing below is started.

This lives in `docs/` rather than `.ai-work/` on purpose: it carries the measurements that gate
billed experiments, and `.ai-work/` is gitignored and worktree-local, so the previous phase's
handoff would not have survived a fresh checkout.

---

## Read this first, in this order

1. `docs/designs/notes-overlay/SYSTEMS_PLAN.md` — § Sequencing is authoritative for what Phases 3
   and 4 contain. § Resolution ladder and § Acceptance Criteria are the behavioural spec.
   **AC-07's wording was corrected on 2026-07-31** and carries a dated note saying why.
2. `.ai-state/decisions/062-notes-anchor-recovery-window-geometry.md` — **the single most
   load-bearing document for Phase 3.** It supersedes an earlier draft whose conclusion was
   refuted, and it carries the measurements that decide whether the Phase 3 spikes are worth
   running at all.
3. `.ai-state/decisions/058-notes-anchor-model.md` — § Disconfirmation names two falsifiers and two
   reversal triggers. **Check them against § "What changed" below before spending anything.**
4. `.ai-state/decisions/` — `dec-056`, `057`, `059`, `060`, `061`. `dec-058` and `dec-060` carry
   dated amendment notes; read those, not only the original text.
5. `.ai-state/TECH_DEBT_LEDGER.md` — `td-024` … `td-027` are the notes overlay's open rows.

---

## What changed at the end of Phase 2, and why it reshapes Phase 3

Phase 2's own retrospective reported that the `fuzzy` rung — the auto-placing rung the whole
recovery ladder exists to reach — was **unreachable** at `guess_threshold = 0.75`, with a ceiling
of ~0.64 across the edit-class matrix, and recommended lowering the threshold to 0.55–0.60.

**Verification refuted the premise.** `fuzzy` fires. What bounded it was **candidate-window
geometry**, not calibration: windows were extended to sentence bounds and capped at 2× the quote,
so a sub-clause was compared against a window roughly twice its length and `SequenceMatcher`'s
ratio was structurally capped near 0.667 *regardless of edit size*; a multi-sentence quote could
not be matched at any threshold because no window ever spanned two sentences.

That was fixed — sub-span alignment plus multi-sentence widening — with the thresholds left alone.
**Measured at a one-character edit, `guess_threshold` unchanged at 0.75:**

| anchored quote shape | before | after |
|---|---|---|
| whole sentence | `fuzzy` 0.996 | `fuzzy` 0.996 |
| sub-clause of a sentence | `orphaned` 0.639 | **`fuzzy` 0.994** |
| two sentences | `orphaned` 0.726 | **`fuzzy` 0.997** |
| three sentences | `orphaned` 0.648 | **`fuzzy` 0.998** |

**The band sharpened rather than widened** — unrelated text *fell* everywhere (whole sentence
0.641 → 0.265, two sentences 0.429 → 0.217, three 0.446 → 0.219). One regression, deliberate and
recorded: a whole-sentence *heavy paraphrase* moved from `fuzzy` 0.788 to `orphaned` 0.675, so
that class is now human-reviewed rather than auto-placed.

### The consequence for Phase 3, stated plainly

Block IDs (Spikes 3a/3b) exist for exactly one purpose: **to raise the automatic re-anchor rate.**
That rate has just been raised substantially, offline, for free, without inverting the feature's
founding coupling — the whole reason `dec-058` declined block IDs is that injecting `^id` tokens
means the unscored personal layer writes into the scored corpus.

The previous handoff also recommended deferring 3a/3b, but for a reason that was wrong: it argued a
*config change* might deliver what the spikes were meant to buy. It would not have — sub-span and
multi-sentence quotes were unreachable at any threshold. The conclusion survives; the reasoning
does not. Do not carry the old reasoning forward.

---

## Phase 3 — what to do, in order

### Step 1 (free, do this first) — re-measure the residual orphan rate

**This is the gate for everything else in Phase 3, and it costs nothing.**

`dec-058` accepted a residual hard-orphan rate of **8–20%** (versus block IDs' 2–6%) as the price
of the bi-partite model. That number was an estimate imported from prior-art research, and it has
never been measured on this corpus — and the geometry fix has certainly moved it.

Measure it on a **seeded, isolated vault** (never the live one): a realistic page set, a realistic
spread of quote shapes, and the loop's actual rewrite behaviour. Report the distribution of
`exact` / `shifted` / `fuzzy` / `orphaned@section` / `orphaned@page`, and specifically the fraction
of rewrites that produce a review-queue item.

Then check `dec-058`'s recorded triggers against it:

- **Falsifier 1** — block-ID preservation ≥95% *and* an A/B eval showing no leg degrading. Still
  requires 3a and 3b to answer. Only worth running if Step 1 shows the residual rate is still the
  binding constraint on review burden.
- **Reversal trigger (b)** — *"the fuzzy-band review queue exceeds roughly one item per rewrite
  event per active topic."* Before the geometry fix this was arguably **already met**, which is the
  25-year failure mode `dec-058` bet against. Step 1 tells you whether it still is. If it is not,
  3a/3b have lost their justification and should be closed as "not needed", not left open.

**Do not run Spike 3a or 3b before Step 1.** They are the only billed items in this phase.

### Step 2 — the two Phase 3 priorities that inverted

Both of these reverse what earlier planning assumed. The reasons are in the measurement above.

**LLM adjudication of the middle band is now *less* relevant, not more.** The previous handoff
argued it had become "far more relevant than planned" because *every* measured non-verbatim edit
landed in the `[0.35, 0.75)` band. After the geometry fix, small and moderate edits land at 0.99 —
above the band entirely. The band is now **sparser** than originally scoped. Re-scope it against a
fresh measurement before building it; it may no longer earn a config key, let alone an LLM call.

This leaves **AC-10 stranded** and it needs a decision either way: it is a Phase 2 exit criterion
whose *discriminating* clause ("only optional adjudicator commentary is absent") depends on an
adjudicator that does not exist, so it cannot currently fail. Its load-bearing clause — identical
offline statuses — is true and was verified more strongly than the criterion asks (the resolution
path's transitive import closure is stdlib-only). Either reword AC-10 or re-assign it to Phase 3.

**Spike 2 (`search_result` citations) is now *more* relevant.** Phase 2 established that quote
shape dominates recovery, and the capture guidance can only *ask* a client for a good quote.
Native citations would let the client pass a precise, verbatim span it did not hand-copy — which
is upstream of the entire problem. It is also the one Phase 3 item that is not billed; it is
blocked on vendor capability, not spend. Settle it early: it may reduce the value of everything
else in this phase.

### Step 3 — the remaining Phase 3 items, unchanged

- **Notes full-text search** behind an explicit `families=` selector defaulting to
  `SCORED_FAMILIES`. The constraint is fixed by `dec-060`: opt-in inclusion, never opt-out
  exclusion, so the eval runner and query engine can never see notes by taking a default.

---

## Phase 4 — deferred, and one item is now better justified than the other two

- **Adaptive threshold tuning from accepted guesses.** Was the direct answer to what looked like a
  calibration problem. It is not one — the problem was geometry, and it is fixed. Re-derive the
  case for this from Step 1's measurement rather than from the earlier framing.
- **A persisted projection index under `.knotica/`.** This now rests on **one** justification, not
  two: `read_note` was made O(1) in Phase 2, so only the drift queue remains, and it resolves every
  anchor in a topic on every open *by design*. Judge it on that alone, against real note counts.
  A dot-prefixed path is already invisible to `iter_page_paths` and to the loop watch, so the seam
  is designed; it is the cost that is unproven.
- **Golden-set promotion** via a staging writer behind the existing `freeze()` gate. **This is a
  one-way door** — `freeze()` enforces trainset/golden disjointness, so a question routed to
  `qa.jsonl` can never later enter `golden.jsonl`. `dec-059` defers it on two independent grounds
  (D-MERIT bias in a sparse, self-selected annotation sample; and the mutual exclusivity itself).
  If it is revisited, it needs a `systems-architect` at opus, not an implementer.

---

## Open debt and unfixed findings you will meet

Everything below is real, recorded, and deliberately left. None of it blocks Phase 3.

| id | what | note |
|---|---|---|
| `td-024` | `promoted:` frontmatter absent, so **trainset** promotions have no note-side traceability | Asymmetric: the gap path keeps it via `reported_reason`. Fix options are ranked in the row. |
| `td-025` | the candidate window's *forward* extension has the unbounded hole the backward side had | Its stated trigger understates the case: the row assumes quotes end in terminal punctuation, but the design's own worked-example quote is an unpunctuated sub-clause. |
| `td-026` | the file-size ratchet scans Python only, so the TypeScript dashboard silently exceeds the 800-line ceiling | `types.ts` is 1093 lines. Two fixes offered; splitting by domain is the better one. |
| `td-027` | `reanchor`/`detach` rewrite **hand-authored** anchor formatting, because both reserialize the whole document | Live path — hand-authoring is a first-class capture surface. Pinned by a characterization test written to **start failing when fixed**. |

**Verification findings left unfixed** (full table with costs in the Phase 2 verification report):

- `_NOTES_DIRECTORY_TEMPLATE = "notes/{topic}"` is declared **four times** while `NOTES_DIR` in
  `core/vault_layout.py` has **zero** consumers — the exact duplication `dec-060` exists to retire,
  re-created for the new family. Recorded as an amendment on `dec-060`.
- A contamination assertion in `tests/core/notes/test_contamination.py` is a **tautology**: it
  checks for a `.md`-suffixed literal, and wikilinks always strip `.md`, so it can never fail.
  Note identity does reach `log.md` as `[[notes/<topic>/<note_id>]]`; lint is immunised against it,
  but the guard proving so is vacuous.
- Another assertion in the same file passes **by truncation** — its fixture is 79 characters
  against a 72-character title cap, so the phrase it looks for is never written. A 61-character
  body does leak verbatim.
- `.ai-work/notes-overlay-phase2/traceability.yml` is half-populated. The tests exist; the
  bookkeeping does not. It renders into the archived SPEC's matrix, so `/sdd-coverage` reports a
  coverage hole that is really a records hole. **Unrecoverable once `.ai-work/` is cleaned.**
- The dashboard's anchor-status filter chips account for 5 of 6 notes — `anchor-invalid` renders in
  the list but has no chip.
- `dashboard/src/App.tsx:56` hardcodes the MCP endpoint to `http://127.0.0.1:8765/mcp` rather than
  the page's own origin, so any dashboard served on another port reads data from whatever is on
  8765. This silently cross-wires two vaults on one machine and was why the Phase 2 render gate
  went unsatisfied for a full session.

---

## Traps — every one of these drew blood in Phase 2

- **The rulings supersede the plan's step bodies, and the bodies were never struck through.**
  `.ai-work/<slug>/IMPLEMENTATION_PLAN.md` carries ~25 mid-flight rulings; where a step body and a
  ruling disagree, the ruling wins. This caught three separate people in Phase 2 — including the
  orchestrator, who copied a stale assertion out of a step body into an agent brief and was
  corrected by the agent.
- **Verify agent reports; do not relay them.** Three overstated claims were caught by direct check
  in one session, all in the same direction. One reported finding did not reproduce at all. A
  verification that only propagates and never retracts is not independent.
- **Agent finales truncate on heavyweight steps — routinely.** Re-derive from ground truth
  (`git status`, a scoped pytest, whether the artifact exists) and resume with `SendMessage`; do
  not respawn. In Phase 2 one agent had finished the work it was narrating and another was
  mid-edit; only the test run distinguished them.
- **Subagents get read-only git. No `stash` / `checkout` / `restore` / `reset` / `clean`, ever** —
  not even to inspect a baseline. Say the prohibition *and* the alternative ("run the tests as they
  are and read the counts"; "use `git show <sha>:<path>`").
- **At most 2–3 agents concurrently, on genuinely disjoint file sets.** Never two on one file, and
  never a full-suite run concurrently with anything — parallel pytest in one worktree causes
  transient `evals/*` import-purity failures.
- **Read the id-discipline gate's exit code directly, never through a pipe:**
  `uv run python scripts/check_id_citation_discipline.py; echo $?`. `AC-NN` and `REQ-NN` in a
  docstring are violations; this fired on a test docstring during Phase 2's own fix pass.
- **Never read or write the live vault at `~/dev/data/knotica`.** A loop service watches it and a
  content commit there costs billed eval spend. Use `template_vault` / `isolated_home` /
  `vault_config`.
- **Rendering the dashboard: use a canary topic.** Seed the fixture vault with a topic name that
  *cannot* exist in the live vault, then verify over `/mcp` **and** in the rendered page before
  trusting anything. `curl`-verifying the server is not sufficient — see the hardcoded-port finding
  above. And pass `?mcp=http://127.0.0.1:<port>/mcp` explicitly until that is fixed.
- **Full coverage needs the evals extra:** `uv sync --frozen --group dev --extra evals`, else ~107
  dspy/anthropic tests silently skip.
- **File-size ceiling 800, hard, exemption list closed.** Check headroom before adding.
- Always `uv run --frozen`; a bare `uv run` can rewrite `uv.lock`.

---

## Where things stand

- **Suite:** 2373 passed, 0 failed, 0 skipped. `ruff` 362 files, `mypy` 186 files, `tsc` clean,
  id-discipline gate exit 0.
- **Decisions:** `dec-056` … `dec-062` all finalized and indexed; `.ai-state/decisions/drafts/` is
  empty but for its tracked `.finalize.lock`.
- **`core/loop.py` is byte-identical to `main`** across all three phases and sits at its file-size
  ratchet baseline with zero slack. The eval instrument's `harness_version` has not moved.
- **`.ai-state/observations.jsonl` now has a `union` merge driver** (`.gitattributes`), so the
  conflict that recurred on every worktree merge resolves itself.

## Recommended shape for the next session

| Work | Agent | Model | Effort |
|---|---|---|---|
| Step 1 re-measurement (design + interpretation) | `i-am:researcher` | opus | high |
| Spike 2 vendor-capability check | `i-am:researcher` | sonnet | medium |
| Any change to `resolve.py` / `scoring.py` / `candidates.py` | `i-am:implementer` | **opus** | high |
| Golden promotion, if revisited | `i-am:systems-architect` | opus | high |
| Orchestration | main session | Opus 5 | xhigh |

Phase 3 is **spike-gated by charter**: nothing in it is a feature, and every item should be able to
answer "what measurement would make this unnecessary?" before it is built. Step 1 answers that
question for the two most expensive items. Francisco runs all billed commands himself.
