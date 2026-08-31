# `dashboard/` — the Preact MCP client

A single-file Preact + TypeScript app, mounted two ways from one artifact: as an MCP App (`ui://knotica/dashboard`, opened by the `open_dashboard` tool) and over HTTP (`knotica mcp --http`). Both mounts import the same loader — never the reverse.

## Build and test

```bash
npm --prefix dashboard install
npm --prefix dashboard test          # vitest run
npm --prefix dashboard run build     # tsc --noEmit && vite build && package-artifact
```

`npm test` transpiles from source through `vitest.config.ts` and reads nothing the build produces, so it runs first and fails faster; CI runs the same two commands in that order. There is no separate typecheck step to remember — `tsc --noEmit` inside `npm run build` covers the tests too, because `tsconfig` includes `src`.

> The built artifact is **committed**. `npm run build` writes `dashboard/dist/index.html` and packages it to `src/knotica/dashboard/app.html`, and CI then runs `git diff --exit-code` over both paths. **Only `src/knotica/dashboard/app.html` is actually gated**: `dashboard/dist/` is gitignored, so that path is untracked and can never dirty a diff — naming it in the command does not make it enforced. A source change without a committed rebuild of `app.html` fails CI. Rebuild and commit in the same change.

`src/knotica/dashboard/__init__.py::dashboard_html` resolves the wheel-packaged `app.html` first and falls back to `dashboard/dist/index.html` in a checkout — which is why **an installed user never needs a Node toolchain**. Keep that fallback intact.

A `core/process_model.py` change touches two `git diff --exit-code` gates at once — the generated mirror (`dashboard/src/processModel.ts`, gated by `make verify`) and the built artifact above (gated by CI) — so run `make dashboard-rebuild` from the repo root to pay both in one command rather than discovering the second gate in CI.

## Lanes and components

The dashboard is structured as **six process lanes**, each declared once in
`src/knotica/core/process_model.py` and projected here.

**Lanes** live in `src/lanes/<lane-name>/` with their stage components and per-lane types/client split. The six lanes are `home/`, `learn/`, `answer/`, `fill/`, `improve/`, `tend/` — see `src/knotica/core/process_model.py` for the ordered stage id/title/handoff declaration each lane's rail projects (lane vocabulary rule: point here, never restate the stage lists).

Each lane carries its own type and client definitions:
- `lanes/<lane>/types.ts` — type definitions for that lane's MCP responses and UI state
- `lanes/<lane>/client.ts` — client methods (`ToolClient` prototype extensions) for that lane's tool calls

This split from the earlier monolithic `types.ts` and `toolClient.ts` (both over 800 lines) allows the client and types to grow with each lane independently, kept under the ratchet ceiling by construction. Home's types and client live in `lanes/home/{types.ts,client.ts}` alongside its `HomeLane.tsx` and `attentionRows.ts`.

Root-level shared presentation primitives (`src/`):
- `icons.tsx` — the 26-glyph inline stroke-SVG icon set (`IconName` union + `<Icon>`), CSP-safe, no icon font
- `InfoPopover.tsx` / `infoPopoverState.ts` — the non-modal `ⓘ` overlay primitive and its module-level "at most one open" signal
- `CopyBlock.tsx` — a mono code block with a copy-to-clipboard button
- `EmptyState.tsx` — the shared icon/title/sentence/one-action template for empty and zero states
- `CreateDrawer.tsx` — the "New knowledge base" / "New topic" panel behind the chrome `⊕` trigger (extracted from `App.tsx`)

Shared infrastructure lives in the lanes root:
- `laneRailState.ts` — pure state derivation for stage rail (no Preact, no DOM, no fetch). There is deliberately **no shared rail component**: every railed lane hand-rolls its own markup against the `.lane-stage` / `aria-current="step"` class contract, because each needs a different body-swap rule. The generic `LaneRail.tsx` shell was deleted once it had gone a full redesign with no production consumer.
- `LoopStrip.tsx` — the state-icon strip every railed lane mounts above its rail, projecting the same stage state the rail already holds
- `laneMeta.ts` — per-lane presentation copy (icon, one-line blurb, rail shape: cycle/line/checks), keyed to `PaneId`
- `stageMeta.ts` — per-stage presentation copy (popover text, optional icon), keyed lane-then-stage-id
- `stageFocus.ts` — the client-owned "what the user is looking at" focus axis, held orthogonal to server-declared stage state
- `ArmedButton.tsx` — two-phase action component (preview → confirm, with cancellation)
- `HandoffStage.tsx` — pauses a lane and dispatches a slash command to the client's own LLM
- `hostCapabilities.ts` — detects host capabilities (ext-apps bridge support) to decide whether to
  show the dispatch button or only the copyable command
