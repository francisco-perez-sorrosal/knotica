---
id: dec-058
title: Personal-note anchoring — bi-partite immutable anchor of record plus derived live projection
status: accepted
category: architectural
date: 2026-07-29
summary: A note's anchor is an immutable (page, commit_sha, quote[, start]) record written once at capture, plus a projection onto HEAD (exact|shifted|fuzzy|orphaned) derived lazily at read time; no block-ID injection, no commit-time re-anchoring, corrections append.
tags: [notes, annotation, anchoring, git, w3c-annotation, loop, offline, client-as-brain]
made_by: agent
agent_type: systems-architect
branch: main
pipeline_tier: standard
affected_files:
  - src/knotica/core/notes/anchor.py
  - src/knotica/core/notes/resolve.py
  - src/knotica/core/operations/capture_note.py
  - src/knotica/core/operations/reanchor_note.py
  - src/knotica/core/notes_config.py
  - src/knotica/core/loop.py
affected_reqs: [REQ-01, REQ-02, REQ-03, REQ-04, REQ-05, REQ-06, REQ-10, REQ-12]
re_affirmed_by: [dec-061, dec-062]
dissent: Declining block-ID injection accepts an estimated 8-20% hard-orphan rate per major rewrite instead of 2-6%; if the loop turns out to preserve `^id` tokens reliably and injection costs the eval scalar nothing, this design leaves a large, cheap durability win unclaimed for a whole release.
---

# Personal-note anchoring — bi-partite immutable anchor of record plus derived live projection

## Context

The notes overlay must attach a personal reflection, provoked by *ephemeral LLM-generated prose*, to a concrete span of stored KB text, and keep it meaningful while the autonomous maintenance loop rewrites pages wholesale (`write_page` is a full-body replace; there is no patch primitive anywhere in the write path).

External prior art (`RESEARCH_FINDINGS_anchoring-prior-art.md`) recommends Obsidian block IDs (`^id`) preserved by the rewriter plus commit-time cooperative re-anchoring as the spine, with quote/keyword fuzzy matching as a recovery ladder and LLM adjudication for the middle band. It also names the load-bearing risk: whether an LLM-authored full-page rewrite reliably preserves `^id` tokens. Measured orphan rates in the reference system (Hypothes.is, Aturban et al. 2015, n=20,953) are 22% orphaned / 53% at risk under *incidental* web drift; full paraphrase is far more destructive.

Knotica has three structural advantages no surveyed system has: the corpus is versioned so every pre-image is permanently retrievable (`VaultVcs.read_file_at`, `path_commit_shas`, `diff_between`); the rewriter is its own cooperative loop; and an LLM is available at both ends. Constraints: deterministic stateless server (client-as-brain), one flock-guarded commit per mutating operation, offline installs must keep working, notes must be hand-editable in plain Obsidian, and notes must never influence any KB quality score.

## Decision

The anchor is **bi-partite**.

**Anchor of record — immutable, written once at capture, never modified:**
`(page, commit_sha, quote[, start])`, stored in the note body under an `## Anchors` heading as a markdown list — one bullet per anchor, carrying a vault-relative wikilink to the page, the capture-time fidelity, and a backticked commit token, with the verbatim quote as a blockquote line beneath it:

```markdown
## Anchors

- [[agentic-systems/alignment-failures#Reward hacking]] — `span` · pinned@`a3f9c21`
  > the model learns to satisfy the metric rather than the goal
```

An optional ` · at=<int>` token disambiguates a quote that occurs more than once. The bullet carries **record facts only**; the projection's `status` is derived at read time and is never written to disk.

Both the wikilink and the quote line are **optional**, and the backticked fidelity plus the `pinned@` token are the bullet's signature. A linkless bullet is how `topic` fidelity is written — which matters more than it looks: when a capture cannot be pinned to a page, the linkless bullet is what still carries the passage the user was reacting to. Without it a degraded capture would store the reflection and drop its provoking quote, defeating the guarantee this decision exists to provide. A bullet with no quote line records "this note is about that page", which is a legitimate capture with nothing to quote.

> **Amended 2026-07-29, during Phase 1 implementation.** This decision originally specified an Obsidian `> [!quote]` callout. The callout has no slot for the capture-time fidelity and no legible shape for the append-only supersession history this ADR mandates for corrections, so Phase 2's `reanchor` would have had to change the format anyway. The markdown-list rendering carries both. The *decision* — an immutable three-scalar record living in the note body, derived projection never persisted — is unchanged; only its on-disk rendering is restated.
>
> Two further points were settled against the implementation and are binding on any future reader:
> **(a)** an `## Anchors` heading opens an anchor region and any other level-1/2 heading closes it; a note may open the region any number of times and anchors are recovered from **every** region, with all non-anchor lines flowing back into the body in document order. The obvious first-heading-to-next-heading reading silently discarded data four ways and was rejected on evidence.
> **(b)** the stored `fidelity` is carried as an opaque string, not a closed enumeration, so a file written by a later generation (`block`, `section`) round-trips through an earlier reader rather than raising.

