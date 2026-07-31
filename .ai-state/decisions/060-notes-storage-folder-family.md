---
id: dec-060
title: Notes live at vault-root notes/<topic>/ behind a first-class folder-family concept
status: accepted
category: architectural
date: 2026-07-29
summary: Personal notes are stored at vault-root notes/<topic>/ (sibling to sources/) so every scoring surface excludes them by omission; a new core/vault_layout.py collapses the duplicated reserved-name declarations and introduces family_of/topic_of, with four declaration moves and three predicate uses — the eight sources-specific literals stay put.
tags: [notes, vault-layout, folder-family, lint, search, scoring, eval-integrity, balanced-coupling, obsidian]
made_by: agent
agent_type: systems-architect
branch: main
pipeline_tier: standard
affected_files:
  - src/knotica/core/vault_layout.py
  - src/knotica/core/lint.py
  - src/knotica/core/vault_scaffold.py
  - src/knotica/search/ripgrep.py
  - src/knotica/core/loop.py
  - vault-template/SCHEMA.md
affected_reqs: [REQ-07, REQ-08, REQ-09, REQ-12]
dissent: Vault-root notes/ costs four declaration moves and a topic-classifier change that <topic>/notes/ would not, and it makes the note's topic a filing convention rather than a structural fact — a cheaper layout was available and was declined on a risk argument, not a cost one.
---

# Notes live at vault-root `notes/<topic>/` behind a first-class folder-family concept

## Context

The vault currently has no folder-family concept. `sources/` is special only because six module-level `_SOURCES_DIR = "sources"` constants and several inline literals say so, and `RESERVED_TOP_LEVEL_NAMES` is declared **twice with byte-identical contents** — `core/vault_scaffold.py:43` and `core/lint.py:56`, the latter's docstring falsely claiming to be the single source of truth. `search/ripgrep.py:_classify` derives a result's topic *positionally* (`parts[0]`), special-casing only `"sources"`.

Four scoring surfaces feed the eval scalar and all key off a topic-scoped walk: BM25 retrieval (`_scope_dirs(topic)`), golden bootstrap and trainset cold-start (`entity_pages` → `iter_page_paths(store, topic)`), lint content pages (`_content_page_paths`), and the scalar's lint normaliser (`harness._count_content_pages`). Health guards for this feature: notes must be invisible to all of them **by construction, not by a growing filter list**, and adding `notes` must *generalise* the `"sources"` special-casing rather than become a fifth hardcode.

Verified additionally, and not covered by either research pass: `lint._vault_link_map` walks the **whole vault** while `harness.py:825` calls `lint_vault(store, topic)`, so `_check_orphans` builds its inbound-target set vault-wide. A wikilink from a note to a KB page would therefore **de-orphan that page, suppress a `PAGE_ORPHANED` violation, and move the `lint_violations` leg of the eval scalar.**

## Decision

**Layout.** Notes live at `notes/<topic>/<YYYYMMDD-HHMMSS>-<slug>.md`, a vault-root sibling of `sources/<topic>/`. `notes` joins the reserved top-level names. Filenames are timestamp-first, never renamed.

**Folder family.** Introduce `src/knotica/core/vault_layout.py`, a ~60-line leaf module with no `core.*` imports, declaring `Family = Literal["page","source","note"]`, `SOURCES_DIR`, `NOTES_DIR`, `TOP_LEVEL_FAMILY_DIRS`, `SCORED_FAMILIES`, the single `RESERVED_TOP_LEVEL_NAMES` (now including `notes`), and `family_of(rel_path)` / `topic_of(rel_path)`.

**Four declaration moves** (behaviour-preserving): `lint.RESERVED_TOP_LEVEL_NAMES` → import (collapsing the duplication); `vault_scaffold.RESERVED_TOPIC_NAMES` → import alias; `lint._SOURCES_DIR` → import; `ripgrep._SOURCES_DIR` + `_classify` → delegate to `family_of`/`topic_of`, which also fixes the silent topic misattribution a new top-level folder would otherwise cause.

**Three predicate uses:** `lint._check_reserved_names` exempts `TOP_LEVEL_FAMILY_DIRS` (generalising the existing `!= _SOURCES_DIR`); `lint._check_orphans` counts only inbound links whose *source* is in `SCORED_FAMILIES`; `loop._content_changed_since` skips the `note` family with one `continue`, mirroring the existing `.knotica/` bookkeeping branch.