- `visibilityPausedPoll.ts` — pause polling when the browser tab is hidden, resume on return

Shared supporting views, currently consumed from `lanes/improve/PromoteStage.tsx` and (`PromptDiff` only) `GateStage.tsx`/`ProveStage.tsx`, plus `lanes/tend/DriftStage.tsx`:
- `ScoreboardPanel`, `PromotePreview`, `DeletePreview`, `PromptDiff` — Improve's Promote/Gate/Prove stages
- `NotePromoteDialog` — Tend's Drift stage

## Talking to the server

`src/toolClient.ts` is the single seam for MCP calls. Add a call there rather than reaching for
a transport in a component. The dashboard is a *client* of the same public tool surface everything
else uses — if a lane needs data, the answer is a tool call, not a new server endpoint.

## Census and enumeration

The six lane names and their ordered stages are declared once in `src/knotica/core/process_model.py`.
The dashboard imports this declaration as `LANES` and `LANE_STAGES`, never restating it. When a
lane or stage is added, removed, or reordered, that change is made in `process_model.py` and the
mirror TypeScript is regenerated (`make dashboard-rebuild` updates `dashboard/src/processModel.ts`).
This single-source-of-truth discipline prevents the UI from deviating from the canonical model.

## Rules that keep the UI honest

- **Billed actions are two-phase, with no exception.** A preview click shows model, thread count, and cost estimate; only a second, explicit confirm executes. A single click must never bill. Mirror the server's nonce flow where the tool mints one; where it does not (`query`), arm client-side with `ArmedButton` — never ship the single click. `processMeta.test.ts` fails the build for any `billed`/`arms-billing` row whose `previewMode` is not `nonce` or `armed`.
- **Text-colour tokens clear WCAG AA on both themes.** Every semantic tone (`--good`/`--warn`/`--bad`/`--running`/`--accent`/`--neutral`) is used as small text somewhere, so all of them must reach 4.5:1 against every surface — including `button.danger`'s fill, which is mixed from `--bad` itself. Both dark blocks (`@media` and `[data-theme="dark"]`) must carry identical values. `themeContrast.test.ts` computes all of it from `theme.css`; do not eyeball a palette change.
- **`unknown` is a real state, not a bug.** When the last eval's harness version differs from the baseline's — or no baseline exists — the loop chart reads `unknown`, not `fail`. Cross-instrument scalars are incomparable and the UI must not imply otherwise.
- **No native dialogs.** Use `ArmedButton` and the lane-rail state machine for all confirmations. No browser `window.confirm()`, no custom modals — the two-phase semantics are the UI itself, not a dialog on top of it.
- **The `arena` dispatcher is read-only** — it exposes only `status` and `history` actions. No mutations.
- **Every process answers six questions, and the answers live in [`lanes/processMeta.ts`](src/lanes/processMeta.ts).** A *process* is a user-triggered action that spends money, mutates the vault, or hands work to another agent — reads and navigation are not processes. Each one declares, in the registry and nowhere else: **why** it is necessary (the cause, in server semantics), **what it will do** (the effect on the vault, the spend, the reversibility), how it **previews** (`nonce` / `armed` / `dry-run` / `none` — there is no billed single-click mode), how it shows **progress** (`busy` / `poll` / `external` / `instant`), how it reports its **outcome** (`result` / `verdict` / `refresh` / `external`), and **what comes next and why** as a reachable `(lane, stage)` anchor. `next` has no null member: `terminal` is an answer, absence is not. `processMeta.test.ts` is the gate — a mutating or billing `ToolClient` method with no row fails the build, a row nothing wires fails it, a registered method called from a file that does not declare its process fails it, and a `next` pointing at a stage `core/process_model.py` does not declare fails it too.
- **A silent re-render is not an outcome.** When the only visible result of a mutation is a list re-reading itself, the process declares `outcomeMode: "refresh"` and supplies the sentence that says so. Where a surface already composes its own sentence it keeps it and passes it in, so there is one live region rather than two — and a live region that already exists is never replaced by one that appears with its text, because that is not reliably announced.
- **A handoff may not claim progress it cannot see.** Once a payload leaves for Claude's turn or your terminal, the only honest claims are *sent*, *the poll runs at this cadence*, and *this panel updates itself*. Never "working", never "done" — machine-checked for every `dispatch: "handoff"` row.
- **A brief never repeats its button's verb.** `ProcessBrief`'s term is an accessible name in the same subtree as the control it explains, so `why approve` beside `✓ Approve` makes both match one role query. Name the consequence instead (`why queue it`).

User-facing page: [`docs/dashboard.md`](../docs/dashboard.md).
