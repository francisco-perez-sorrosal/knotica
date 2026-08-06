# Memory Guillotine — the pitch, updated to what shipped

A seven-slide deck for the Memory Guillotine: claim-level retraction, demotion, and
re-grounding inside an AI-maintained wiki. Open [`index.html`](index.html) in a browser
— it is a self-contained `<deck-stage>` component with no build step and no server.

Originally a hackathon pitch (2026-07-07), when the feature was a proposal. It has been
**updated against the shipped implementation**, because a pitch deck that keeps
describing a design nobody built is worse than no deck: it is a confident, well-designed
account of the wrong system.

## What changed, and why the deck was worth keeping

The argument survived intact. One design commitment did not, and it is the most
interesting thing on the slides:

| Pitched | Shipped |
|---|---|
| "Execute a reversible demotion or removal patch"; "wiki pages are edited" | **No page prose is ever rewritten.** `guillotine/` is a read-only analysis layer; the `.diff` is evidence rendered for a human and is never applied |
| The claim is removed, and that is the end state | An applied weakening verdict files an `origin="retracted"` **gap**, which flows through discovery → human approval → gated ingest to be re-grounded in a real source |
| `DISPUTE / DEMOTE` as one band | `DISPUTE` only when a refutation was actually found; otherwise it degrades to `DEMOTE`, because disputing without counter-evidence overstates the evidence |

What did *not* change: the five risk bands. `VERDICT_THRESHOLDS` in
`src/knotica/guillotine/models.py` still publishes exactly the 0–25 / 26–45 / 46–65 /
66–80 / 81–100 split the original deck showed.

## Verifying the deck against the code

Every claim on the slides is checkable, and should be re-checked before the deck is
shown again. The load-bearing ones:

| Slide claim | Where it is true |
|---|---|
| Risk bands and verdicts | `guillotine/models.py` (`VERDICT_THRESHOLDS`, `Verdict`, `PassageRole`) |
| `DISPUTE` → `DEMOTE` without refutations | `guillotine/score.py` |
| Pipeline is search → classify → score → patch | `guillotine/runner.py` composes those four; `report.py` is called by the adapters (`cli/guillotine.py`, `core/operations/guillotine.py`), not the runner |
| No page prose rewritten; package holds no transaction | `.ai-state/DESIGN.md` §3 `guillotine/` row; enforced by the codebase-wide sole-writer scan in `tests/test_architecture_boundaries.py` |
| A trial cannot cite its own prior reports | `guillotine/paths.py::is_guillotine_report_path` |
| Artifacts + catalog bullet in one commit; gap filed separately | `core/operations/guillotine.py` |
| Retracted gap joins the gap-fill queue | `core/gapfill.py::file_retracted_gap`, `origin="retracted"` |
| CLI flags | `cli/guillotine.py` |
| CLI-only, no MCP surface | no `guillotine` reference under `src/knotica/mcp_server/` |

## Slide sequence

1. Memory Guillotine
2. What Is an LLM-Wiki?
3. What Was Pitched, What Shipped
4. A Claim-Level Trial
5. Verdicts and the Risk Score
6. The Loop Closes: Retraction Becomes Research
7. Using It Today

## Files

- `index.html` — the deck; content lives here
- `deck-stage.js` — the slide-stage web component (vendored, unmodified)

The deck loads DM Sans and DM Serif Display from Google Fonts, so text falls back to a
system sans when offline. Nothing else is fetched.
