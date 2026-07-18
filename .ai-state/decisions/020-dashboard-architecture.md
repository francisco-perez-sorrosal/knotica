---
id: dec-020
title: Dashboard architecture — one MCP data contract, one artifact, two transports
status: accepted
category: architectural
date: 2026-07-17
summary: The Knotica dashboard is a pure MCP client over deterministic tools (wiki_status, metrics_read, …); one Preact+Vite single-file artifact mounts both as a ui:// MCP App (Claude Desktop) and via streamable HTTP (browser / Claude Code Browser pane); CI builds the HTML into the wheel so uv-only users never need node; official mcp SDK stays (dec-007 vindicated).
tags: [dashboard, mcp-apps, ext-apps, ui, phase-3a, loop, client-as-brain, cold-start]
made_by: agent
agent_type: systems-architect
branch: worktree-hackathon-loop-ideas
pipeline_tier: standard
affected_files: [src/knotica/mcp_server/, src/knotica/core/status.py, src/knotica/core/metrics.py, dashboard/, pyproject.toml]
affected_reqs: []
dissent: A Python-stdlib localhost dashboard with a parallel REST/SSE data path (the earlier hackathon-scoped framing) would ship a visible chart sooner, at the cost of a second data contract that drifts from the MCP tool surface and cannot render inside Claude Desktop's MCP-App sandbox.
re_affirms: [dec-003, dec-007, dec-013]
---

## Context

The loop demo ("The Wiki That Argues With Itself") needs a live management surface: scalar-over-generations chart, gate line, stage cards, later arena/golden/vault panes. Two audiences matter: Claude Desktop Chat (can render MCP Apps via SEP-1865 / ext-apps) and Claude Code (cannot — Browser pane previews localhost only). The sandbox CSP for MCP Apps blocks all network (`connect-src 'none'`), so a localhost `fetch` dashboard cannot feed a `ui://` iframe. Ecosystem survey (RESEARCH_FINDINGS.md, 2026-07-17) confirmed: official `mcp` SDK ≥1.26 already registers Apps as plain `@mcp.tool(meta=…)` + `@mcp.resource(mime_type="text/html;profile=mcp-app")` (qr-server crib); knotica pins 1.28.1; PrefectHQ `fastmcp` buys nothing we lack. IDEA_DETAIL.md § Long-term dashboard architecture decided the permanent shape; this ADR promotes it.

## Decision

1. **One data contract — MCP tools.** The dashboard is a pure MCP client. Every pane reads through deterministic tools (`wiki_status`, `metrics_read`, later golden-review / schema-diff ops). No parallel REST API, no SSE side channel, no second data path. Honors client-as-brain + stateless-server by construction.

2. **One artifact, two transports.** A `ToolClient` seam with two implementations:
   - **Bridge** — `@modelcontextprotocol/ext-apps` `App` over postMessage inside the `ui://` mount (Claude Desktop / claude.ai).
   - **Standalone** — `@modelcontextprotocol/sdk` `Client` + `StreamableHTTPClientTransport` against the server's own `streamable_http_app()` with `stateless_http=True` + CORS (localhost now; Phase-4 Railway/Render + OAuth later). Claude Code's Browser pane views this mount — no third build.

3. **TypeScript workspace, CI-built single file.** `dashboard/` npm workspace: Preact + Vite + `vite-plugin-singlefile` + uPlot + `@preact/signals`. Types generated from tool JSON schemas. CI builds the single-file HTML; the wheel force-includes it (mirror `vault-template/`); the server serves it as a resource string. **No Python/uv user needs node**; cold-start (dec-013) untouched — eval/dashboard build deps stay off the wheel's runtime deps.

4. **M1 tool shapes (the integration spine).**
   - `wiki_status(topic: str = "")` — vault or topic-scoped: page/curated/lint counts, compile-ready progress, `last_eval` (latest `MetricsRecord` subset or null), `gate` (`state` ∈ {unknown,pass,fail}, `baseline`, `last_scalar`), `loop.stage` (null until the M2 runner persists state). Gate baseline stays `null` / `state=unknown` until the loop runner records one — M1 does not invent a hardcoded 0.5707.
   - `metrics_read(topic: str, limit: int = 100, before_generation: int | None = None)` — windowed `metrics.jsonl` records (ascending generation in the window), `has_more` / `next_before_generation`, `skipped_malformed` count. Path: `<topic>/.knotica/metrics.jsonl` via `VaultStore`.

5. **dec-007 reversal trigger stays dormant.** Official SDK path is proven by ext-apps Python examples. Re-open only if host interop breaks on the official-SDK path in practice.

## Considered Options

### Option A — MCP-tools-only + dual-transport TS artifact (chosen)
- **Pros:** one contract for Claude and the dashboard; MCP-App sandbox compatible; Phase-4 HTTP transport arrives early rather than as a second server; CI-built artifact protects uvx cold start.
- **Cons:** M1 must land before a visible chart; Node toolchain exists in-repo for authors (CI absorbs it for consumers).

### Option B — stdlib Python HTTP + SSE + later MCP-App wrap
- **Pros:** fastest first pixel; reuses `review_golden.py` pattern.
- **Cons:** REST/SSE is a second data path; CSP blocks it inside `ui://`; retrofit under a working side channel is the sloppiness the quality-first plan forbids.

### Option C — switch to PrefectHQ `fastmcp` 3.x for native Apps helpers
- **Pros:** ergonomic `@app.ui()` helpers.
- **Cons:** larger dep surface vs dec-007/dec-013; official SDK already sufficient (`mcp>=1.26`).

## Consequences

- **Positive:** dashboard doubles as a living exercise of the same tool surface Claude uses; M2 runner state has a single exposure path; Phase-3a keep/discard loop and Phase-4 remote mount share architecture.
- **Negative:** charting is blocked on M1 schemas + M3 workspace; gate pass/fail is honest-`unknown` until M2 persists a baseline.

## Disconfirmation

- **Falsifier:** if Claude Desktop cannot render a `ui://` resource registered via the official SDK's two-decorator pattern against knotica's FastMCP instance, the Python path assumption was wrong — fall back to the CDN vanilla-JS path (qr-server) behind the same `ToolClient` seam, or re-open dec-007.
- **Steelmanned runner-up (Option B):** a visible Sentinel demo at M2 without waiting on the TS workspace may still want a temporary stdlib pane — only acceptable if it calls the same MCP tools over streamable HTTP (no REST), i.e. it is Option A's standalone mount, not a parallel API.
- **Reversal trigger:** adopt PrefectHQ `fastmcp` only if official-SDK host interop fails in practice; drop the TS workspace for the CDN fallback only if CI single-file packaging proves operationally worse than an embedded HTML string.

## Prior Decision

Re-affirms `dec-003` (thin deterministic tools — `wiki_status` / `metrics_read` are read-only, topic-explicit, no progressive disclosure), `dec-007` (official mcp SDK — Apps need no supersession), and `dec-013` (eval/dashboard weight stays off the uvx wheel runtime). Extends `dec-004` by applying the `NOT_CONFIGURED` contract to the new tools. Does not supersede any prior decision.
