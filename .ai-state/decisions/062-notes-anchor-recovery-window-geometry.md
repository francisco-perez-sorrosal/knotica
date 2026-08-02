---
id: dec-062
title: Anchor recovery is bounded by candidate-window geometry, not by guess_threshold — fix the geometry, leave the threshold
status: accepted
category: architectural
date: 2026-07-31
summary: Measurement showed the fuzzy rung's reachability depends on the anchored quote's shape rather than on edit size, because sentence-bounded windows capped at 2x the quote structurally cap the similarity ratio; the fix is sub-span alignment plus multi-sentence widening in the candidate/scoring layer, and guess_threshold stays at 0.75.
tags: [notes, anchoring, calibration, thresholds, measurement, candidate-generation, scoring, phase-3-gate]
made_by: user
agent_type: orchestrator
branch: worktree-notes-overlay-phase2
pipeline_tier: full
dissent: Lowering guess_threshold was one config line and would have moved most of the distribution into `fuzzy` immediately; instead this changes two pure modules at the heart of every projection in the vault, where every defect this phase has been silent and vault-wide, and it does so to buy correctness that no user has yet asked for.
affected_files:
  - src/knotica/core/notes/candidates.py
  - src/knotica/core/notes/scoring.py
  - src/knotica/core/notes/resolve.py
  - src/knotica/mcp_server/tools_notes.py
  - skills/wiki-maintenance/SKILL.md
affected_reqs: [REQ-03, REQ-04]
re_affirms: dec-058
supersedes: dec-draft-c4b81d90
re_affirmed_by:
  - dec-064
---

## Context

`dec-058` set `guess_threshold = 0.75` and `complete_orphan_threshold = 0.35`, citing MSR: start
high, accept more orphans and more review, avoid silent misplacement. `SYSTEMS_PLAN` recorded that
Hypothesis's weights were **never validated against this corpus**. Phase 2 validated them — and the
first validation drew the wrong conclusion.

**A superseded draft of this decision** (`dec-draft-c4b81d90`) reported that no non-verbatim edit
exceeds ~0.64, concluded that the `fuzzy` rung is unreachable at the shipped threshold, and
recommended leaving the threshold alone pending a user ruling. Post-implementation verification
measured the same ladder from two independent directions and **refuted the premise**.

`fuzzy` fires. At `guess_threshold = 0.75`, a one-character typo scores **0.9946** — when the
anchored quote is approximately a whole sentence. The draft's matrix used a single quote shape (a
sub-clause of a longer sentence) and generalised from it.

**What actually governs reachability is the quote's shape, not the size of the edit:**

| anchored quote | 1-character typo | why |
|---|---|---|
| a whole sentence | `fuzzy` 0.9946 | the candidate window coincides with the quote |
| a sub-clause of a sentence | `orphaned` 0.639 | the window is structurally ~2x the quote |
| two sentences | `orphaned` 0.6934 | a window never spanned more than one sentence |
| three sentences | `orphaned` 0.5697 | same, worse |

`generate_candidates` extended every window to sentence bounds and capped it at
`CAP_MULTIPLIER x len(quote)` = 2x. So `SequenceMatcher.ratio() = 2M/(len(a)+len(b))` was capped
near `2·len(q)/(len(q)+2·len(q)) ≈ 0.667` for a sub-span **no matter how small the edit** — which
is why a one-character typo (0.639) and a heavy paraphrase (0.647) were indistinguishable — and a
multi-sentence quote could not be matched at any threshold, because no window ever spanned two
sentences. A 107-configuration sweep at a single-character edit reached `fuzzy` only at ≥85%
sentence coverage.

The compounding detail: `note_capture`'s own tool description instructed clients to pass *"the
passage you displayed"*, which is frequently more than one sentence — **the recovery ladder's
headline capability was disabled by the capture guidance itself.**

## Decision

**Fix the geometry. Leave `guess_threshold` at 0.75 and `complete_orphan_threshold` at 0.35.**

Three parts, ruled together by the user on 2026-07-31:

1. **Sub-span alignment** (`scoring.py`, new `align_candidate`) — a candidate window is trimmed to
   the region the quote actually matches, and the quote/prefix/suffix ratios are drawn from there,
   so a window wider than the quote is no longer penalised for its width. This is exactly what
   ruling **L6** deferred during implementation, saying there was "no validation data to justify
   it". The measurement above is that data. "Matched extent" was chosen over a fixed `len(quote)`
   slice after measuring both.
2. **Multi-sentence widening** (`candidates.py`) — a window absorbs neighbouring sentences when the
   quote outruns its own sentence, with block bounds made symmetric so widening cannot swallow page
   chrome. The single-sentence path is byte-identical, which is what keeps `td-025`'s backward-
   boundary regression green.
3. **Capture guidance** (`tools_notes.py`, `skills/wiki-maintenance/SKILL.md`) — prefer one
   complete sentence as `quote`. Now a preference rather than a workaround, since recovery works on
   any shape.

`resolve.py` needed no logic change; the aligned span flows through `score_candidates`. Its rung-7
docstring was corrected, since the reported `span` is now the aligned sub-span rather than the
sentence-bounded window.

**Measured after the fix**, at a 1-character edit, threshold unchanged:

| quote shape | before | after |
|---|---|---|
| whole sentence | 0.996 `fuzzy` | 0.996 `fuzzy` |
| sub-clause | 0.646 `orphaned` | **0.988 `fuzzy`** |
| two sentences | 0.726 `orphaned` | **0.997 `fuzzy`** |
| three sentences | 0.648 `orphaned` | **0.998 `fuzzy`** |

