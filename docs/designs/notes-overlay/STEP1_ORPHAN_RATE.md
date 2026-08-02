# Phase 3 Step 1 — the residual orphan rate, measured

**Date:** 2026-07-31. **Cost:** zero — no eval run, no LLM call, no vault touched.
**Instrument:** `scripts/measure_orphan_rate.py` (`--replicates` for the spread run).

This lives in `docs/` for the reason the brief states: it gates billed experiments, and
`.ai-work/` would not survive a fresh checkout.

---

## What was asked

`dec-058` accepted an estimated **8–20% residual hard-orphan rate** (versus block IDs' 2–6%) as
the price of the bi-partite model. The number was imported from prior-art research and had never
been measured on this corpus, and the `dec-062` geometry fix has moved it. Step 1 measures it, then
checks `dec-058`'s recorded reversal triggers against the result.

## How it was measured

`resolve_anchor` is a pure function of `(historical_text, head_text, anchor, two thresholds)`, so
the measurement drives it directly on real page text. **No vault is opened at all** — contact with
the live vault is structurally impossible, not merely avoided.

- **Corpus** — the vault template's three curated KB content pages (headline figures) plus the
  54KB raw source page (reported separately; it is real and anchorable but not what notes
  predominantly point at, and unstratified it supplied 95% of the sample).
- **Anchors** — 59 on KB pages, spread across the four quote shapes `dec-062` proved decisive
  (whole sentence, sub-clause, two sentences, three sentences), sampled stratified within shape.
- **Rewrites** — nine page-level classes at increasing lexical distance, plus two controls.
- **Thresholds** — shipped values, unchanged: `guess_threshold` 0.75, `complete_orphan_threshold` 0.35.
- **Replicates** — six independent perturbation draws per class, because anchors sharing a page
  also share one rewrite realization and are not independent samples.

**Self-checks, both passing:** `untouched` resolves 100% `exact`; `insertion-elsewhere` resolves
100% `shifted`; zero `anchor-invalid` across 963 resolutions.

### The load-bearing assumption

Real loop rewrites are LLM-authored and cost billed spend, so rewrites here are **synthesised at
controlled lexical distance**. This is defensible rather than merely convenient: the resolver is
*purely lexical* — `difflib.SequenceMatcher` plus page-rarest-word seeding — so it cannot
distinguish a semantic paraphrase from a synthetic perturbation of equal lexical distance.
Substitution words are drawn from the corpus's own vocabulary, so the page word-frequency
statistics that drive rarest-word seeding stay realistic.

What this does **not** establish is how often each class actually occurs in loop output. That
distribution is unmeasured, so no aggregate rate is asserted — results are reported per class,
against a *measured* page-similarity figure, and the trigger question is inverted instead.

---

## Result

Hard orphan = `orphaned` at any fidelity: not auto-placed, needs a human. Drift queue = the code's
own `reconcile._QUEUE_MEMBER_STATUSES`, which also contains `fuzzy`.

| rewrite class | page similarity | hard-orphan (mean, 6 draws) | drift-queue |
|---|---|---|---|
| untouched | 1.000 | 0.0% | 0.0% |
| typo | 0.997 | 0.6% | 8.2% |
| insertion-elsewhere | 0.967 | 0.0% | 0.0% |
| light-copyedit | 0.964 | 2.5% | 44.9% |
| moderate-rewording | 0.907 | 15.5% | 77.4% |
| section-restructure | 0.885 | 27.7% | 83.1% |
| heavy-paraphrase | 0.713 | 83.9% | 98.6% |
| total-rewrite | 0.403 | 100.0% | 100.0% |
| paragraph-deletion | n/a | 100.0% | 100.0% |

**There is a cliff between page similarity 0.91 and 0.71.** Below roughly 0.90 the hard-orphan
rate climbs from ~15% to ~84%. `dec-058`'s 8–20% estimate is not one number that is right or
wrong — it is correct only for the light-to-moderate band and understates the heavy band by 4–5x.

Three findings worth carrying forward:

1. **Quote shape no longer decides recoverability.** On KB pages the hard-orphan rate is flat
   across shapes — whole-sentence 38.7%, sub-clause 37.6%, two-sentence 38.9%. `dec-062`'s fix
   holds on realistic pages, not only on its own fixtures, and the shape axis is retired as a
   concern. (KB prose blocks are short, so three-sentence quotes occur only on the raw source
   page, where the all-pages figures are whole-sentence 39.0% against three-sentence 52.8% — a
   residual gradient at the longest shape, on the least representative page.)
2. **Renaming headings costs more than the equivalent prose edit.** `section-restructure` carries
   the same 20% word perturbation as `moderate-rewording` but nearly doubles the hard-orphan rate
   (27.7% vs 15.5%), because a renamed heading removes rung 8's structural fallback and pushes
   results from `orphaned@section` down to `orphaned@page`.
3. **The dominant orphan is not a lost note.** 352 of 394 orphans are `orphaned@section`, and
   rung 8 *always* supplies the surviving section's span as a `best_guess`. The review item is
   "your passage was rewritten; it was in this section", not "gone". This is materially weaker
   than what `dec-058`'s con-column means by an unrecoverable orphan.

---

## Against `dec-058`'s reversal trigger (b)

> *"the fuzzy-band review queue is measured to exceed roughly one item per rewrite event per
> active topic"*

A rewrite event produces (anchors on that page) x (rate), so the trigger is a statement about
**anchor density**, and the measurement converts it into a threshold:

| rewrite class | anchors on the page needed to breach |
|---|---|
| typo | ~170 |
| light-copyedit | 40 |
| moderate-rewording | 6.5 |
| section-restructure | 3.6 |
| heavy-paraphrase | 1.2 |
| total-rewrite / paragraph-deletion | 1.0 |

**The trigger is neither met nor unmet — it is conditional on one unmeasured quantity.** If loop
rewrites are typically light copyedits, a topic would need ~40 anchors on a single rewritten page
to generate one review item, and 3a/3b have lost their justification. If loop rewrites are
typically heavy paraphrases, a *single* anchored note breaches it, and 3a/3b target exactly the
band where the damage is.

## Measured on the real vault — this supersedes the synthetic estimate above

Run on a read-only clone of the live vault the same day. **Real before/after page pairs, real
anchors sampled from the pre-rewrite text, resolved by the shipped resolver** — no synthetic
perturbation and no curve interpolation. This is the measurement the two sections above were
proxies for, and where they disagree, this wins.

The history holds 55 modified-content-page events. They are not one population:

| class | events | anchors | hard-orphan | drift queue |
|---|---|---|---|---|
| **ordinary knowledge rewrites** | 7 | 209 | **7.7%** | 15.3% |
| wholesale supersession | 1 | 90 | 100% | 100% |
| OKF repair migration | 40 | 1236 | 0.5% | 0.8% |
| hard-wrap reflow migration | 7 | 221 | **0.0%** | 14.5% |

**Ordinary knowledge rewrites orphan at 7.7% — below the 8–20% band `dec-058` accepted as the
price of declining block IDs.** The bi-partite bet is holding, and holding better than its author
estimated. `dec-058` trigger (b) would need **13 anchors on a single rewritten page** to breach.

**One event supplies 85% of all orphaning**, and it is not a rewrite: a page superseded wholesale
when its slug changed (page similarity 0.161, headings replaced). Every anchor into it orphans,
correctly — this is precisely the "correct orphan" `dec-058` argued most residual orphans are.

**Both bulk migrations are benign.** The reflow — which unwraps hard-wrapped bodies and should be
the worst case for verbatim quote matching — produces **zero** hard orphans: every anchor lands
`shifted`, `fuzzy` or `exact`, because unwrapping moves words without changing them, and the
ladder's rungs 4–7 absorb that completely.

### Two corrections to the estimates above

1. **The synthetic curve badly overestimates whitespace-only rewrites.** It predicted 8.3% hard
   orphans for the migrations; measured is 0.5% and 0.0%. The curve was built from word
   *substitution*, so at equal `SequenceMatcher` ratio it misreads a reflow — every rarest-word
   seed survives a reflow and none survives a paraphrase. **The curve is valid for paraphrase-like
   rewrites only**, and `measure_rewrite_severity.py`'s interpolated output must not be used for
   migration-shaped changes.
2. **The blended figure that script reports is not the answer.** Run naively it reports 12.0% /
   13.3% and "WITHIN the accepted band" — an artifact of a sample that is 85% mechanical churn,
   scored by a curve that is wrong for exactly that churn. Always split by commit class first.

## The recommendation — close Spikes 3a and 3b as "not needed"

The brief set this test: *"If it is not [still the binding constraint], 3a/3b have lost their
justification and should be closed as 'not needed', not left open."* It is not, on two independent
grounds:

- **The measured residual rate on ordinary rewrites (7.7%) is below the band `dec-058` accepted.**
  Review burden is not the binding constraint; `dec-058`'s reversal trigger (b) is not met.
- **Block IDs cannot address the dominant orphan source.** 85% of measured orphaning comes from a
  wholesale supersession that replaced 84% of the page and every heading. An injected `^id` would
  have been destroyed with the text it marked. The one class that orphans heavily is *immune to
  the intervention 3a/3b would buy* — while still costing the score-integrity inversion
  `dec-058` declined it for.

`dec-058`'s falsifier 1 (≥95% ID preservation plus a clean A/B eval) remains formally unanswered,
but answering it is now pointless: even a passing result would buy almost nothing measurable here.
Falsifier 2 (read-time resolution cost) is untouched by this work and still open.

### What still deserves attention

- **Supersession is its own event class and is not modelled anywhere.** It orphans every anchor
  into the page, by design and correctly, but nothing in the design distinguishes "your passage
  was reworded" from "this page was replaced". A note orphaned by supersession wants a different
  review affordance — a pointer at the *replacing* page — and the anchor of record already carries
  everything needed to offer one. Cheap, unbuilt, and worth more than either spike.
- **Anchor density is the untested half of trigger (b).** Every rate here is per anchor; the
  trigger is per rewrite *event*. At 7.7% a topic needs ~13 anchors on one rewritten page to
  generate a single review item, which no real vault currently approaches — but nobody has
  measured note density because notes have barely been used yet. Re-check once they are.

### Reproducing this

```sh
git clone --no-hardlinks ~/dev/data/knotica /tmp/vault-clone
uv run --frozen python scripts/measure_rewrite_severity.py /tmp/vault-clone
```

`measure_rewrite_severity.py` is read-only (`git log` / `git show` only — no checkout, no fetch,
no write, no lock) and **refuses to run against any vault named in
`~/.config/knotica/config.toml`**, so it cannot be pointed at the live vault by accident. Read its
blended headline with the two corrections above in mind: split by commit class before believing
any aggregate it prints.

---

## A correction to the brief

The brief's § "Step 2" states that LLM adjudication of the `[0.35, 0.75)` band is *less* relevant
now, because small and moderate edits land at 0.99 and vacate the band. The first half is
confirmed; **the conclusion does not follow.**

Measured band occupancy on KB pages: **30.5% of all resolutions**, populated almost entirely by the
heavy classes — `heavy-paraphrase` 89.8%, `paragraph-deletion` 81.4%, `total-rewrite` 50.8%,
against `light-copyedit` 1.7%.

The band did not empty; it **relocated**. Sub-span alignment raised scores everywhere, so light
edits left the band upward into `fuzzy` *and* heavy edits entered it from below. Every one of its
162 members is an item a human must triage (141 `orphaned@section`, 21 `orphaned@page`), so an
adjudicator would be labelling the majority of the review queue in exactly the band where review
burden concentrates — not a marginal surface.

Band occupancy is conditional on the same unmeasured rewrite mix as everything else, so this
re-scopes the question rather than settling it. But "sparser than originally scoped" is not
supported, and the adjudicator should not be dropped on that basis.

---

## Limitations, stated plainly

- **Three KB content pages, 59 anchors.** Small. The large effects (the 0.91→0.71 cliff) are far
  outside the replicate spread; fine distinctions between adjacent classes are not.
- **The rewrite-class→real-severity mapping is the weakest link**, which is why every figure is
  reported against a measured page-similarity ratio rather than against a class label alone.
- **`AC-10` remains stranded**, unchanged by this measurement — see the brief. Its discriminating
  clause still depends on an adjudicator that does not exist.