Only three fields are stored because the rest of the W3C selector composition is *derivable*: given `(page, commit, quote)`, `read_file_at(commit, page)` yields the historical text, and locating the quote in it recomputes `start`, `end`, `prefix`, `suffix` and the enclosing heading on demand, guaranteed self-consistent. `start` is retained only to disambiguate a repeated quote. In a versioned corpus, `TextPositionSelector` and prefix/suffix are a cache of a git read, not data.

**Live projection — derived, never persisted, never committed:**
`status ∈ {exact, shifted, fuzzy, orphaned}`, `granularity ∈ {span, section, page, topic}`, a span into HEAD, a score, and an optional `best_guess`. Computed lazily at read time from `HEAD:page` plus the historical blob.

**Resolution ladder** (in order): historical resolution (always available; failure is `anchor-invalid`, a data-integrity error, not an orphan) → page missing ⇒ `orphaned@topic` → exact quote at recorded offset ⇒ `exact` → exact quote elsewhere ⇒ `shifted` → MSR keyword candidate generation (≥3 page-rarest words of the quote, relaxing frequency until three are found, windows extended to sentence bounds, capped at 2× quote length) → Hypothesis-weighted scoring (quote 50 / prefix 20 / suffix 20 / position 2, normaliser 92, `difflib.SequenceMatcher` for similarity) → `score ≥ guess_threshold` ⇒ `fuzzy` → historical heading still present ⇒ `orphaned@section` with a guess **clamped just below the threshold** so it is always reviewed → `score ≥ complete_orphan_threshold` ⇒ `orphaned@page` with a guess → otherwise `orphaned@page` with **no guess**.

Two thresholds, per MSR-TR-2001-107: `guess_threshold = 0.75`, `complete_orphan_threshold = 0.35`, in a `[notes]` config table mirroring `[loop]`/`[gapfill]`. Start the guess threshold high; adaptive tuning is out of v1 scope.

**No `^block-id` injection into page bodies in v1.**

**LLM adjudication is confined** to the `[complete_orphan_threshold, guess_threshold)` band, to human-facing review surfaces only, and produces a non-binding label (`moved` | `retracted` | `unknown`) — never an applied re-anchor. Offline installs traverse the identical deterministic ladder.

**Re-anchoring runs lazily at read time**, never on the mutation critical path. A **read-only post-merge reconciliation pass** on the default branch reports status transitions to a review queue; it takes no lock and writes no note file.

**Corrections append.** A human-accepted re-anchor adds a new anchor block; the original is never modified or removed.

## Considered Options

### Option A — Bi-partite: immutable record + derived projection (chosen)

- **Pro** — a note is permanently readable from `(page, commit, quote)` alone via `git show`, with zero dependence on HEAD, on the loop's behaviour, or on any hook having run.
- **Pro** — zero writes on resolution. A rewrite touching N annotated spans produces no note commits at all, so the loop never mutates the user's personal files and there is no commit churn.
- **Pro** — resolver improvements are backfillable: change the matcher or the thresholds and every projection in the vault recomputes correctly, because no original was ever overwritten.
- **Pro** — the anchor layer cannot wedge the KB. Resolution is a pure read outside the flock.
- **Con** — without block IDs, full paraphrase is unrecoverable deterministically; estimated 8–20% hard-orphan rate per major rewrite versus 2–6% with IDs (both low-certainty extrapolations from adjacent measurements).
- **Con** — resolution cost is paid on every read rather than once per mutation.

### Option B — Block IDs + commit-time re-anchoring (the research's recommended spine)

