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

**Lanes** live in `src/lanes/<lane-name>/` with their stage components and per-lane types/client split. The six lanes are:
- `home/` — cross-topic attention inbox (rail-less)
- `learn/` — read-only topic exploration
- `answer/` — question-answering and trainset curation
- `fill/` — gap-fill workflow (diagnose → discover → approve → ingest)
- `improve/` — topic-scoped iterative loop (observe → heal → instrument → prove → promote → gate)
- `tend/` — per-vault maintenance (doctor → lint → okf → migrate → drift)

Each lane carries its own type and client definitions:
- `lanes/<lane>/types.ts` — type definitions for that lane's MCP responses and UI state
- `lanes/<lane>/client.ts` — client methods (`ToolClient` prototype extensions) for that lane's tool calls

This split from the earlier monolithic `types.ts` and `toolClient.ts` (both over 800 lines) allows the client and types to grow with each lane independently, kept under the ratchet ceiling by construction. Home's types and client live in `lanes/home/{types.ts,client.ts}` alongside its `HomeLane.tsx` and `attentionRows.ts`.

Shared infrastructure lives in the lanes root:
- `laneRailState.ts` — pure state derivation for stage rail (no Preact, no DOM, no fetch)
- `LaneRail.tsx` — stage-rail rendering, two-phase armed-confirm affordances
- `ArmedButton.tsx` — two-phase action component (preview → confirm, with cancellation)
- `HandoffStage.tsx` — pauses a lane and dispatches a slash command to the client's own LLM
- `hostCapabilities.ts` — detects host capabilities (ext-apps bridge support) to decide whether to
  show the dispatch button or only the copyable command
- `visibilityPausedPoll.ts` — pause polling when the browser tab is hidden, resume on return

Shared supporting views used across lanes:
- `ScoreboardPanel`, `PromotePreview`, `DeletePreview`, `PromptDiff`, `MetadataTreePanel`,
  `NotePromoteDialog`

## Talking to the server

`src/toolClient.ts` is the single seam for MCP calls. Add a call there rather than reaching for
a transport in a component. The dashboard is a *client* of the same public tool surface everything
else uses — if a lane needs data, the answer is a tool call, not a new server endpoint.

## Census and enumeration

The five lane names and their ordered stages are declared once in `src/knotica/core/process_model.py`.
The dashboard imports this declaration as `LANES` and `LANE_STAGES`, never restating it. When a
lane or stage is added, removed, or reordered, that change is made in `process_model.py` and the
mirror TypeScript is regenerated (`make dashboard-rebuild` updates `dashboard/src/processModel.ts`).
This single-source-of-truth discipline prevents the UI from deviating from the canonical model.

## Rules that keep the UI honest

- **Billed actions are two-phase.** A preview click shows model, thread count, and cost estimate; only a second, explicit confirm executes. A single click must never bill. Mirror the server's nonce flow rather than inventing a client-side confirmation.
- **`unknown` is a real state, not a bug.** When the last eval's harness version differs from the baseline's — or no baseline exists — the loop chart reads `unknown`, not `fail`. Cross-instrument scalars are incomparable and the UI must not imply otherwise.
- **No native dialogs.** Use `ArmedButton` and the lane-rail state machine for all confirmations. No browser `window.confirm()`, no custom modals — the two-phase semantics are the UI itself, not a dialog on top of it.
- **The `arena` dispatcher is read-only** — it exposes only `status` and `history` actions. No mutations.

User-facing page: [`docs/dashboard.md`](../docs/dashboard.md).
