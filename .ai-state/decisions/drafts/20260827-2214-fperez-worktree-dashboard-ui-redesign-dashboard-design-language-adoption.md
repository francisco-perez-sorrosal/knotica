---
id: dec-draft-6c4b92df
title: Adopt the exe.dev structural grammar over a retained Solarized semantic palette
status: proposed
category: implementation
date: 2026-08-27
summary: Replace the dashboard's neutral ramp, typographic voice and ornament with a flat monospace dark-first language, while keeping Solarized's semantic hues and adding a neutral tone for `unknown`.
tags: [dashboard, ui, design-tokens, theming, accessibility]
made_by: agent
agent_type: interface-designer
branch: worktree-dashboard-ui-redesign
pipeline_tier: standard
affected_files:
  - dashboard/src/theme.css
  - dashboard/src/app.css
  - dashboard/src/compileStages.ts
---

## Context

The user asked for a visual redesign inspired by exe.dev/integrations: near-black flat
background, monospace type, uppercase letter-spaced micro-labels, icon-led outlined cards,
subtle 1px borders, pill nav, clean tables. The current dashboard is Solarized: teal-washed
surfaces (`#002b36` dark, `#fdf6e3` light), a serif display face, two radial glow gradients
on `body`, and two-layer drop shadows on cards.

Three constraints bound the change. The host may force either theme via `data-theme`, so
light mode stays first-class even though the reference is dark-only. No external font may be
fetched (CSP; sandboxed iframe). And `app.css` is 3098 lines with ~40 sites reading
`var(--display)` and seven `--chart-*` constants baked into uPlot strokes, which cannot read
`var()` at all.

Separately, two existing tone mappings are dishonest and contradict the project's own
"`unknown` is a real state, not a bug" invariant: `flywheelTone("Curating")` returns `"bad"`
(a healthy fresh topic renders **red**), and `.gate-unknown` is tinted `--warn`.

## Decision

Adopt exe.dev's **structural grammar**; retain Solarized's **semantic hues**; replace the
**neutral ramp**, the **typographic voice** and the **ornament**.

- New 11-step neutral ramp (`--n-0` … `--n-10`) becomes the source of `--bg`, `--surface`,
  `--surface-raised`, `--line`, `--text`, `--heading`, `--muted`. Dark `--bg` is `#0b0c0d`.
- `--display` is **re-valued** to the monospace stack rather than deleted, so all 22 existing
  call sites inherit the new voice with no edit. `--sans` is retained for multi-sentence
  prose. `body` moves from sans to mono.
- Ornament removed: `--glow` becomes `transparent` (killing both body radial gradients) and
  `--shadow` becomes `none`. Cards are 1px borders. A separate `--shadow-overlay` exists for
  popovers only.
- `--accent`/`--good`/`--bad`/`--warn` keep their Solarized hues. Two tones are **added**:
  `--neutral` (for `unknown`, `not checked`, `curating`, `pending`, `—`) and `--running`
  (in-flight, distinct from warn).
- `flywheelTone` gains a `"neutral"` return: `Curating → neutral`, `Ready → info`.
  `.gate-unknown` moves from `--warn` to `--neutral`.
- `--chart-*` and the uPlot chart are explicitly out of scope.

## Considered Options

### A. Full palette replacement (drop Solarized entirely)

- **Pro**: cleanest match to the reference; no legacy hue baggage.
- **Con**: the accent hues carry stable semantic meaning across a 3098-line stylesheet and
  are duplicated as literal hex in seven `--chart-*` constants that uPlot reads directly.
  Replacing them means re-tuning the loop chart, which is a separate task with its own
  visual-regression surface — a large blast radius for no user-visible benefit.

### B. Layout-only restructure, palette untouched

- **Pro**: minimal risk; no token audit; no contrast re-verification.
- **Con**: does not achieve the goal. The teal wash, the glow gradients and the serif face
  are precisely what makes the current UI read as a different product from the reference.
  Restructuring the layout inside the old skin gets a better-organised Solarized page.

### C. Hybrid — structural grammar over retained hues (chosen)

- **Pro**: the largest visual delta for the smallest diff; the neutral ramp and body chrome
  are a `theme.css`-only change with zero component edits, making it the cheapest possible
  rollback point; the `--display → var(--mono)` re-valuation reaches 22 call sites in one
  line; the semantic layer that other code depends on is untouched.
- **Con**: the resulting palette is neither pure Solarized nor pure exe.dev, so a future
  reader must be told which parts are load-bearing. `--display` keeps a name that no longer
  describes its value.

## Consequences

**Positive.** The dominant visual change ships as a token-only increment with no component
risk. Semantic call sites keep working. The added `--neutral` tone finally gives the
codebase a way to render `unknown` honestly, closing a standing invariant violation.
Removing the shadows and gradients also removes two composited paint layers.

**Negative.** `--display` is now a misleading name and must carry a comment saying so;
new code should read `--mono` directly. Every surface's contrast must be re-verified on
both themes — the design fixes the floor at 4.5:1 for all text including 10px micro-labels
(no large-text exemption) and 3:1 for borders, icons and focus rings. Long-form prose that
was on `--display` will render monospace; any such site must be repointed to `--sans`
rather than reverting the token.

## Disconfirmation

**Falsifier.** If, after increment 1 ships, light mode reads as an inverted dark theme
rather than a designed light theme — or if any text surface measures below 4.5:1 — the
ramp is wrong and the neutral values need re-derivation per theme rather than as one
inverted scale.

**Steelmanned runner-up.** Option A is the honest reading of the user's brief: they pointed
at a specific site and asked for that design language, and a hybrid always risks landing in
an uncanny middle. The counter is that the reference's *identity* lives in its structure —
flat neutral ground, mono voice, 1px borders, uppercase micro-labels — not in its specific
accent hues, of which it barely uses any.

**Reversal trigger.** If the loop chart is ever re-tuned (the `--chart-*` constants
rewritten), revisit the whole palette at once — that is the moment a full Solarized removal
becomes cheap, and doing it then avoids paying the visual-regression cost twice.