**Eight sources-specific literals stay unchanged** — `store_source.py`, `source_ingest.py`, `guillotine/search.py`, `evals/citations.py`, `page.py`, `okf/index.py`, `golden_review.py`, `vault_scaffold.py:128`. They encode where a stored source lives and how a citation key resolves, not folder-family dispatch.

**Notes frontmatter** is flat scalars only — `type: note`, `schema_version`, `id`, `topic`, `intent` (`reflection|dispute|gap|question`), `created`, `updated`, `status`, `tags` — inside knotica's strict YAML subset and Obsidian-native. `id` equals the filename stem exactly. `type`, `id`, `topic` and `created` are required on read; every other field defaults (`schema_version`→`1`, `intent`→`reflection`, `updated`→`created`, `status`→`active`, `tags`→`[]`), so a hand-authored note needs only four fields and a body. `confidence`, `sources` and `supersedes` are deliberately absent. Notes are exempt from the core page contract, `INDEX_MISSING_ENTRY` and `PAGE_ORPHANED` **by construction**: `_topic_directories()` excludes reserved names, so a note is never a member of `_content_page_paths`. No lint exemption list is added.

**Anchors live in the note body** under an `## Anchors` heading, as a markdown list — one bullet per anchor (a list of maps is not expressible in knotica's strict frontmatter parser, and a body list is what the user wants to see anyway, is clickable in Obsidian, and is something a human appends to by hand without thinking). Each bullet's wikilink target is vault-root-relative, so multi-anchor and cross-topic anchors are free; `topic` is the note's filing location, not an anchor constraint. Full bullet grammar and the section-detection rule are specified in `dec-058`.

> **Amended 2026-07-29, during Phase 1 implementation.** Originally specified as `> [!quote]` callouts; restated as a `## Anchors` markdown list for the reasons recorded in `dec-058`'s own amendment note (the callout cannot carry per-anchor fidelity or the append-only supersession history). The `id` frontmatter field was likewise absent from the original field list and is now named explicitly, along with the required-vs-defaultable split. Neither change touches this decision's substance — the layout, the folder family, the four declaration moves, the three predicate uses, and the graph-scoping call all stand as written.

**There is no second link graph.** Notes use ordinary `[[...]]` wikilinks and join the one existing graph — which is what produces Obsidian backlinks on the annotated KB page for free. Separation is achieved by scoping the single consumer where graph membership has consequences (`_check_orphans`).

**Notes retrieval is anchor-driven in v1** — list `notes/<topic>/`, parse anchors, filter by target page. `_scope_dirs` is untouched. When full-text notes search arrives, the search backend gains a `families` parameter **defaulting to `SCORED_FAMILIES`**: opt-in inclusion, never opt-out exclusion.

## Considered Options

### Option A — vault-root `notes/<topic>/` with a folder-family module (chosen)

- **Pro** — every topic-scoped scoring surface excludes notes by *never looking*, exactly as `sources/` is *included* only via an explicit second scan directory. A structural guarantee, not a maintained filter list.
- **Pro** — the only leak (`_check_orphans`' vault-wide inbound set) is one predicate at one call site.
- **Pro** — retires a live drift hazard: the codebase ends with **fewer** literal declarations than it started with.
- **Pro** — notes are independent of any single topic's lifecycle; a topic can be reorganised without touching the user's personal files.
- **Con** — requires four declaration moves and a `_classify` change that the nested layout would not.
- **Con** — the note's topic becomes a filing convention rather than a structural path fact.

### Option B — `<topic>/notes/` nested inside the topic (the vault-model research's cost-preferred option)

- **Pro** — materially cheaper: topic attribution, search scoping and link-graph inclusion all work unmodified; it mirrors the existing `<topic>/reports/guillotine/` precedent.
- **Con** — decisive: it places notes inside `iter_page_paths(store, topic)`, the primitive all four scoring surfaces key off. Every one of the eval research's nine exclusion-checklist items becomes a filter that must be added *and kept*, and a single missed filter is a **silent** score contamination with no error and no test failure.
- **Con** — notes would be subject to the full content-page lint contract (8 required frontmatter fields, index entry, inbound link) unless a further exemption class is added.

### Option C — vault-root `notes/` with no folder-family module (add `"notes"` beside every `"sources"` literal)

- **Pro** — smallest diff.
- **Con** — this is precisely the "fifth hardcode" the health guard forbids, and it leaves the duplicated reserved-name declaration in place while doubling the number of places that must stay in sync.

### Option D — Notes outside the vault entirely

- **Pro** — zero contamination risk by definition.
- **Con** — forfeits git history, Obsidian backlinks, hand-editability in the vault, and the "vault is the only state" invariant. Rejected without further analysis.

## Consequences

**Positive**

- Score integrity is structural: four of the five scoring surfaces need no change at all, and the fifth needs one predicate.
- A pre-existing duplicated declaration and a positional topic classifier are retired as a side effect.
- Notes are hand-authorable in plain Obsidian with no tooling, and annotated KB pages show marginalia in the backlinks pane for free.
- Multi-anchor and cross-topic anchoring fall out of the layout rather than needing machinery.

**Negative**

- A vault created before this change and then given a `notes/` folder will be flagged `RESERVED_TOP_LEVEL_NAME` by an *older* knotica. Low impact: that check runs only at whole-vault scope, never in the eval path.
- `topic_of()` for a note path returns the filing topic, which can disagree with a cross-topic anchor's target — documented, not enforced.
- Eight `"sources"` literals remain, so the generalisation is partial by design and a future reader may mistake that for oversight. Recorded here as deliberate.

## Disconfirmation

> **Amended 2026-07-31, after Phase 2 verification.** Two of this decision's stated counts did not
> survive measurement, and one of its claims is now false. The counts are **three** declaration
> moves, **ten** predicate uses and **nine** sources-specific literals left in place — not the
> four/three/eight recorded above: `lint._SOURCES_DIR` never became an import, and `core/links.py`
> gained a predicate without appearing in `affected_files`. More consequentially, the claim that
> "the codebase ends with **fewer** literal declarations than it started with" is **not true for
> the `notes/` family**: `_NOTES_DIRECTORY_TEMPLATE = "notes/{topic}"` is declared four times
> (`notes/store.py`, `capture_note.py`, and — added in Phase 2 — `reanchor_note.py` and
> `promote_note.py`), while `NOTES_DIR` in `vault_layout.py` has **zero** consumers outside its own
> module. The exact duplication failure mode this decision exists to retire has been re-created for
> the new family and then doubled. No behaviour is wrong today; the risk is the silent divergence
> named in § Context. `RESERVED_TOP_LEVEL_NAMES` itself remains genuinely single-declaration, so
> AC-14 is unaffected. Not fixed in the Phase 2 verification pass — recorded here so the next
> reader is not misled by the summary line.

**Falsifier.** Two observations would make this wrong:
1. A scoring surface is found (or added) that walks the whole vault rather than a topic subtree, as `_vault_link_map` already does. Every such surface converts "exclusion by omission" back into "exclusion by filter" and erodes the entire cost argument for the root layout. If several exist, Option B's cheapness wins because the structural guarantee was never real.
2. Users predominantly want notes to travel with their topic — renamed, archived or exported alongside it. Then the nested layout is the correct ontology and the root layout is an eval-integrity workaround wearing an ontology costume.

**Steelmanned runner-up (Option B, `<topic>/notes/`).** The strongest case: this decision spends four file changes and a classifier rewrite to buy protection against a *hypothetical* future maintainer forgetting a filter — while the codebase already demonstrates that the team writes and keeps such filters correctly (the `.knotica/` sub-cased exclusion in `_content_changed_since`, the overlay-filename skip in `_content_page_paths`, the `_OKF_FRONTMATTER_EXEMPT` set). The exclusion checklist is nine cited items, all known, all testable in one characterisation test — and that test is being written *anyway* under the chosen design, since `_check_orphans` still leaks. So the guarantee is not actually structural; it is one predicate plus a test, exactly like Option B, but with a worse ontology (notes divorced from the topic tree) and a bigger diff. Meanwhile Option B inherits topic attribution, search scoping and the guillotine-reports precedent for free, and its lint exposure is a solved problem with two existing exemption idioms to copy.

**Reversal trigger.** Revisit if the AC-04 characterisation test (scalar byte-identical with and without a populated `notes/` tree) ever fails for a reason *other* than a genuinely new whole-vault surface — i.e. if omission proves not to be doing the work claimed for it. Also revisit if a second unscored family (drafts, journals, scratch) is proposed: two families under `<topic>/` with a shared filter may then be cheaper in total than two root siblings.
