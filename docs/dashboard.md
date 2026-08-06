# Knotica dashboard

Preact MCP client for the self-improvement loop: **Vault → Ask → Loop → Arena → Ingest → Datasets**.
It talks only to knotica MCP tools (`wiki_status`, `query`, `compile_*`, `arena_*`, `datasets_*`, …) — no
parallel REST API.

The **Loop** pane reflects `knotica loop --topic <t>` (the autonomous watcher — see
[`docs/architecture.md`](../docs/architecture.md#3a-loop-lifecycle-knotica-loop---topic-t)): an
Observe → Gate → Heal stepper driven by `wiki_status.loop` (`runner` liveness, `progress` — live
per-question/substage counts (`answering`, `judging k/n`) during an in-flight eval, `stage`,
`baseline_scalar`, `baseline_policy`) and `wiki_status.llm` (whether headless LLM credentials are
present). A **defend** toggle switches the gate policy between `latest` and `best` inline
(`loop_baseline_policy`); when metrics history shows a scalar above the current baseline, a
**"Re-freeze at best (X)"** action appears (`loop_rebaseline`). The scoreboard separates live
candidates from an **Observation history** section — merged `loop/r/*` audit pointers, de-emphasized,
each with a delete action; the loop auto-prunes these beyond the newest 5. The Vault pane's dataset
bootstrap shows live per-page progress while cold-starting a topic's trainset. None of this is polled
from a parallel API — the same `wiki_status` tool call both mounts use.

## Two mounts (same artifact)

| Mount | Audience | How |
|-------|----------|-----|
| **MCP App** (`ui://knotica/dashboard`) | Claude Desktop Chat / claude.ai | Ask Claude to call tool `open_dashboard` |
| **HTTP** (`GET /`) | Browser / Claude Code Browser pane | `knotica mcp --http --port 8765` |

End-user Desktop install + AWM walkthrough: [`docs/CLAUDE_DESKTOP.md`](../docs/CLAUDE_DESKTOP.md).
Ask/Compile/Arena panes call headless MCP tools (`query`, `compile_run`, …) and need LLM
credentials in Desktop’s MCP `env` — see
[Headless LLM credentials](../docs/CLAUDE_DESKTOP.md#headless-llm-credentials-query--compile--eval).

## Develop / rebuild

```sh
cd dashboard
npm install
npm run build
```

The build emits one self-contained `dist/index.html` and packages it into
`src/knotica/dashboard/app.html` (wheel artifact). CI verifies the checked-in
HTML stays current with sources.

### Local HTTP preview

```sh
# from repo root (vault must be configured)
knotica mcp --http --port 8765
open 'http://127.0.0.1:8765/?topic=agentic-systems'
```

Use `?mcp=http://host:port/mcp` to point the client at another streamable-HTTP MCP server.
Use `?vault=<name>` when multiple vaults are configured.

To see the Loop pane's runner-liveness chip and live progress, start the watcher in another terminal:

```sh
knotica loop --topic agentic-systems   # watches forever; first observation freezes the gate baseline
```

### Claude Desktop (MCP App)

1. Install/register knotica with Desktop — see [`docs/CLAUDE_DESKTOP.md`](../docs/CLAUDE_DESKTOP.md).
2. In Chat: “Call knotica `open_dashboard` with topic `agentic-systems`.”
3. The host renders the `ui://` iframe; panes call tools over the postMessage bridge
   (`connect-src` is blocked — no `fetch` to localhost from inside the App).

If the host lacks MCP Apps, `open_dashboard` still returns the HTTP fallback URL.
