# Phase 4 Step 1 — read-time resolution cost, measured

**Date:** 2026-08-02. **Cost:** zero — no eval run, no LLM call, no vault touched.
**Instrument:** `scripts/measure_read_latency.py`.

This answers `dec-058`'s **falsifier 2** — *"read-time resolution measured on a realistic vault
costs enough to be user-visible"* — the only falsifier Phase 3 left open, and the entire
justification for a persisted projection index.

Lives in `docs/` for the reason its predecessors do: it gates design work, and `.ai-work/` is
gitignored and worktree-local.

---

## How it was measured

The script **takes no vault argument and never opens an existing vault.** It builds a throwaway
vault under `tempfile.mkdtemp()`, measures, and removes it. Contact with `~/dev/data/knotica` is
structurally impossible rather than guarded against — a stronger guarantee than
`measure_rewrite_severity.py`'s config-file refusal, and the right one here because this script
*creates* vault content where that one only reads history.

- **Pages** — 2KB KB content pages, the middle of the vault template's 1.7–2.5KB range.
- **Anchors** — whole-sentence quotes. Phase 3 measured hard-orphan rate flat across quote shapes
  on KB pages, so the shape axis is retired and one shape suffices.
- **Status mix** — a controlled fraction of anchored passages is rewritten in a third commit, so
  drift-queue membership is set rather than incidental. Default **15%**, matching the 15.3% Phase 3
  measured for ordinary knowledge rewrites. A seed that orphaned everything would overstate the
  drift-queue cost ~6x.
- **Decomposition** — `VaultVcs._run` is wrapped, so every measurement splits into git-subprocess
  time and in-process time (dominated by `difflib.SequenceMatcher` inside `resolve_anchor`).
  Wall-clock alone would not say *what* to fix.

**Three surfaces are timed**, because a "drift-queue open" is not one call:

| surface | what it is |
|---|---|
| `read_note` | the O(1) single-note read — the control |
| `list_notes` | resolves every anchor in the topic once |
| `drift_open` | what `notes read action=drift` actually costs: `list_notes` for the members, then `reconcile_notes` — **which calls `list_notes` a second time internally** — then one historical resolution per queue member |

---

## Result

Realistic mix (15% drift-queue membership):

| notes | anchors/note | anchors | surface | wall | git | cpu | git calls |
|---|---|---|---|---|---|---|---|
| 10 | 1 | 10 | `list_notes` | 2.61s | 2.43s | 0.18s | 20 |
| 10 | 1 | 10 | `drift_open` | **1.47s** | 1.36s | 0.11s | 54 |
| 25 | 1 | 25 | `drift_open` | **2.21s** | 2.09s | 0.11s | 128 |
| 50 | 1 | 50 | `drift_open` | **6.69s** | 6.42s | 0.27s | 256 |
| 100 | 1 | 100 | `drift_open` | **14.78s** | 14.22s | 0.56s | 505 |
| 200 | 1 | 200 | `drift_open` | **24.70s** | 23.98s | 0.72s | 1010 |
| 100 | 3 | 300 | `drift_open` | **30.58s** | 29.54s | 1.04s | 1529 |
| 200 | 3 | 600 | `drift_open` | **64.98s** | 62.66s | 2.32s | 3044 |

Pessimistic mix (100% membership): 50 notes → 13.02s; 100 notes → 20.36s.

`read_note` is flat at **33–110ms** and 2–6 git calls regardless of topic size. Phase 2's O(1)
claim holds exactly.

### The answer to the brief's question

> *at what note count does a drift-queue open cross a user-visible threshold, and is that count
> reachable this decade?*

**It is already crossed, at 8–10 notes.** Not reachable this decade — reachable today, below
current usage. Falsifier 2's predicate is met, and comfortably.

### But the cost is not resolution

**Git subprocess is 92–97% of wall-clock in every one of the twelve scenarios.** Actual
resolution — the `SequenceMatcher` work `dec-058` reasoned about — is **3–8%**: 0.56s at 100
anchors, 2.32s at 600.

The call count is exactly predictable, and the prediction matched every measured row:

```
list_notes  = 2 x anchors
drift_open  = 2 x (2 x anchors) + 7 x queue_members
```

(200 notes x 1 anchor, 30 queue members: predicted 2·(2·200) + 7·30 = 1010; measured 1010.)

Every factor in that formula is a **redundancy**, not resolution work:

1. **`read_file_at` spawns two processes per anchor, not one.** It calls `_exists_at_ref` and then
   `show` — but `git show` already fails on a missing path, so the probe is redundant in the
   common case. This is the leading `2 x`.
2. **The drift path resolves the whole topic twice.** `_drift_payload` already holds a resolved
   `NotesListing`, then calls `reconcile_notes`, which calls `list_notes` again. This is the outer
   `2 x`. It is also why the queue fraction matters less than expected: at 100 anchors, going from
   15% to 100% membership only takes 505 calls to 1100, because the doubled base listing dominates.
3. **Blobs are re-fetched per anchor.** Anchors sharing a `(pinned_at, page)` pair — the normal
   case, since notes cluster on the same pages — each pay their own `git show` for byte-identical
   content. Nothing memoizes within a call.

A fourth, not a redundancy but worth recording: `reconcile_notes` computes transitions for **every**
queue member regardless of the page requested, so a paginated drift open is O(topic), not O(page).