**The band sharpened rather than widened** — the failure mode that would have made this a bad
trade. Every measured unrelated-text cell *fell*: whole sentence 0.641 → 0.265, two sentences
0.429 → 0.217, three sentences 0.446 → 0.219. Same-passage edits rose, unrelated text dropped, and
the empty band between them grew.

## Considered Options

### A — capture guidance only

One sentence in the tool description asking for a whole-sentence quote. Zero algorithmic risk,
immediate effect for new captures, nothing for anchors already on disk. **Adopted, but as part of
the fix rather than instead of it**: guidance that exists to route users around a defect is a
workaround, and it leaves every hand-authored and already-captured note stranded.

### B — fix the candidate-window geometry

**Adopted.** Addresses the cause for every quote shape, including notes already captured, because
the bi-partite design recomputes each projection from unchanged records — the backfillability
`dec-058` was chosen for. Costs a change in the two pure modules every projection runs through.

### C — lower `guess_threshold` to 0.55–0.60

**Rejected.** The superseded draft's recommendation, and the weakest option once the cause was
understood:

- It does **nothing** for multi-sentence quotes, which were unreachable at any threshold.
- It would auto-place notes on a score band in which a 1-character typo (0.639) and a heavy
  paraphrase (0.647) are indistinguishable — the score no longer tracks edit distance there, so
  the threshold would be gating on noise. That is the silent misplacement MSR warned about and
  `dec-058` deliberately guarded against.
- It papers over a geometry defect with a config value, leaving the defect to resurface wherever
  else the score is consumed.

## Consequences

**Positive.** All four quote shapes reach `fuzzy` for small edits, so the auto-placing rung the
recovery ladder exists to reach is now reachable on realistic data. Resolver improvements are
retroactive by construction: no stored anchor changes, every projection recomputes. The
separation between same-passage edits and unrelated text widened, so the thresholds are now
gating on a signal that discriminates.

**Negative.** The change lives in `candidates.py` and `scoring.py`, which every projection in the
vault runs through, and a defect there is silent and vault-wide. It is guarded by the existing
table-driven suites plus six re-bracketed fixtures. Six pre-existing test fixtures had to be
re-bracketed because their scores moved — thresholds re-chosen around the new scores, with no
assertion weakened and no case dropped.

**One behaviour change, flagged rather than buried.** A whole-sentence *heavy paraphrase* moved
from `fuzzy` 0.788 to `orphaned` 0.675 — alignment made the scorer stricter about what counts as
the same passage, so that case is now human-reviewed instead of auto-placed. Given MSR's finding
that users reject silent low-confidence placement, this is defensible, but it is a real reduction
in automatic recovery for one class and should be watched.

**Not addressed here.** The rung-8 clamp reports `guess_threshold - CLAMP_EPSILON` when candidate
scoring never ran; that is a separate defect, fixed separately by adding
`Projection.score_measured` so a sentinel is never rendered to a human as a similarity percentage.

## Disconfirmation

**Falsifier.** Either of these would make this decision wrong:

1. Field use shows the `fuzzy` rung now placing notes on the *wrong* passage — a same-page
   paraphrase of a different claim scoring above 0.75 because alignment trims to whatever
   fragment happens to match. The band-sharpening measurement argues against it (unrelated text
   fell to ~0.22), but "unrelated text" and "a different claim on the same page" are not the same
   test, and only the second one matters.
2. The re-bracketed fixtures turn out to have been re-bracketed *around* a defect rather than
   around a corrected score — i.e. a rung is now exercised only by a threshold chosen to reach it,
   with no realistic input landing there.

**Steelmanned runner-up (Option C).** Lowering the threshold was one line, needed no review of the
two most dangerous modules in the feature, and would have moved most of the measured distribution
into `fuzzy` the same afternoon. The argument that the 0.55–0.60 band "does not discriminate" rests
on one fixture family; a wider corpus might show it separating cleanly. And `dec-058` itself said
to *start high and let the user lower it* — declining to lower it after measuring is arguably
refusing the escape hatch the original decision built in. If the geometry change later proves to
have introduced a silent misplacement, this decision will look like it rewrote a scorer to avoid
editing a config file.

**Reversal trigger.** Revisit when either holds: (a) a note is observed re-anchored to a passage
that is on the right page but is not the passage the user pinned — the silent-misplacement mode,
which no amount of band-sharpening rules out; or (b) the automatic re-anchor rate is measured on a
real vault and is *still* the binding constraint on review burden, in which case the remaining
limit is the threshold after all and Option C returns with evidence behind it.

## Prior Decision

Supersedes the unfinalized draft `dec-draft-c4b81d90` ("The fuzzy rung is unreachable at
guess_threshold 0.75 — measured, and left unchanged pending a user ruling"). That draft never
reached `accepted`. Its measurement was sound for the one quote shape it tested and its conclusion
did not generalise: the rung is reachable, and what limits it is geometry rather than calibration.
Its recommendation to defer Phase 3's block-ID spikes (3a/3b) still stands, but for a different
reason — it argued that a config change might deliver what the spikes were meant to buy, and a
config change would not have. Option B addresses the same failure class as block IDs, costs no
billed eval run to test, and does not invert the feature's founding coupling by writing into the
scored corpus.

Re-affirms `dec-058`: nothing here changes the bi-partite anchor model, and the fix is a live
demonstration of the property that model was chosen for — the resolver improved and every stored
record stayed untouched.