- **Pro** — the only surveyed mechanism that survives a full paraphrase cleanly; lowest estimated residual orphan rate.
- **Pro** — matching happens once, immediately, against fresh text.
- **Con** — annotation durability becomes contingent on LLM instruction-compliance across every mutating path; the research names this its own load-bearing risk and it is unmeasured.
- **Con** — `^id` tokens are literal corpus text: chunked, BM25 length-normalised (document length here is *file byte size*, so ~10 bytes × ~30 blocks ≈ 7% inflation feeding the `b=0.75` term), read by the LLM judge if copied into an answer, and liable to be swept into extracted citation quotes. A mechanism that improves annotation while degrading KB eval quality is a bad trade, and the magnitude is unknown.
- **Con** — the unscored personal layer would edit the scored KB corpus, inverting the feature's founding coupling.
- **Con** — commit-time rewriting destroys the original anchor, making resolver improvements unbackfillable, and puts note work inside the recently *widened* `vault_mutation_span` (checkout→merge→branch-delete→commit, with crash self-heal), adding a failure mode to the KB's most safety-critical section on behalf of a personal feature.
- **Con** — E's stated advantage, "match while the pre-image is still in hand," is imported from systems that do not retain pre-images. Git retains all of them forever, so E buys latency, not capability.

### Option C — Embedding / semantic re-anchoring

- **Pro** — meaning-preserving paraphrase is exactly what embeddings handle well.
- **Con** — no production annotation system was found doing this and no accuracy data exists.
- **Con** — breaks offline installs.
- **Con** — its characteristic failure is re-anchoring to a *similar* passage that is not *the* passage — the silent-wrong-anchor mode the two-threshold design exists to prevent.

### Option D — CRDT relative positions (Yjs / Automerge)

- **Con** — rejected categorically. The stability guarantee holds only for edits applied through the CRDT; the loop writes whole `.md` files out of band, yielding `null` or nonsense. Also architecturally incompatible with a plain-markdown, git-is-the-state vault.

## Consequences

**Positive**

- A note is never lost and its provoking text is never unreadable, regardless of what the loop does.
- Notes impose zero write load, zero lock time and zero failure modes on the KB mutation path.
- The whole anchor layer is deterministic, stdlib-only (`difflib`), and offline-identical.
- Orphaning becomes a *feature* — a curated stream of "the KB changed under something you cared about" — rather than a defect list, because the historical text is always renderable beside the current one.
- Append-only anchors make anchor history auditable and every projection recomputable.

**Negative**

- Higher deterministic orphan rate than the block-ID design; more human review per major rewrite.
- Resolution work is paid per read. Mitigated: happy path is one `str.find`; a persisted index under `.knotica/` is a designed but unbuilt seam.
- Notification depends on a reconciliation pass that could silently stop running. Degraded mode is correct (read-time resolution is unaffected), so this is a UX loss, never data loss.
- Two thresholds are a tuning surface with no local empirical basis; the MSR values are from n=8 on a different corpus.

## Disconfirmation

**Falsifier.** Two independent measurements would each make this decision wrong:
1. Block-ID preservation across the loop's rewrites measures ≥95% **and** an A/B eval on clones (HEAD vs. HEAD+injected-IDs, same frozen `golden.jsonl`, same instrument and snapshot) shows no leg of the scalar degrading beyond the loop's regression tolerance. Then IDs are nearly free and the paraphrase failure class is eliminable — the chief reason for declining them evaporates.
2. Read-time resolution measured on a realistic vault costs enough to be user-visible. Then the derived-projection premise ("resolution is free, so don't persist") no longer holds and a persisted, mutation-time-updated index becomes the right shape — which is Option B's locus by another route.

**Steelmanned runner-up (Option B).** The strongest case for block IDs plus commit-time re-anchoring: annotation systems have failed for 25 years on exactly one axis, and the MSR study's finding is unambiguous — orphaning is the number-one user complaint, ahead of everything else. This design *chooses* a 3–4× worse orphan rate on the strength of a reframing ("most residual orphans are correct orphans") that is asserted, not measured. Meanwhile Knotica is the one system in the entire surveyed literature whose rewriter is *cooperative and under its own control*: it can simply be told to preserve the IDs, and a lint check can verify it did. Declining to use that unique advantage — the single structural edge the research identifies — because of an unquantified worry about ~7% BM25 length inflation is trading a certain, large, user-facing benefit against a speculative, small, machine-facing cost. And the coupling-inversion objection is aesthetic: the vault already carries frontmatter, citation keys and index catalog lines that exist to serve machinery rather than the reader; one more invisible token is not a category change. If the eval delta measures to zero, this design will look like it spent a release avoiding a solved problem.

**Reversal trigger.** Revisit when *either* holds: (a) Spike 3a and 3b both pass their gates (≥95% ID preservation; no eval-leg degradation beyond the loop's regression tolerance) — then add `^id` as an *additional selector on new anchors*, with no stored record changing and the bi-partite structure intact; or (b) the fuzzy-band review queue is measured to exceed roughly one item per rewrite event per active topic, i.e. the review burden becomes the thing users complain about — the exact 25-year-old failure mode this design bet against.
