# `dashboard/` — the Preact MCP client

A single-file Preact + TypeScript app, mounted two ways from one artifact: as an MCP App (`ui://knotica/dashboard`, opened by the `open_dashboard` tool) and over HTTP (`knotica mcp --http`). Both mounts import the same loader — never the reverse.

## Build

```bash
npm --prefix dashboard install
npm --prefix dashboard run build     # tsc --noEmit && vite build && package-artifact
```

> The built artifact is **committed**. `npm run build` writes `dashboard/dist/index.html` and packages it to `src/knotica/dashboard/app.html`; the Dashboard CI workflow rebuilds and then runs `git diff --exit-code` on both paths. A source change without a committed rebuild fails CI. Rebuild and commit in the same change.

`src/knotica/dashboard/__init__.py::dashboard_html` resolves the wheel-packaged `app.html` first and falls back to `dashboard/dist/index.html` in a checkout — which is why **an installed user never needs a Node toolchain**. Keep that fallback intact.

## Talking to the server

`src/toolClient.ts` is the single seam for MCP calls. Add a call there rather than reaching for a transport in a component. The dashboard is a *client* of the same public tool surface everything else uses — if a pane needs data, the answer is a tool call, not a new server endpoint.

Panes live in `src/*Pane.tsx`, with supporting views alongside (`ScoreboardPanel`, `PromotePreview`, `DeletePreview`, `PromptDiff`, `MetadataTreePanel`, `NotesDriftView`, `NotePromoteDialog`, `CompilePanel`).

## Rules that keep the UI honest

- **Billed actions are two-phase.** A preview click shows model, thread count, and cost estimate; only a second, explicit confirm executes. A single click must never bill. Mirror the server's nonce flow rather than inventing a client-side confirmation.
- **`unknown` is a real state, not a bug.** When the last eval's harness version differs from the baseline's — or no baseline exists — the loop chart reads `unknown`, not `fail`. Cross-instrument scalars are incomparable and the UI must not imply otherwise.
- Read-only panes must stay read-only; the `arena` dispatcher exposes only `status` and `history`.

User-facing page: [`docs/dashboard.md`](../docs/dashboard.md).