### Machine calibration, stated plainly

Raw git spawn on this machine measures **17–22ms/call** (`show` 17.0, `cat-file -e` 22.0,
`log -2` 16.7, 50 reps each). That is slow — Linux CI typically sees 3–6ms — so **the absolute
seconds above are machine-specific and this machine is on the slow end.** The decomposition
(92–97% subprocess) and the call-count model are not machine-specific. At an optimistic 5ms/call,
200 notes x 1 anchor still costs ~5s, and 100 notes ~2.5s: the verdict survives the calibration,
the exact numbers do not.

---

## What this means for the persisted index

**Falsifier 2 is triggered on its wording and refuted on its reasoning.** Its full text:

> *"Read-time resolution measured on a realistic vault costs enough to be user-visible. Then the
> derived-projection premise ('resolution is free, so don't persist') no longer holds and a
> persisted, mutation-time-updated index becomes the right shape."*

The premise it names — *resolution is free* — is **correct as measured**: resolution is 3–8% of
wall. What is expensive is the git plumbing wrapped around it, and that is an implementation
defect in three named places, not a property of the derived-projection design.

An index would remove the git reads, so it would work. It is simply the most expensive available
fix for a problem three much smaller changes address, and it carries exactly the invalidation and
staleness burden `dec-058` declined it for. Building it now would be paying a permanent structural
cost to avoid deleting a redundant `_exists_at_ref` call.

**Recommendation: close the persisted projection index as "not needed", and open the three
redundancies as the actual work.** This mirrors how Phase 3 closed the spikes — on measurement,
with the instrument committed and re-runnable.

### Post-fix — measured, 2026-08-02

The three redundancies were fixed and this instrument re-run. **The projection above was close on
call count and pessimistic on wall-clock:**

| notes x anchors | git calls before → after | wall before → after |
|---|---|---|
| 10 x 1 | 54 → 12 (4.5x) | 1.47s → **0.132s** (11x) |
| 50 x 1 | 256 → 52 (4.9x) | 6.69s → **0.548s** (12x) |
| 100 x 1 | 505 → 100 (5.05x) | 14.78s → **1.049s** (14x) |
| 200 x 1 | 1010 → 200 (5.05x) | 24.70s → **2.453s** (10x) |

Call count fell **5.05x**, against the modelled ~6x — the model was slightly optimistic because it
assumed `reconcile`'s own `read_file_at(pinned_at, …)` would be memoized too; that read lives
outside `store.py`'s pass cache and still costs one subprocess per queue member. Wall-clock fell
**10-14x**, i.e. *more* than the call ratio, because fewer spawns also means less contention per
spawn.

A drift-queue open is now **snappy at 10 notes** (0.132s) where it was 1.47s, and perceptible
rather than punishing at 50 (0.548s).

**A correction to this document's own earlier claim.** The first post-fix run reported 125 calls at
100 anchors, not 100, and the discrepancy was the instrument's fault rather than the fix's: the
script called `reconcile_notes` without handing it the listing, so it measured a double-listing the
read path no longer performs. `_drift_open` now mirrors `_drift_payload` exactly. A measurement
harness that models the old call path silently under-reports the fix it is measuring.

### Against the index closure's falsifier

`dec-draft-9e1d9377`'s falsifier 1: *"the three redundancies are fixed and a re-run still shows a
drift-queue open above ~1s at note densities the project actually reaches."*

**Not triggered.** Zero notes exist in either configured vault, and at every density within reach —
10 notes (0.132s), 50 notes (0.548s) — a drift open is well under a second. The 1s line is crossed
at **100 notes** (1.049s), which is a density this project has never approached.

The margin there is thin, though, and worth stating plainly: at 100 notes the fix lands *just* over
the threshold rather than comfortably under it. Two reductions remain available before an index
would be the next lever — memoizing `reconcile`'s own historical read (15 of the 100 calls at that
density) and the three per-queue-member metadata calls (`path_commit_shas`, `commit_subject`,
`commit_timestamp`, another 45). Neither is structural.

### What would still deserve an index

Post-fix CPU remains: 0.24s of resolution at 100 anchors, 1.07s at 600, and that floor is immune
to every fix above. If note density ever reaches the high hundreds of anchors per topic **and**
the drift queue is opened interactively, the comparison work alone re-opens this question — on its
own merits, and pointing at memoized projections rather than at the git layer.

---

## Reproducing this

```sh
uv run --frozen python scripts/measure_read_latency.py
uv run --frozen python scripts/measure_read_latency.py --notes 50 100 --queue-fraction 1.0
```

No vault argument exists and none can be supplied.

## Limitations, stated plainly

- **A seeded vault is not a real one.** Real vault history has thousands of commits, which makes
  `git log -2 -- <path>` slower, not faster; the seeded three-commit history is if anything
  optimistic. Page content is synthetic but size-matched to the template.
- **Anchors are synthesised, and cluster by construction.** Real notes may distribute over pages
  differently, which changes how much redundancy 3 costs — but not redundancies 1 and 2, which are
  per-anchor and per-call respectively.
- **Timing variance is real.** Per-call git cost ranged 15–130ms across rows, the high end being
  first-run warm-up. Rows are single runs, not replicates — adequate for a result whose effect
  size is 10-100x a threshold, inadequate for comparing adjacent rows.
