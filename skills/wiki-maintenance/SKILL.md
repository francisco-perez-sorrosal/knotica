---
name: wiki-maintenance
description: This skill should be used whenever a conversation shows the symptoms of being wiki-relevant — even when the user never names knotica or "the wiki": a factual question about something the vault might cover; a user sharing a source, URL, or paper worth capturing; an expressed sense that "the wiki was wrong" or "we're missing X"; or any talk of the self-improvement loop, pending suggestions, or wiki status. It teaches how to detect wiki-relevant conversation, decide whether it is in scope with a cheap scope-check, and route to the right operation (ingest / query / lint / curate) — offering, never silently mutating. The step-by-step protocol for each operation lives in the vault's operation prompts (load via read_protocol).
version: 0.1.0
---

# Knotica Wiki Maintenance

Knotica is an AI-maintained, compounding markdown wiki living in an Obsidian vault
(Karpathy's **llm-wiki** pattern). The MCP server exposes deterministic tools only — **you
are the brain**: you do all the fetching, reading, writing, and synthesizing, guided by the
vault's schemas. This skill teaches the *judgment* for that work. It is not the procedure.

## The prompts carry the protocol; this skill carries the judgment

Each of the four operations ships as a vault operation prompt that carries the **canonical,
authoritative step-by-step protocol**. When you run an operation, follow its prompt — do not
reconstruct the steps from memory or from here:

| Operation | Canonical protocol |
|-----------|--------------------|
| Ingest | `.knotica/prompts/ingest.md` (vault) |
| Query  | `.knotica/prompts/query.md` (vault) |
| Lint   | `.knotica/prompts/lint.md` (vault) |
| Curate | `.knotica/prompts/curate.md` (vault) |

These prompts resolve from the vault per invocation (root defaults, earned topic overrides),
and are the same artifacts DSPy and SIA later optimize — so the protocol is deliberately kept
in one place. This skill never restates their steps; it tells you *when* to reach for each and
*why* the conventions exist.

## Detecting wiki-relevant conversation

The wiki should recede behind ordinary conversation: you route into it on the *symptoms*
of wiki-relevant talk, not on the user naming knotica. Watch for four:

- **A factual question** about something the vault might cover ("what do we know about X?",
  or any question you would otherwise answer from your own memory).
- **A shared source** — the user drops a URL, paper, or document worth capturing.
- **A reported gap or error** — "the wiki was wrong about Y", "we're missing Z".
- **Loop / suggestion / status talk** — anything about candidates, the self-improvement
  loop, pending suggestions, or wiki health.

On any of these, do a cheap **scope-check before routing**: call `wiki_status(view="scope")`
— a committed, deterministic, read-only lookup that returns the topics this vault covers
(and totals). You are the classifier; the server stays dumb. Then:

- **In scope** → route. For a question, prefer `query` (read-only, grounded) over answering
  from memory; if the wiki cannot answer, *offer* `gap_report`. For a shared source, *offer*
  to ingest. Route to *read or offer* only — **never a silent mutation**. Every commit stays
  user-gated.
- **Out of scope** → answer normally. Do not route; the vault does not cover this.

This is detection and routing — *when/whether* to enter an operation. The *how* — the step
sequence for each operation — lives in the vault's operation prompts; load one with
`read_protocol` and never reconstruct its steps here.

## When to reach for which operation

- **Ingest** — new external material must enter the wiki: a paper, doc, or source the user
  wants captured and distilled into entity pages. Ingest both *stores the raw source
  immutably* and *writes schema-conformant pages* from it. Reach for ingest when the goal is
  to grow the knowledge base, not to answer a question.
- **Query** — the user has a question the wiki might already answer. Read-only, no commits.
  Prefer query over answering from your own memory: the value of the wiki is grounded,
  cited answers. If the wiki cannot answer, say so plainly rather than filling the gap silently.
- **Lint** — verify health: mechanical conformance first (the `lint_check` tool), then the
  semantic pass only you can do (contradictions, staleness, schema-spirit violations, missing
  links). Reach for lint after a batch of ingests, when something feels inconsistent, or on a
  periodic sweep. Semantic lint is judgment work — mechanical checks cannot see a contradiction.
- **Curate** — save an interaction as a training example. Ingest and query *already offer this
  at their end* (the flywheel does not fill itself); reach for standalone curate when you want
  to capture an earlier interaction, or when the user explicitly asks to save one.

The operations are not siloed: a query may reveal a contradiction (fold into a lint), an
ingest may supersede an existing page (see supersession below), and every good interaction is
a curation candidate.

## Schema-first discipline

The vault's schemas are the constitution, not a suggestion. **Read the effective schema before
you write or judge anything** — it is the merged root invariants ⊕ topic overlay (allowed
entity types, the page template, required frontmatter, the topic's ingest rule). Overlays
*extend but never contradict* root; divergence is *earned*, so a new topic starts empty and
inherits root defaults.

- Everything you write must conform to the resolved schema — frontmatter fields, entity types,
  page template, wikilink style. Conforming pages are what make the wiki lintable and
  optimizable; a page that ignores the schema is technical debt the next lint will surface.
- Resources are **not** auto-loaded — you fetch the schema yourself (the operation prompts say
  exactly which resource URI and how). Do not assume you already have it.

## Per-operation commit expectations

Every mutating operation is **one atomic unit: write → secret-scrub → one git commit → one
`log.md` append** — this is the audit trail and the rollback boundary. Consequences for how you
work:

- **Never write `log.md` or `index.md` yourself** — the tools maintain both, in the same commit
  as the page. The catalog line comes from the `index_entry` you pass, not from you editing the
  index. (Both are reserved names; a direct write fails.)
- **One commit per meaningful change** — do not batch unrelated page writes expecting a single
  commit; each `write_page` is its own committed unit. Re-sending identical content is a safe
  no-op (no commit).
- Sources are **immutable**: a source key holds one content forever. A defective stored source
  is fixed by re-storing under a suffixed key, never by overwriting.

## Supersession and confidence conventions

These cheap, high-value conventions keep a compounding wiki trustworthy — honor them when you
write and check them when you read:

- **Supersession** — when a new page replaces an older one, mark the relationship in frontmatter
  (`supersedes` / `superseded_by`) and set the old page `status: stale`. A superseded-but-unmarked
  page is exactly what a semantic lint hunts for. When answering a query, prefer the superseding
  page and flag anything drawn from a stale one.
- **Confidence** — the `confidence` frontmatter field records how well-grounded a page's claims
  are. When you cite a `confidence: low` (or `status: stale`) page in an answer, say so — do not
  present shaky knowledge as settled.
- **Citations** — every substantive claim in an answer cites the vault page(s) it came from, as
  wikilinks. Grounding is the whole point; an uncited answer is indistinguishable from a guess.

## The curation flywheel: why every interaction ends with an offer

Knotica improves without touching any model weights — its "weights" are its **schemas and
prompts**, evolved by two nested loops fed by curated examples:

- **Inner loop — DSPy** optimizes the *operation prompts* against a metric (QA accuracy +
  citation validity − lint violations − token cost), using each topic's curated `qa.jsonl` as the
  trainset. More curated examples → a compile-ready topic → better operation prompts.
- **Outer loop — SIA** evolves *structure* (schemas, the vault's shape, and prompt scaffolding),
  proposing changes as reviewable branches — never direct commits to main.

This is why the ingest and query prompts always end by offering to curate, and why standalone
curate exists: **the flywheel is the product.** An interaction that was good but uncaptured is a
lost training example. Solicit curation; do not treat it as optional politeness.

## This skill is itself SIA-evolvable

Like the operation prompts and the schemas, this skill is an artifact the outer (SIA) loop may
revise as the wiki's conventions mature. Treat its guidance as the current best judgment, not a
frozen constitution — the vault's `SCHEMA.md` and the operation prompts remain the runtime source
of truth for any specific operation.
