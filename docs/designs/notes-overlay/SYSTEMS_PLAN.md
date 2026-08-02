# Plan: Personal Notes (Marginalia) Overlay

**Task slug:** `notes-overlay` · **Tier:** Standard · **Date:** 2026-07-29 · **Mode:** feature (design-only pass)

## Goal

Let a reader durably attach a personal note to the concrete KB span that provoked it — captured from ephemeral LLM-generated prose, surviving the maintenance loop's rewrites, invisible to every KB quality score, and harvestable as real human eval questions.

## Tech Stack

Python 3.12+, uv-managed, src layout. FastMCP server, Obsidian/git vault via `VaultStore`/`VaultVcs`/`VaultTransaction`. Resolver uses **stdlib only** (`difflib.SequenceMatcher`) — no new runtime dependency, offline-safe by construction. No new library is introduced, so no version/capability verification is required this pass.

## Acceptance Criteria

Behavioural. Each is observable and drives a downstream test.

- [ ] **AC-01** When a note is captured against a page span the server can verify verbatim, the note file exists at `notes/<topic>/<ts>-<slug>.md` in exactly one commit, and its anchor of record carries `(page, commit_sha, quote, start)`.
- [ ] **AC-02** When the client supplies a quote the server *cannot* find in the named page, the note is still stored — with `provenance: page` and the unverified quote preserved verbatim — and the response reports the degradation. Capture never fails on an unverifiable quote.
- [ ] **AC-03** When a note file is written or edited, the autonomous loop's `observe_default()` does not treat it as a content change and no eval is triggered.
- [ ] **AC-04** When a topic containing notes is linted or evaluated, the composite eval scalar and every one of its three legs are **byte-identical** to the same vault with the `notes/` tree deleted.
- [ ] **AC-05** When a note's anchored page is rewritten such that the quote survives verbatim at a new offset, resolving the note reports `status: shifted` and points at the new offset — with zero writes and zero commits.
- [ ] **AC-06** When the quote no longer appears verbatim but a candidate scores at or above `guess_threshold`, resolving reports `status: fuzzy` with the candidate span and a confidence score.
- [ ] **AC-07** When the best candidate scores below `guess_threshold`, resolving reports `status: orphaned`. If the historical enclosing heading still exists at HEAD, `best_guess` is that surviving section's span — structural evidence, offered *regardless of score*, at `section` fidelity. Otherwise a candidate at or above `complete_orphan_threshold` is carried as `best_guess` at `page` fidelity, and below it no guess is offered. *(Corrected 2026-07-31: the original wording described a single-threshold ladder in which the `complete_orphan_threshold` band alone gates every guess. The shipped ladder evaluates the surviving-heading rung first — see § Resolution ladder rungs 7–9 — because a surviving heading is structural evidence stronger than any similarity score. The code was right and this criterion was wrong.)*
- [ ] **AC-08** When a note's anchor is orphaned or fuzzy, the pre-rewrite text of the anchored span is still retrievable verbatim from the anchor of record alone (`page` + `commit_sha` + `quote`), with no dependence on the current HEAD.
- [ ] **AC-09** When a human corrects an anchor, a *new* anchor is appended to the note; the original anchor of record is never modified or removed.
- [ ] **AC-10** Anchor resolution cannot reach an LLM client: the transitive import closure of the resolution entry points (`core.notes.store`, `core.notes.reconcile`, `core.notes.resolve`) contains no model client, so an install with no credentials produces every status in AC-05..AC-08 identically. *(Reworded 2026-08-02. The original added "only optional adjudicator commentary in the `[complete_orphan_threshold, guess_threshold)` band is absent" — a clause that **could never fail**: with no adjudicator built, there is nothing to omit, so it was vacuously true whether or not the guarantee held. The load-bearing half was meanwhile asserted in prose and gated by nothing. This wording keeps the real guarantee and states it in the form that is actually verified, which is **stronger** than status parity: parity says the two paths agree, import purity says the offline path is the only path. Verified by `tests/core/notes/test_offline_parity.py`. When an adjudicator is built, its offline-absence belongs in that phase's own criterion — not backdated into a Phase 2 exit gate.)*
- [ ] **AC-11** When a note-derived question is promoted, a `QARecord` with `source: curate_example` is appended to `<topic>/.knotica/datasets/qa.jsonl` in one commit, with `pages_used` naming the anchored KB page(s) — after explicit human confirmation, never automatically.
- [ ] **AC-12** When a note's anchor orphans, no gap record is created automatically; filing a gap is a human action, offered only for `intent ∈ {dispute, gap, question}`, and lands with `origin: reported`.
- [ ] **AC-13** A note file created by hand in Obsidian — correct frontmatter, one `> [!quote]` callout — is read, resolved, and listed identically to a tool-captured note.
- [ ] **AC-14** After the folder-family prerequisite, `RESERVED_TOP_LEVEL_NAMES` is declared exactly once in the codebase and `notes` is a member; `knotica lint` reports no `RESERVED_TOP_LEVEL_NAME` violation for a vault containing `notes/`.

## Behavioral Specification

Standard tier, medium complexity. Requirements in `When/and/the system/so that` form.

| ID | Requirement |
|---|---|
| REQ-01 | **When** a capture request names `(topic, page, quote, text, intent)` **and** `quote` occurs in the working-tree page, **the system** writes one note file whose anchor records the current HEAD sha, the quote, and the match offset, in one flock-guarded commit, **so that** the note is permanently reconstructible from git alone. |
| REQ-02 | **When** `quote` does not occur in the named page, **the system** stores the note anyway with `provenance: page` and returns a degradation notice, **so that** capture friction never costs the user their reflection. |
| REQ-03 | **When** a note's anchor is resolved against HEAD, **the system** returns exactly one of `exact \| shifted \| fuzzy \| orphaned` plus a confidence score and a span, **and** performs no write, **so that** resolution is free, re-runnable, and never produces commit churn. |
| REQ-04 | **When** the exact quote is absent, **the system** generates candidates by seeding on the page-rarest words of the quote and scores each by weighted quote/prefix/suffix/position similarity, **so that** paraphrase is recoverable without an LLM. |
| REQ-05 | **When** a candidate scores in `[complete_orphan_threshold, guess_threshold)` **and** an LLM client is configured, **the system** may attach a non-binding adjudication (`moved` \| `retracted` \| `unknown`) to the review item, **and** never applies it automatically, **so that** offline installs behave identically on the deterministic path. |
| REQ-06 | **When** a human accepts a re-anchor, **the system** appends a new anchor block to the note file, **so that** the anchor history is append-only and resolver improvements are backfillable. |
| REQ-07 | **When** any KB scoring surface enumerates pages (retrieval, golden bootstrap, trainset bootstrap, lint content pages, content-page count), **the system** excludes the `note` family by omission, **so that** notes cannot contaminate a score by construction rather than by an accumulating filter list. |
| REQ-08 | **When** lint computes inbound wikilink targets for the orphan check, **the system** counts only links whose *source* is a scored family, **so that** a personal note cannot silently de-orphan a KB page and move `lint_violations`. |
| REQ-09 | **When** the loop diffs changed paths on the default branch, **the system** skips paths in the `note` family, **so that** a note write never wakes an eval cycle. |
| REQ-10 | **When** the loop merges a rewrite into the default branch, **the system** runs a read-only reconciliation that reports anchor-status transitions to a review queue, **and** takes no vault lock and writes no note file, **so that** notes never sit on the mutation critical path. |
| REQ-11 | **When** a human promotes a note-derived question, **the system** appends one `QARecord` to the topic's trainset via `curate_example`, **so that** the eval bridge reuses the existing idempotent single-commit append and no new queue or record type is introduced. |
| REQ-12 | **When** a note carries multiple anchors or an anchor into a page of a different topic, **the system** resolves each independently, **so that** one reflection may span several claims and cross topic boundaries. |

## Architecture

### Overview

Four separable pieces, deliberately loosely coupled:

1. **A folder family** — `notes/<topic>/` at vault root, made legible by a single new `core/vault_layout.py` module that collapses the existing duplicated reserved-name declarations and introduces `family_of(path)`. Notes are excluded from every scoring surface *by omission*, and from the one surface that omission does not cover (`lint._check_orphans`) by a single family predicate.
2. **A bi-partite anchor** — an **immutable anchor of record** `(page, commit_sha, quote[, start])` written once at capture, and a **derived live projection** onto HEAD with status `exact | shifted | fuzzy | orphaned`, computed on read and never persisted. Corrections append a new anchor; nothing is ever rewritten.
3. **A deterministic resolver** — stdlib-only, offline-safe, with two thresholds and an optional LLM adjudicator confined to the middle band and to human-facing review.
4. **A one-line eval bridge** — `curate_example()` with `pages_used` set to the anchored KB page, behind explicit human confirmation.

The load-bearing move is (2). Everything downstream — no commit churn, no loop coupling, backfillable resolver improvements, permanent readability of the provoking text — follows from making the anchor immutable and the projection derived.

### Components

| Change | Path | Notes |
|---|---|---|
| **New** | `src/knotica/core/vault_layout.py` | ~60 lines. `RESERVED_TOP_LEVEL_NAMES` (single declaration, now incl. `notes`), `SOURCES_DIR`/`NOTES_DIR`, `TOP_LEVEL_FAMILY_DIRS`, `Family = Literal["page","source","note"]`, `family_of(rel_path)`, `topic_of(rel_path)`, `SCORED_FAMILIES`. Zero imports from `core.*` — a leaf module. |
| **New** | `src/knotica/core/notes/anchor.py` | Anchor-of-record dataclass + note-file parse/serialize (frontmatter + `> [!quote]` callouts). Pure, no I/O. |
| **New** | `src/knotica/core/notes/resolve.py` | The resolution ladder + scoring. Pure over `(historical_text, head_text, anchor)`. Stdlib only. |
| **New** | `src/knotica/core/notes/store.py` | Read/list notes for a topic or an anchored page; resolves projections. Read-only. |
| **New** | `src/knotica/core/operations/capture_note.py` | The single mutating operation. One `VaultTransaction`, op name `note_capture`. |
| **New** | `src/knotica/core/operations/reanchor_note.py` | Human-accepted append-a-new-anchor. One `VaultTransaction`, op `note_reanchor`. |
| **New** | `src/knotica/core/notes_config.py` | `[notes]` config table: `guess_threshold`, `complete_orphan_threshold`, `adjudicate` (bool). Mirrors `loop_cadence_config.py` / `gapfill_config.py` exactly. |
| **Modify** | `src/knotica/core/lint.py` | `RESERVED_TOP_LEVEL_NAMES` and `_SOURCES_DIR` become imports; `_check_reserved_names` exempts `TOP_LEVEL_FAMILY_DIRS`; `_check_orphans` filters inbound links by source family. |
| **Modify** | `src/knotica/core/vault_scaffold.py` | `RESERVED_TOPIC_NAMES` becomes an import alias of the single declaration. |
| **Modify** | `src/knotica/search/ripgrep.py` | `_classify` delegates to `family_of`/`topic_of` so a `notes/` path is classified `note`, not a page of topic `"notes"`. `_scope_dirs` unchanged in v1. |
| **Modify** | `src/knotica/core/loop.py` | One `continue` branch in `_content_changed_since` keyed on `family_of(path) == "note"`. |
| **Modify** | `vault-template/SCHEMA.md` | Document `notes/` as a reserved, unscored folder family. |
| **Unchanged, deliberately** | `store_source.py`, `source_ingest.py`, `guillotine/search.py`, `evals/citations.py`, `page.py`, `okf/index.py`, `golden_review.py` | Their `"sources"` literals are *sources-specific logic*, not folder-family dispatch. Rewriting them buys nothing for notes and would dwarf the feature. See § Rejected Alternatives R6. |

### The anchor model

#### Anchor of record — immutable, three scalars

```yaml
page:   agentic-systems/agent-memory.md   # vault-root-relative
commit: 9f3c1e2                            # W3C TimeState; vault HEAD at capture
quote:  trading recall precision for storage economy   # TextQuoteSelector.exact
start:  118                                # optional TextPositionSelector hint / disambiguator
```

**Why only three fields.** The W3C composition the external research recommends — `FragmentSelector` refinedBy `TextQuoteSelector` + `TextPositionSelector` + `TimeState` — is the right *semantics*, but in a git-backed corpus most of it is **derivable, not storable**. Given `(page, commit, quote)`, the historical blob is `read_file_at(commit, page)`; locating `quote` in it yields `start`, `end`, `prefix`, `suffix`, and the enclosing heading — exactly the full selector set, recomputed on demand, guaranteed self-consistent. Storing them would be storing a cache of a git read. `start` is retained only as a disambiguator when the quote occurs more than once. This is the one place where Knotica's versioned corpus genuinely buys a simplification that no prior-art system could take.

#### Live projection — derived, never persisted, never committed

```
status:      exact | shifted | fuzzy | orphaned
granularity: span | section | page | topic
span:        (start, end) into HEAD:page      # absent when orphaned
score:       0.0 – 1.0                         # 1.0 for exact/shifted
best_guess:  (start, end) | null               # only when orphaned above the complete-orphan floor
```

Computed by reading `HEAD:page` and the historical blob. No cache in v1 — resolution is a single `str.find` on the happy path and personal-scale note counts are in the tens-to-low-hundreds per topic. A persisted index, if ever needed, belongs under `.knotica/` (dot-prefixed ⇒ already invisible to `iter_page_paths`, already excluded from the loop watch) and is a designed seam, not v1 scope.

#### Steelman: bi-partite vs. the researcher's B+E spine

Both designs contain the same ingredients; they differ in **which one is load-bearing**, and the difference decides three things.

| Axis | Researcher's B+E (block IDs preserved by the loop; anchors rewritten at commit time) | Bi-partite (immutable record + derived projection) |
|---|---|---|
| What must hold for a note to remain readable | The loop must have preserved `^id` across an LLM rewrite; the commit-time re-anchor hook must have run | Nothing. `git show <commit>:<page>` always works |
| Cost of a rewrite touching N notes | N note-file rewrites ⇒ commit churn, or one commit in which **the loop mutates the user's personal notes** | Zero writes |
| Resolver improvement | Unbackfillable — the original anchor was overwritten | Recompute every projection from unchanged records |
| Where failure lands | On the KB mutation critical path, inside the widened flock span | Nowhere; resolution is a pure read |
| Residual hard-orphan rate (research §Feasibility) | 2–6% | 8–20% |

E's stated advantage is *"match while the old text is still in hand."* **That advantage does not exist here.** E is imported from systems (Hypothes.is, Google Docs) where the pre-image is *not* retained, so the moment of mutation is the only chance to match. Knotica retains every pre-image forever. Commit-time re-anchoring therefore buys latency, not capability — and pays for it with lock-hold time on the KB's most safety-critical span (the widened `vault_mutation_span` that brackets checkout→merge→branch-delete→commit, with crash self-heal). Trading a note-layer convenience against a wedge risk on the KB mutation lock is a bad exchange for a feature that is by charter personal and unscored.

The one thing E genuinely provides is **notification** — "the KB changed under something you cared about," which the research correctly identifies as the most valuable output of the whole layer. That is recovered in full by the post-merge reconciliation pass (below), off the critical path.

Accepting bi-partite means accepting the 8–20% row instead of the 2–6% row. That is the honest cost, and it is acceptable because: (a) orphaning is graded, never fatal (A1) — the note is always readable and always attached at *some* granularity; (b) the research's own reframing holds that most residual orphans are *correct* orphans; (c) it is reversible — block IDs can be added later as an extra selector on new anchors without touching a single stored record.

#### Decision: no `^block-id` injection in v1

**Rejected for v1.** Four reasons, in order of weight:

1. **Coupling inversion.** Injecting `^id` tokens means the personal-notes layer writes into the scored KB corpus. The feature's founding constraint is that notes are independent of and invisible to the KB. A mechanism whose first act is to edit every KB page contradicts the thing it exists to serve.
2. **It makes annotation durability depend on LLM instruction-compliance across every mutating path.** The research names this the single load-bearing risk of its own recommendation. Bi-partite has no equivalent dependency.
3. **`^id` tokens are literal corpus text.** They are chunked, BM25-scored (document length here is *file byte size*, so ~10 bytes × ~30 blocks ≈ 7% length inflation per page, feeding the `b=0.75` length-normalisation term), potentially copied into generated answers the LLM judge reads, and potentially swept into extracted citation quotes. The direction of the effect is knowable; the magnitude is not. A mechanism that improves annotation while degrading KB eval quality is a bad trade.
4. **It is not needed.** Bi-partite already guarantees permanent readability; IDs would only raise the *automatic re-anchor* rate.

**How it would be measured, if revisited (Spike 3, Phase 3 gate).** Two experiments, both on clones, neither through the live gate (an instrument change auto-refreezes the baseline):
- *Preservation*: rewrite N (≥20) content pages through the normal ingest path with `^id` tokens present; count surviving IDs. Below ~95% survival, the mechanism is not worth its contract cost.
- *Eval delta*: run `knotica eval` on the topic's frozen `golden.jsonl` twice — once on a clone of HEAD, once on a clone with `^id` injected into every block — same instrument, same snapshot, same seeds. Compare the composite scalar and each of the three legs (`qa_accuracy`, `citation_validity`, `lint_violations`). **Gate:** any degradation exceeding the loop's own regression tolerance rejects the mechanism outright.

These are Phase-3 gates, not Phase-0 blockers — which removes the prompt's "Spike 1" from the critical path entirely. See § Sequencing.

#### Resolution ladder

Given anchor `(page, commit, quote[, start])` and current HEAD:

| # | Step | Result |
|---|---|---|
| 0 | **Historical resolution.** `read_file_at(commit, page)`; locate `quote` (disambiguated by `start`). Always available. Failure here means a hand-edited or corrupt anchor, reported as `anchor-invalid` — a data-integrity error, not an orphan. | derives `end`, `prefix` (32 chars), `suffix` (32 chars), enclosing heading |
| 1 | `page` missing at HEAD (deleted or renamed) → **`orphaned`, granularity `topic`**. Stop. | |
| 2 | `quote` occurs verbatim in `HEAD:page` at the recorded offset → **`exact`**, score `1.0`. Stop. | |
| 3 | `quote` occurs verbatim at a different offset (>1 occurrence resolved by proximity to the historical offset) → **`shifted`**, score `1.0`. Stop. | |
| 4 | **Keyword candidate generation** (MSR): pick the ≥3 rarest words of `quote` within `HEAD:page` (frequency 1, relaxing to 2, 3, … until three are found; proper nouns always eligible). Seed a window at every occurrence, extended to sentence boundaries, capped at 2× the quote length. | candidate set |
| 5 | **Score each candidate** with Hypothesis weights — quote 50 / prefix 20 / suffix 20 / position 2, normaliser 92 — using `difflib.SequenceMatcher.ratio()` for the three similarity terms and `1 − offset_delta/len(text)` for position. Take the argmax. | `score ∈ [0,1]` |
| 6 | `score ≥ guess_threshold` → **`fuzzy`**, granularity `span`. Stop. | |
| 7 | Historical enclosing heading still present at HEAD → **`orphaned`, granularity `section`**, `best_guess` = the section span, score **clamped just below `guess_threshold`** so it is always reviewed and never silently placed (MSR's finding that users reject silent low-confidence placement). Stop. | |
| 8 | `score ≥ complete_orphan_threshold` → **`orphaned`, granularity `page`**, `best_guess` = the argmax span. | |
| 9 | otherwise → **`orphaned`, granularity `page`**, `best_guess: null` (MSR: single-keyword garbage guesses rated 1.0/7 — showing them is worse than showing nothing). | |

**Fusing A and C rather than running them in series.** MSR keyword anchoring earns its keep as a *candidate generator* — it finds spans that whole-string search cannot reach after a paraphrase. Hypothesis's weights earn their keep as a *scorer* — tuned, published, and dominated by quote similarity (50:40:2), which is the right prior. Running them as two independent scorers with two threshold sets doubles the tuning surface for no gain. One generator, one scorer, two thresholds.

**Thresholds.** `guess_threshold = 0.75`, `complete_orphan_threshold = 0.35`, both in a `[notes]` config table mirroring `[loop]`/`[gapfill]`. Start the guess threshold **high** per MSR (more orphans, more review, fewer silent misplacements) and let the user lower it. Adaptive auto-tuning from accepted guesses is explicitly out of v1 scope.

**LLM adjudication (F) — where it is permitted.** Only in the band `[complete_orphan_threshold, guess_threshold)`; only when a client is configured and `[notes] adjudicate = true`; only in the **human-facing review surface**, never during read-path resolution; and its output is a *label* (`moved` | `retracted` | `unknown`) plus one sentence of rationale attached to the review item — never an applied re-anchor. Its unique value is the judgement no string algorithm can make: *this passage was not moved, it was retracted*. Offline installs traverse the identical deterministic ladder and simply see no commentary (AC-10).

#### Where re-anchoring runs

**Lazily at read time, always. Never on the mutation critical path. Plus a read-only post-merge reconciliation pass for notification.**

| Option | Verdict |
|---|---|
| On the mutation critical path (E) | **Rejected.** The flock span was recently *widened* to bracket entire git-mutation sequences with crash self-heal; adding note resolution inside it lengthens the hold and introduces a failure mode in the loop's merge path on behalf of an explicitly unscored personal feature. Its stated benefit (pre-image availability) is already free in git. |
| Lazily at read time | **Chosen.** Zero commits, zero lock, zero loop coupling; re-runnable against any HEAD; resolver improvements apply retroactively to every note. Cost: O(page length) per anchor per read — a single `str.find` on the happy path. |
| Loop-side pass (on the clone) | **Rejected as the primary.** The clone is throwaway; a candidate may be refused and quarantined to `loop/x/*`, so re-anchoring against it is speculative work against text that may never exist on the default branch. |
| Loop-side pass (**after merge to default**), read-only | **Adopted as a complement.** Recovers the notification value: after a merge, diff the touched paths, resolve only the anchors on those pages, and report status transitions (`exact → fuzzy`, `* → orphaned`) into the notes review queue. Takes no vault lock, writes no note file. Degraded-mode safe: if it never runs, read-time resolution still gives the correct answer — the user just isn't proactively told. |

### Storage, layout, and the folder-family abstraction

#### Layout (D1, accepted)

```
<vault-root>/
  notes/
    <topic>/
      20260729-193045-sleep-replay.md
```

`notes/` joins `RESERVED_TOP_LEVEL_NAMES`. Filenames are `<YYYYMMDD-HHMMSS>-<slug>.md`: chronologically sortable in Obsidian, collision-free, and — critically — **never renamed**, so note-to-note links stay stable forever.

**Why D1 is right despite Option B being cheaper.** The vault-model research favours `<topic>/notes/` on cost-to-implement grounds. That reading is correct about the mechanics and wrong about the risk profile. Under Option B, notes land inside `iter_page_paths(store, topic)` — the exact primitive all four scoring surfaces key off. Every one of the eval-gap research's checklist items 1–8 becomes a filter that must be *added and then kept*, and a single missed filter is a **silent** score contamination. Under D1, notes are outside `<topic>/` and every one of those surfaces excludes them by never looking — the same way `sources/` is *included* only by an explicit second scan directory. **Exclusion-by-omission is a structural guarantee; exclusion-by-filter is a maintenance obligation.** Given health guard "no score contamination," the guarantee wins. Confirmed against code: `_count_content_pages`, `entity_pages`, `_content_page_paths` and `_scope_dirs(topic)` all scope to `<topic>/` and are free under D1.

#### The one leak omission does not cover

`lint._vault_link_map` walks the **whole vault** (`iter_page_paths(store)` with no directory argument), and `harness.py:825` calls `lint_vault(store, topic)` whose `_check_orphans` builds `inbound_targets` from that whole-vault map. Verified directly: **a wikilink from `notes/<topic>/x.md` to a KB page would de-orphan that page, suppress a `PAGE_ORPHANED` violation, and move the `lint_violations` leg of the eval scalar.** This is the single real contamination vector under D1 and it is not hypothetical.

Fix: `_check_orphans` counts only links whose **source** is in `SCORED_FAMILIES`. One predicate, one call site.

#### The folder-family decision

**Introduce a first-class folder-family concept — scoped tightly.**

`src/knotica/core/vault_layout.py`, a ~60-line leaf module (no `core.*` imports, so it cannot create a cycle):

```
Family        = Literal["page", "source", "note"]
SOURCES_DIR   = "sources"
NOTES_DIR     = "notes"
TOP_LEVEL_FAMILY_DIRS  = frozenset({SOURCES_DIR, NOTES_DIR})
SCORED_FAMILIES        = frozenset({"page", "source"})
RESERVED_TOP_LEVEL_NAMES = frozenset({...,"sources","notes",...})   # THE declaration
family_of(rel_path) -> Family
topic_of(rel_path)  -> str
```

**Call sites that move** (declaration → import; behaviour-preserving):

| # | Site | Change |
|---|---|---|
| M1 | `core/lint.py:56` `RESERVED_TOP_LEVEL_NAMES` | import — **collapses the duplication** (verified byte-identical to `vault_scaffold.py:43`; the docstring already falsely claims it is "single source of truth") |
| M2 | `core/vault_scaffold.py:43` `RESERVED_TOPIC_NAMES` | import, keeping the public name as an alias |
| M3 | `core/lint.py:72` `_SOURCES_DIR` | import |
| M4 | `search/ripgrep.py:58,290-302` `_SOURCES_DIR` + `_classify` | `_classify` delegates to `family_of`/`topic_of`; fixes the silent topic misattribution (`notes/x/y.md` would otherwise classify as topic `"notes"`, kind `page`) |

**Call sites that use the new predicate:**

| # | Site | Change |
|---|---|---|
| U1 | `core/lint.py` `_check_reserved_names` | `name != _SOURCES_DIR` → `name not in TOP_LEVEL_FAMILY_DIRS` |
| U2 | `core/lint.py` `_check_orphans` | filter `inbound_targets` by source family ∈ `SCORED_FAMILIES` |
| U3 | `core/loop.py` `_content_changed_since` | `if family_of(path) == "note": continue` |

**Call sites that deliberately stay unchanged:** `store_source.py:31`, `source_ingest.py:101`, `guillotine/search.py:13`, `evals/citations.py:45`, `core/page.py:210`, `okf/index.py:73-79`, `golden_review.py:72`, `vault_scaffold.py:128`. These are *sources-specific logic* (where a stored source lives, how a citation key resolves), not folder-family dispatch. Migrating them is a pure-cost refactor with no consumer. **Stay Surgical.**

Net: **one new module, four moves, three uses, and the codebase ends with fewer literal declarations than it started with.** Health guard "no fifth hardcode" is satisfied — and the pre-existing fourth is retired.

#### Notes frontmatter schema

Flat scalars only — inside knotica's strict YAML subset (`page.py:255-276`: no nested maps, no block-scalar values) and trivially Obsidian-native:

```yaml
---
type: note
schema_version: 1
topic: agentic-systems
intent: reflection        # reflection | dispute | gap | question
created: 2026-07-29
updated: 2026-07-29
status: active            # active | archived
tags: [memory, consolidation]
---
```

Deliberately absent: `confidence`, `sources`, `supersedes`/`superseded_by`. A personal reflection has no confidence rating and cites nothing. Notes are exempt from `FRONTMATTER_MISSING`/`FRONTMATTER_FIELD`, `CITATION_UNRESOLVED`, `INDEX_MISSING_ENTRY` and `PAGE_ORPHANED` **by construction** — they are never members of `_content_page_paths`, because `_topic_directories()` excludes reserved names and `notes` is now reserved. No lint exemption list is added (A4 satisfied without new machinery).

#### Anchors in the body, not the frontmatter

The anchor of record cannot live in frontmatter: a list of anchors is a list of maps, which knotica's strict parser rejects. It lives in the body as an Obsidian callout — which is better anyway, because it is what the user wants to *see*:

```markdown
> [!quote] [[agentic-systems/agent-memory]] `@9f3c1e2` `#118`
> trading recall precision for storage economy

Same as the sleep-replay literature — does the loop know that?
```

One callout = one anchor. N callouts = N anchors (A6). The wikilink target is vault-root-relative, so **cross-topic anchors are free** — the note's `topic` frontmatter field is its *filing* location, not a constraint on where it may point. The callout renders natively in Obsidian, is hand-editable, and the wikilink produces a real backlink on the KB page's backlinks pane — so opening a KB page in plain Obsidian shows your marginalia with no tooling at all.

#### The notes graph

**There is no second graph.** Notes use ordinary `[[...]]` wikilinks (to KB pages and to each other) and therefore join the one existing link graph — which is exactly what makes Obsidian backlinks work. Separation is achieved not by building a parallel structure but by **scoping the one consumer where graph membership has consequences** (`_check_orphans`, U2). Everywhere else, note→note and note→page edges are inert: notes are never content pages, so no orphan/index/frontmatter check ever takes a note as its subject.

This is the Balanced-Coupling answer to A6's "own graph": the requirement is *"note edges must not affect KB quality judgements,"* not *"a second graph data structure must exist."* Building the latter to satisfy the former would be ceremony.

#### Retrieving notes for the user

**Anchor-driven, not search-driven, in v1.** "Show me my notes on this page / in this topic" is answered by listing `notes/<topic>/`, parsing anchors, and filtering by target page — no BM25, no corpus change, nothing to exclude. `_scope_dirs` is untouched.

Full-text search over notes is deferred. When it arrives, the constraint is fixed now: the search backend gains an explicit `families: frozenset[Family]` parameter **defaulting to `SCORED_FAMILIES`**. The eval runner and query engine take the default and therefore can never see notes; only an explicitly note-scoped call passes `{"note"}`. Opt-in inclusion, never opt-out exclusion.

### Loop interaction

| Question | Answer |
|---|---|
| Does a note write wake the loop? | **No.** `_content_changed_since` gains one `continue` branch keyed on `family_of(path) == "note"`, exactly mirroring the existing `.knotica/` bookkeeping branch. `log.md` is already skipped, so the note transaction's log append is already inert. This affects `observe_default()` only — the candidate-gate path is separately triggered by an explicit `loop/c/*` publish and is correctly unaffected. |
| Does the loop trigger re-anchoring? | **Not on the clone.** A clone's rewrite may be refused and quarantined to `loop/x/*`; re-anchoring against text that may never reach the default branch is speculative work. |
| When, then? | **After merge to the default branch**, as a read-only reconciliation: diff the merge's touched paths, resolve only anchors on those pages, report status transitions to the notes review queue. No lock, no note-file write, no commit. If it is skipped or fails, read-time resolution is unaffected — it is purely a notification accelerator. |
| Does the loop ever write a note file? | **Never.** Only `capture_note` (human/client-initiated) and `reanchor_note` (human-accepted) write into `notes/`. |

### The eval bridge

#### Correction to the research's "cheapest path"

The eval-gap research concludes that `bootstrap(pages=[...])` / `bootstrap_trainset(pages=[...])` is the cheapest route for notes → eval questions. **Verified against source: this does not work.** Both call sites compute `entity_pages(store, topic)` first and then *intersect* with `pages` (`golden.py:559-561`, `train_bootstrap.py:93` + `_filter_pages`). `entity_pages` walks `iter_page_paths(store, topic)`. A note at `notes/<topic>/x.md` is not in that set, so `pages=[note_path]` selects **zero** pages and returns empty. The `pages` parameter can only *narrow* the KB page set; it cannot introduce a path.

This is a genuine correction, and it also happens to point at the right answer.

#### Decision: `curate_example` → trainset; golden deferred

**The bridge is `core.operations.curate_example.curate_example(query, pages_used, answer, verdict, notes)`.**

- The client-as-brain already holds the note's question and can answer it from the KB — no synthesis call is needed at all.
- `pages_used` is the **anchored KB page path(s)**, not the note path. This is the correct semantics: an eval question must be answerable from the KB corpus, not from a personal note. It is also already accepted — `pages_used` is unvalidated free-form strings.
- Terminal shape is a `QARecord` with `source: curate_example` — exactly what a trainset entry is. One `VaultTransaction`, one commit, idempotent by `(query, answer, verdict)` fingerprint.
- Human gate = explicit confirmation before the call. No new queue, no new record type, no sixth JSONL file.

**Golden (`held_out`) promotion is deferred out of v1**, for two independent reasons:
1. **D-MERIT bias.** Notes are a *biased, sparse* sample — users annotate what surprised or annoyed them. That is an excellent hard-negative / regression-probe source and a dangerous headline benchmark.
2. **They are mutually exclusive anyway.** `freeze()` runs `verify_disjoint_from_trainset` and raises `GoldenSetContaminationError`. A question routed to `qa.jsonl` can *never* subsequently enter `golden.jsonl`. So the destination is a one-way choice that must be made deliberately — not a default.

If golden promotion is later wanted, the smallest correct path is a writer that stages a candidate dict into `golden.staging.jsonl` (reusing `_write_staging`'s shape) and lets the **existing** `golden_review` load/save + `freeze()` human gate do the rest. That is a Phase-4 item, not v1.

This narrows D3 ("approved candidates enter dev/golden") to **trainset-first, golden-deferred**. Registered as an objection with reasons above; the human-review-gate half of D3 is honoured in full.

#### Gap origin: reuse `reported`, do not add a fourth

`intent: dispute` / `intent: gap` notes that a human elects to file become gaps with **`origin: reported`** — the same class `gap_report` already produces. A note with `intent: gap` *is* a user-reported gap; only the capture surface differs, and a capture surface is not a taxonomy. Provenance is preserved without a schema change: `GapRecord.reported_reason` carries `note:<notes/topic/file.md>#<anchor-id>`. A fourth origin would cost a `GAP_ORIGINS` member, a `_ORIGIN_QA_ID_PREFIX` entry, a new writer, and every origin-switching consumer — for a distinction that a free-text pointer already makes. Reversible: if note-origin gaps later prove to behave differently, split then. (The external research's §7 reaches the same conclusion independently.)

#### Orphaned notes: no auto-filed gaps

**An orphaned note does not automatically file a gap.** It becomes a **review-queue item**. Reasons:
1. Auto-filing would let the personal layer write into the KB's gap pipeline with no human in the loop — a direct breach of D2's opt-in-per-note boundary.
2. It creates a feedback loop where every rewrite generates gaps proportional to note density on the touched page. Notes would become a rate-limiter on the loop's own healing.

Instead, the review surface offers **"file as gap"** as a one-click human action, routed through the existing `report_gap` path, and offered **only** for `intent ∈ {dispute, gap, question}` — never for `reflection`. This makes D2's per-note intent do real, visible work rather than being decorative metadata, and it preserves exactly the regression-probe signal the research identifies (research open question 6: "probably yes as a probe, no as an eval headline metric" — this is the probe half, with the human as the gate).

### Worked example — one note through a page rewrite

**t₀ — capture.** HEAD = `9f3c1e2`. `agentic-systems/agent-memory.md` contains, under `## Consolidation`:

> Episodic traces are compressed into semantic summaries during idle periods, trading recall precision for storage economy.

Claude Desktop synthesises a paragraph about memory consolidation; the user reacts. The client calls capture with `page=agentic-systems/agent-memory.md`, `quote="trading recall precision for storage economy"`, `intent=question`. The server finds the quote once at offset 118, records `commit: 9f3c1e2`, `provenance: verified`, and writes `notes/agentic-systems/20260729-193045-sleep-replay.md` in one commit (`knotica(note_capture): agentic-systems — sleep-replay`). The loop's `_content_changed_since` skips the path; no eval fires.

**t₁ — read.** Quote found verbatim at 118 → **`exact`**, score 1.0. Zero writes.

**t₂ — a source ingest merges, adding a paragraph above.** Quote found verbatim at 402 → **`shifted`**, score 1.0. The historical offset 118 is used only as a proximity tie-break. Zero writes.

**t₃ — the loop merges a rewrite of the section:**

> During idle periods the agent replays episodic traces and distils them into semantic summaries, deliberately giving up recall precision in exchange for storage economy.

Exact fails. Keyword generation picks the page-rare `recall`, `precision`, `economy`, seeds a window, extends to sentence bounds. Hypothesis scoring against the stored quote: quote ≈ 0.72, prefix ≈ 0.30, suffix ≈ 0.95, position ≈ 0.60 → normalised ≈ **0.78 ≥ 0.75** → **`fuzzy`**, granularity `span`, pointing at the new text. Still zero writes to the note.

The post-merge reconciliation notices the `exact → fuzzy` transition on a touched page and appends one row to the review queue.

**t₄ — human review.** The surface shows the historical text (`git show 9f3c1e2:agentic-systems/agent-memory.md`, always available) beside the current span. Accepting changes nothing — the projection is already right. Correcting **appends a second anchor** (`@c7d1a90` + the corrected quote); the original anchor is untouched. The resolver then prefers the newest verified anchor and keeps the older one as history (AC-09).

**t₅ — the claim is retracted.** The guillotine demotes it; the paragraph is removed. Exact fails; best candidate scores 0.21 < 0.35 → **`orphaned`**, granularity `page`, no guess. The queue reports: *your note's passage no longer exists in `agent-memory.md`.* The provoking text is still one `git show` away and is rendered inline. Because `intent: question` is an opted-in intent, "file as gap" is offered; the human clicks; `report_gap` files `origin: reported`, `reported_reason: note:notes/agentic-systems/20260729-193045-sleep-replay.md#a1`. Had the intent been `reflection`, the note would simply sit — readable, orphaned, harmless, and never touching the KB.

At no point did the loop write to `notes/`. At no point did a note write wake the loop. At no point did a note appear in a scored corpus. And at every point the reflection and its provoking text were readable.

### Decisions

See ADR fragments (§ ADR Fragments below) for the full trade-off records. Summary:

| Decision | Chosen | Chief alternative rejected |
|---|---|---|
| Anchor model | Bi-partite: immutable record `(page, commit, quote[, start])` + derived projection | B+E spine with block IDs and commit-time anchor rewriting |
| `^block-id` injection | Not in v1; gated behind a two-part spike in Phase 3 | Inject now as the durability spine |
| Re-anchor locus | Read-time lazy + read-only post-merge reconciliation | On the widened mutation critical path |
| Storage | `notes/<topic>/` at vault root (D1) | `<topic>/notes/` (cheaper, but exclusion-by-filter) |
| Folder family | New `core/vault_layout.py`; 4 moves + 3 uses; 8 sources-specific literals untouched | Full de-duplication of every `"sources"` literal |
| Notes graph | One graph; scope the one consumer with consequences | A second parallel graph structure |
| Eval bridge | `curate_example` → trainset; golden deferred | `bootstrap(pages=[note])` (verified non-functional); the `SuggestionRecord` queue |
| Gap origin | Reuse `reported` + `reported_reason` pointer | A fourth `note` origin |
| Orphan → gap | Human one-click, intent-gated | Automatic filing |

## Codebase Readiness

### Pre-Refactor Assessment

**Outcome: `fold-into-Prerequisites`.**

Phase 2 surfaced three real structural issues: `RESERVED_TOP_LEVEL_NAMES` declared twice with byte-identical contents (`vault_scaffold.py:43`, `lint.py:56` — the latter's docstring falsely claiming single-source-of-truth status); six scattered `_SOURCES_DIR = "sources"` module constants plus several inline literals; and `search/ripgrep.py:_classify` deriving topic positionally, which silently misattributes any new top-level folder. All three are genuinely in the feature's path.

The remedy is nonetheless **small and inseparable from the feature**: one ~60-line leaf module, three import swaps, one function delegation — four files, mechanical, covered by existing lint and search tests. Independence is *low*: extracted on its own, `vault_layout.py` would have no consumer and no behavioural justification; it only earns its place because `notes` needs a second family. Magnitude low + independence low ⇒ fold inline as the first prerequisite step, not a separate sub-pipeline. This is also a design-only pass, so emitting a `PRE_REFACTOR_PLAN.md` would dispatch a mini-pipeline for implementation the user has not yet authorised.

**Affected `td-NNN` rows:** none. The active ledger rows (`td-001`…`td-012`) cover test hermeticity, file-size ceilings (`harness.py`, `loop.py`, `records.py`), ruff debt and CLI clone-root surfacing — none overlaps the folder-family scope. No status flips.

### Structural Issues

1. **Duplicated reserved-name declaration** (`vault_scaffold.py:43` ≡ `lint.py:56`) — kept in sync by convention only. Adding `notes` to one and not the other is a live and silent failure mode. Retired by M1/M2.
2. **No folder-family concept** — `"sources"` is special in six module constants and several inline literals; `_classify` classifies by position. Generalised, not duplicated, by `vault_layout.py`.
3. **Whole-vault link map feeding a topic-scoped orphan check** (`lint.py:158,176` + `harness.py:825`) — the one contamination vector D1 does not close by omission. Verified directly. Closed by U2.
4. **`file-size ceilings`** — `lint.py` and `loop.py` (`td-008`, in-flight, 1087 lines) are already over the project's 800-line ceiling. This feature's edits to both are *small* (an import swap plus a predicate in `lint.py`; one `continue` in `loop.py`) and must stay that way: put all new notes logic in new modules under `core/notes/`, never inline in `loop.py` or `lint.py`.
5. **No `[notes]` config precedent** — but two exact templates exist (`loop_cadence_config.py`, `gapfill_config.py`); follow them verbatim.

### Prerequisites

**P0 — folder-family extraction (must land before any notes code).**
- Create `core/vault_layout.py` with the single `RESERVED_TOP_LEVEL_NAMES` (including `notes`), family constants, and `family_of`/`topic_of`.
- M1–M4: swap declarations for imports in `lint.py` (×2), `vault_scaffold.py`, `ripgrep.py`; delegate `_classify`.
- U1: `_check_reserved_names` exempts `TOP_LEVEL_FAMILY_DIRS`.
- U2: `_check_orphans` filters inbound links by source family.
- Behaviour-preserving except U1/U2 and the `notes` membership; existing lint and search tests are the safety net, extended with a characterisation test proving AC-04 (scalar identical with and without a `notes/` tree).
- Document `notes/` in `vault-template/SCHEMA.md`.

**P0b — loop watch exclusion** (`_content_changed_since`, U3) must land in the same phase as the first note write. A note write that wakes the loop bills money.

**No API version drift** was detected: this design introduces no new external dependency and no new external API surface.

### Existing Patterns

- **Mutation**: exactly one `VaultTransaction` per operation, all slow work before the lock, whole-file writes only, `_OP_RE`-conformant op names, `knotica(<op>): <topic> — <title>` commit subject. `capture_note`/`reanchor_note` follow `curate_example.py` as the template (dec-008).
- **Config**: `[notes]` table mirroring `core/loop_cadence_config.py` — all-defaults byte-identical to no-config.
- **Read-only analysis returning data, not exceptions**: `core/lint.py`'s `Violation` list is the model for the resolver's projection records.
- **Human gates**: the shared decision-envelope shape `{decision_id, summary, context, options, provenance, diff, reason_required}` (`tests/test_decision_envelope.py`) is the convention any notes review surface should adopt — **not** the two-phase `confirm_nonce`/`estimated_cost` billing envelope, which is a different animal.
- **Reserved-folder precedent**: `sources/` is included in search only by an *explicit* second scan dir — the exact shape `notes/` should invert (never included by default).

## Constraints for the interface-designer

Stated explicitly, per the concurrency protocol. These are behavioural contracts, not UI opinions:

1. **Capture is one-shot and cannot fail on an unverifiable quote.** If the server cannot find the quote, it stores the note with `provenance: page` and *reports* the degradation. No confirm-retry round-trip on the capture path (capture friction is the feature's life-or-death variable).
2. **The default retrieval path must never include the `note` family.** Any notes-search surface must pass an explicit family selector; there is no "exclude notes" flag, only an "include notes" one.
3. **Every orphan at or above `complete_orphan_threshold` must ship with a best guess and with the historical text rendered.** MSR's strongest UX finding: showing a guess dramatically speeds review even when the user disagrees with it; showing a bare gravestone does not.
4. **"File as gap" is a human action, offered only for `intent ∈ {dispute, gap, question}`.** Never automatic, never offered for `reflection`.
5. **There is no "edit anchor" operation.** Correcting an anchor appends a new one.
6. **A note captured by tool and a note hand-written in Obsidian must be indistinguishable to every read path.** The file format is the contract, not the tool.
7. Notes review surfaces should use the three-human-gate decision envelope, not the billing envelope.

## Proposed deltas to `.ai-state/DESIGN.md` and `docs/architecture.md`

Not applied this pass (nothing is built). To apply when implementation lands:

**`.ai-state/DESIGN.md`**
- §3 Components: add `core/vault_layout.py` (Status: Planned), `core/notes/{anchor,resolve,store}.py` (Planned), `core/operations/{capture_note,reanchor_note}.py` (Planned), `core/notes_config.py` (Planned).
- §5 Data Flow: add the notes capture flow (client → verify → one commit) and the resolution flow (read HEAD + historical blob → projection, no write), plus the read-only post-merge reconciliation edge.
- §7 Constraints: add "the `note` family is excluded from every scoring surface by omission; inclusion is opt-in only" and "note anchors are append-only; the loop never writes into `notes/`."
- §8 Decisions: reference the three ADR fragments below.

**`docs/architecture.md`** — no change until components reach Status `Built`; the developer guide is a strict subset of DESIGN.md's Built set.

**`vault-template/SCHEMA.md`** — document `notes/<topic>/` as a reserved, unscored folder family with the note frontmatter schema and the `> [!quote]` anchor convention (needed so hand-authored notes are well-formed).

## Sequencing

Four phases, each independently shippable, each delivering value alone.

### Phase 0 — Foundations (no notes yet)
`vault_layout.py` + M1–M4 + U1/U2/U3 + SCHEMA.md documentation.
**Ships value alone:** retires the duplicated reserved-name declaration (a real latent bug), fixes `_classify`'s positional topic derivation, and closes the note-de-orphans-a-page contamination vector *before* any note can exist.
**Exit:** AC-14 and AC-04 (with an empty/synthetic `notes/` tree) pass.

### Phase 1 — Capture + page-level anchoring (the value floor)
`capture_note`, note file format, `core/notes/anchor.py`, listing notes for a page/topic, the `[notes]` config table. **Anchoring is verified-span-or-page-level only** — steps 0–3 of the ladder (`exact`/`shifted`/`orphaned@page`). No fuzzy matching, no keywords, no thresholds.
**Ships value alone:** the user can capture reflections in-flight, they land durably in git, they show up in Obsidian's backlinks pane on the KB page, the historical text is permanently readable, and nothing can contaminate a score. This is a complete, useful product even if anchoring never improves.
**Exit:** AC-01, AC-02, AC-03, AC-04, AC-05, AC-08, AC-13.

### Phase 2 — The recovery ladder and the review loop
`core/notes/resolve.py` (keyword generation + Hypothesis scoring + two thresholds), `fuzzy`/`orphaned`-with-guess statuses, `reanchor_note` (append-only), the post-merge reconciliation pass, the review surface, "file as gap" (human, intent-gated), and the `curate_example` eval bridge.
**Exit:** AC-06, AC-07, AC-09, AC-10, AC-11, AC-12.

### Phase 3 — Optional accelerators, both spike-gated
- **Spike 3a (block-ID preservation):** rewrite ≥20 pages with `^id` present; count survivors. Gate: ≥95%.
- **Spike 3b (block-ID eval delta):** two `knotica eval` runs on clones — HEAD vs. HEAD+injected-IDs — same frozen `golden.jsonl`, same instrument, same snapshot. Gate: no leg degrades beyond the loop's regression tolerance.
- **Spike 2 (`search_result` citations):** can an MCP tool result carry `search_result` blocks to Claude Desktop, and can a later tool call read the citation metadata back? Settles P1-a vs P1-b for *capture precision only*.
- Also: LLM adjudication of the middle band; notes full-text search behind an explicit `families=` selector.

### Phase 4 — Deferred
Golden-set promotion via a staging writer behind the existing `freeze()` gate; adaptive threshold tuning from accepted guesses; a persisted projection index under `.knotica/` if read latency ever justifies it.

### Objection registered against the prompt's spike sequencing

The prompt frames Spike 1 (block-ID preservation) and Spike 2 (`search_result` citations) as **gating** the whole design. Under this architecture **neither gates anything**, and saying so is the point of raising it:

- **Spike 1 is not gating** because block IDs are not the durability spine. Bi-partite guarantees permanent readability without them; IDs would only raise the automatic re-anchor rate. Running the spike first would spend billed eval runs to decide a Phase-3 accelerator before Phase 0 exists. Deferred to Phase 3, with a sharper two-part gate (preservation **and** eval-delta) than the prompt's single preservation test — because preservation alone is not sufficient evidence to inject text into a scored corpus.
- **Spike 2 is not gating** because P1-a (provenance-carrying reads) is the design and P1-c (server-side verbatim verification of a client-supplied quote) is the floor, and the floor is what Phase 1 ships. Native `search_result` citations would improve *capture precision* — the fraction of captures landing `provenance: verified` rather than `page` — which is a Phase-3 refinement, not a Phase-0 blocker. It is also owned by the interface-designer's surface, not by this design.

Moving both off the critical path is the single largest schedule saving in this plan: **Phase 0 can start immediately**, with no billed experiment and no unverified vendor capability in front of it.

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| A future scoring surface is added that walks the whole vault (like `_vault_link_map` does) and silently re-admits notes | Medium | High — silent score contamination, the exact failure the design exists to prevent | Ship AC-04 as a **standing characterisation test**: the composite scalar and all three legs must be byte-identical with and without a populated `notes/` tree. It fails loudly the moment a new surface leaks. |
| Fuzzy re-anchoring silently places a note on the wrong span | Medium | Medium — user distrust; corrupted marginalia | Explicit `guess_threshold` (Hypothesis's own library enforces *none* — the floor is ours to choose); start high per MSR; `fuzzy` is always a *labelled* status the UI shows, never silent; corrections append, so a bad projection is never destructive. |
| Read-time resolution becomes slow as notes accumulate | Low | Low | Happy path is one `str.find` per anchor; fuzzy only runs when exact fails. Personal scale is tens–hundreds per topic. Designed seam: a persisted index under `.knotica/` (dot-prefixed ⇒ already invisible to `iter_page_paths` and to the loop watch). |
| `notes/` folder added to a vault that predates the `notes` reserved name → an old `knotica lint` reports `RESERVED_TOP_LEVEL_NAME` | Low | Low | The reserved-name check runs only at whole-vault scope (`if scope is None`), never in the eval path, so no score is affected. P0's U1 exemption lands before any note can exist. |
| A hand-edited note breaks the anchor callout format | Medium | Low | Parse failures degrade to "note with no anchors" — the reflection is still readable and listed. Never an exception; report as data, following `lint.Violation`. |
| `loop.py` (already 287 lines over ceiling, `td-008` in-flight) grows further | Low | Medium — worsens active debt | The loop change is exactly one `continue` branch calling into `vault_layout`. All notes logic lives in `core/notes/`. Non-negotiable. |
| The post-merge reconciliation pass silently stops running | Low | Low | Degraded mode is correct by construction: read-time resolution is unaffected; the user simply is not proactively notified. Never a data-loss path. |
| Notes fill with reflections that are never reviewed (write-only store) | Medium | Medium — the feature quietly dies | Out of this design's control, but the orphan queue is designed as the resurfacing mechanism: "the KB changed under something you cared about" is a genuine notification, not a chore list. Measure adoption before investing in Phase 3+. |
| Cross-topic anchors make `topic` frontmatter misleading | Low | Low | Documented explicitly: `topic` is the note's *filing* location, not an anchor constraint. Resolution is per-anchor. |

## Stakeholder Review

Tier 1 (self-review). Findings folded into the architecture above; the non-obvious ones recorded here.

**Developer lens.** Four phases, each independently shippable; Phase 0 touches four files mechanically with existing test coverage. All new logic lands in new modules, so neither over-ceiling file (`lint.py`, `loop.py`) grows meaningfully. New-code placement is unambiguous: anchors and resolution in `core/notes/`, mutations in `core/operations/`, layout facts in `core/vault_layout.py`.

**Test lens.** The three pure modules (`anchor.py`, `resolve.py`, `vault_layout.py`) are trivially unit-testable with no vault. The resolver is a pure function of `(historical_text, head_text, anchor)` — table-driven tests over the §3.4 edit-class matrix (insert-elsewhere / typo / reorder / paraphrase / split-merge / delete) map directly onto the four statuses. **AC-04 is the load-bearing test** and must be a characterisation test, not a unit test: same vault ± a populated `notes/` tree, assert byte-identical scalar and legs. Offline-parity (AC-10) is testable by running the ladder with the LLM seam absent.

**Operations lens.** No new process, no new dependency, no new external call. Adds one reserved folder name (a vault-template documentation change; existing vaults need no migration since absence of `notes/` is indistinguishable from before). The one operational hazard — the loop firing evals on note writes — is closed in P0b and is a billing concern, so it must land with the first note write, not after.

**Simplicity lens.** The design deliberately *declines* four things the research recommends or implies: a second link graph, block-ID injection, commit-time re-anchoring, and a fourth gap origin. Each declination is recorded with its reason in § Rejected Alternatives. The plan adds one new leaf module and one new subpackage, retires one duplicated declaration, and reuses `curate_example`, `report_gap`, `VaultTransaction`, the `[loop]`/`[gapfill]` config idiom, and the three-gate decision envelope unchanged.

**Unresolved tension.** Developer/test lenses want block IDs (higher automatic re-anchor rate, less review work); the simplicity and score-integrity lenses reject them for v1. Resolved in favour of the latter *and made falsifiable*: Spike 3a/3b in Phase 3 is the pre-registered experiment that would overturn the call.

## Rejected Alternatives

**R1 — Block-ID (`^id`) injection as the durability spine (research option B).** Rejected for v1. It inverts the feature's founding coupling (the unscored personal layer would edit the scored corpus); it makes annotation durability depend on LLM instruction-compliance across every mutating path, which the research itself names as the load-bearing risk; and `^id` tokens are literal corpus text that is chunked, BM25 length-normalised (document length here is *file byte size*), read by the LLM judge, and potentially swept into extracted citation quotes. Bi-partite achieves permanent readability without it. **Revisitable** via Spike 3a+3b (Phase 3), where it would be added as an *extra selector on new anchors*, not as the spine — no stored record would change.

**R2 — Commit-time cooperative re-anchoring on the mutation path (research option E).** Rejected as the primary locus. E's core advantage — "match while you still have the pre-image" — is imported from systems that do not retain pre-images. Git retains every one forever, so E buys latency, not capability, and pays with hold time and a new failure mode inside the recently widened `vault_mutation_span` (which brackets checkout→merge→branch-delete→commit with crash self-heal). Its genuine benefit, notification, is fully recovered by the read-only post-merge reconciliation pass. **Adopted in weakened, read-only, off-critical-path form.**

**R3 — CRDT relative positions (research option G).** Rejected categorically, as the research recommends. Guarantees hold only for edits applied *through* the CRDT; the loop writes whole `.md` files out of band, which yields `null` or nonsense. It would also put a CRDT in the middle of a vault whose entire value proposition is plain markdown + git.

**R4 — Embedding / semantic re-anchoring (research option D).** Rejected for v1. No production annotation system was found doing it and no accuracy data exists; it breaks offline installs; and its characteristic failure — re-anchoring to a *similar* passage that is not *the* passage — is precisely the silent-wrong-anchor mode the two-threshold design exists to prevent. The deterministic ladder is offline-safe and reproducible.

**R5 — `<topic>/notes/` (vault-model research Option B).** Rejected despite being materially cheaper to implement. It places notes inside `iter_page_paths(store, topic)`, converting every one of the nine exclusion-checklist items into a filter that must be added *and kept*; one missed filter is a silent score contamination. Exclusion-by-omission is a structural guarantee; exclusion-by-filter is a permanent maintenance obligation. Health guard "no score contamination" decides it.

**R6 — Full de-duplication of every `"sources"` literal.** Rejected as scope creep. Only four sites are folder-family *dispatch* (two reserved-name declarations, `lint._SOURCES_DIR`, `ripgrep._classify`); the remaining eight encode sources-specific logic (where a stored source lives, how a citation key resolves, how OKF infers a type) and migrating them buys nothing for notes. The health guard says *generalise, don't duplicate* — satisfied: the codebase ends with **fewer** literal declarations than it started with.

**R7 — A second, parallel notes link graph.** Rejected. The requirement is "note edges must not affect KB quality judgements," not "a second graph must exist." Notes use ordinary wikilinks (which is what gives Obsidian backlinks for free) and separation is achieved by scoping the one consumer where membership has consequences — `_check_orphans`. Building a parallel structure to satisfy a scoping requirement is ceremony.

**R8 — Reusing the `SuggestionRecord` queue for the eval bridge.** Rejected on the eval research's evidence, independently confirmed: `SuggestionRecord.candidate` is contractually a `SourceCandidate` payload, the queue's terminal action is a `loop/c/*` source ingest with no path to a `qa.jsonl`/`golden.jsonl` append, and the gap→suggestion join key `(gap_id, source_key)` is a DOI/URL normaliser meaningless for a note-derived question.

**R9 — `bootstrap(pages=[note_path])` as the eval bridge.** Rejected because **it does not work**. Verified at `golden.py:559-561` and `train_bootstrap.py:93`: `pages` is intersected with `entity_pages(store, topic)`, which walks `iter_page_paths(store, topic)`. A note path is not in that set, so the call selects zero pages. The parameter can narrow the KB page set; it cannot introduce a path. This corrects the eval research's stated "cheapest path."

**R10 — A fourth `note` gap origin.** Rejected. A note with `intent: gap` is a user-reported gap; the capture surface differs, the semantics do not. `GapRecord.reported_reason` already carries the note pointer, so provenance survives with no schema change. Reversible if note-origin gaps later prove behaviourally distinct.

**R11 — Auto-filing gaps from orphaned notes.** Rejected. It would breach D2's opt-in-per-note boundary by letting the personal layer write into the KB pipeline unattended, and it creates a feedback loop where every rewrite generates gaps proportional to note density — making notes a rate-limiter on the loop's own healing. Replaced by a one-click, intent-gated human action through the existing `report_gap` path.

**R12 — Golden-set promotion in v1.** Deferred, narrowing D3. D-MERIT's partial-annotation result warns that a biased, sparse annotation sample makes a poor headline benchmark; and `freeze()`'s `verify_disjoint_from_trainset` guard makes trainset and golden mutually exclusive per question, so the destination is a one-way choice that should not have a default. Trainset-first via `curate_example`; golden promotion behind the existing `freeze()` gate in Phase 4.

## ADR Fragments

| Fragment | id | Covers |
|---|---|---|
| `.ai-state/decisions/058-notes-anchor-model.md` | `dec-058` | Bi-partite anchor, no block IDs, resolution ladder, re-anchor locus |
| `.ai-state/decisions/060-notes-storage-folder-family.md` | `dec-060` | `notes/<topic>/`, `vault_layout.py` folder family, graph scoping, frontmatter |
| `.ai-state/decisions/059-notes-eval-bridge.md` | `dec-059` | `curate_example` bridge, golden deferral, `reported` origin reuse, no auto-gap |
