# TASK_BRIEF — notes-overlay

**Date:** 2026-07-29
**Tier:** Standard (design-only pass; no implementation this cycle)
**Slug:** `notes-overlay`

## Intent

Add a **personal notes (marginalia) layer** over the topics of any Knotica-managed KB.

The user reads a *synthesized answer produced by an LLM agent* (Claude Desktop / Claude Code)
about a KB topic. Reading it provokes a personal reflection. The passage that provoked it is
ephemeral generated prose — it does **not** literally exist as any stored document/line/char
range in the vault. The system must nevertheless durably anchor the note to a concrete KB
location (document → paragraph → line → char boundary) and keep that anchor meaningful as the
AI maintenance loop continuously rewrites pages.

The notes layer is:
- **Independent** of the page/source relationship graph — its own folder, its own graph.
- **Never scored** — it must not influence any KB quality measurement.
- **Harvestable** — a legitimate source of real human questions for eval datasets.

## Key signals

- Vault is the only state; git-backed Obsidian markdown; one commit per mutating operation.
- Client-as-brain invariant: the MCP server is deterministic; the client LLM does cognition.
- Pages are rewritten wholesale (`write_page` is a full-body replace) — anchors WILL drift.
- Multi-vault: the active KB is resolved per call; notes are per-vault.

## Locked decisions (user, 2026-07-29)

| # | Decision | Value |
|---|---|---|
| D1 | Storage locus | `notes/<topic>/` at vault root, sibling to `sources/<topic>/` |
| D2 | Feedback boundary | Opt-in per note via explicit `intent` (reflection default; dispute/gap/question promote) |
| D3 | Eval mining | Mined into a human-review queue; approved candidates enter dev/golden; notes never scored |
| D4 | Capture UX | All four: natural language, `/knotica:note`, dashboard NotesPane, direct-in-Obsidian |

## Assumptions (surfaced, not confirmed)

| # | Assumption | Load-bearing? | Reversible? |
|---|---|---|---|
| A1 | Anchor fidelity is **graded**, never fatal: `span → block → section → page → topic`. A note is always storable. | Yes | Yes |
| A2 | The client supplies the verbatim quote it displayed + claimed provenance; the server **verifies and resolves** deterministically. Server never guesses. | Yes | No — it is the client-as-brain invariant |
| A3 | Anchors drift under loop rewrites; an explicit `orphaned` state + re-anchor pass + human review is required, not optional. | Yes | Yes |
| A4 | Notes are exempt from the core page frontmatter contract (they get their own schema). | Yes | Yes |
| A5 | Notes are excluded from the loop's change-detection watch scope (a note must never trigger an eval). | Yes | Yes |
| A6 | Notes may reference *multiple* anchors (one note, several spans) and other notes (own graph). | Medium | Yes |

## Health guards

- **No score contamination.** Every scoring surface funnels through `iter_page_paths()`; the design
  must make notes invisible there by construction (omission), not by a growing filter list.
- **No fifth hardcode.** The codebase has 4+ literal `"sources"` string checks and no folder-family
  concept. Adding `notes` must *generalize* that, not duplicate it. (Balanced Coupling.)
- **No loop churn.** A note write must not wake the autonomous loop.
- **Capture friction is the feature's life-or-death variable** (per annotation prior art). Any design
  that costs the user more than one sentence in-flight will die unused.

## Uncertainty Flag

**6/10.** The storage/exclusion/UX halves are well-grounded by research. The *anchoring* half —
mapping ephemeral generated prose to a durable, rewrite-surviving span — is the genuinely hard,
under-determined part and depends on the external prior-art pass.

## Out of scope this pass

Implementation. This cycle produces: `SYSTEMS_PLAN.md`, `INTERFACE_DESIGN.md`, ADR fragments.
