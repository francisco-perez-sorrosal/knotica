# Idea Ledger — Hackathon "Loop Engineering" (2026-07-16)

**Task slug:** `hackathon-loop-ideas` · **Tier:** Spike (ideation only; user-directed worktree isolation)
**Worktree/branch:** `.claude/worktrees/hackathon-loop-ideas` / `worktree-hackathon-loop-ideas`

## Decision

**Selected (user-confirmed): "The Wiki That Argues With Itself"** — a synthesis of three promethean
candidates, built as three rungs with a cut line at each boundary:

1. **Regression Sentinel spine** — every vault/prompt change → `knotica eval` on Buildkite;
   green merges, red self-corrects (auto-revert at minimum).
2. **Prompt Evolution Arena as the corrective** — a red gate triggers N `query.md` variants raced
   through cached re-evals; live leaderboard; winner auto-merges (Phase-3a-lite, no DSPy).
3. **Component-delta diagnose heuristic** — biggest-dropping scalar component routes the corrective
   (quality → arena; citation/lint → rollback).

## Why it wins on the fitness function

- **Loop narrative (5/5):** the gate-triggers-a-race story makes self-correction *visible competition*;
  scalar = observe, merge/revert = correct — the autoresearch keep/discard physics as CI.
- **Visual (5/5):** live scalar-over-generations chart (metrics.jsonl was designed for it) + racing
  leaderboard, on the shipped Solarized design system.
- **Buildable (5/5):** every rung reuses shipped Phase-0–2 assets; graceful degradation — a slip at
  any rung still demos the rung below.
- **Sponsors (4/5):** Buildkite genuinely load-bearing (spike-verified GO: free tier, Homebrew macOS
  agent, REST trigger/poll, ~15–25 min to first green); AWS App Runner hosts the dashboard (Akash
  dropped — funded-wallet friction); Pomerium conditional (hard cutoff, first cut).
- **Residual (5/5):** the eval-gate CI, the metrics dashboard, and the variant→score→promote loop all
  survive as real knotica infrastructure pointing at Phase 3a/3b.

## Revision 2026-07-17 — sponsor stack dropped, project-native stack adopted

User re-decision: forget the hackathon sponsor tools; build with the best pragmatic tools already in
the project's orbit. Same loop, same three rungs, same demo narrative. Stack becomes:

- **Trigger/orchestrator:** local `scripts/loop_runner.py` (git branch poll on the vault repo) replaces
  Buildkite as the spine; sub-second reaction, decision logic in-process, truer to the clone-and-branch
  invariants.
- **Variant generation:** the shipped OAuth-first `evals/llm.py` `LLMClient`.
- **Dashboard:** localhost stdlib + Solarized + SSE (unchanged — never was sponsor tax).
- **Audit/CI:** GitHub (the vault's existing private remote) for branch audit; GitHub Actions eval-on-PR
  workflow as the standing-CI stretch. **Obsidian** joins the demo via Advanced-URI deep links from the
  dashboard into the healed pages.
- **Hosting/access:** none — localhost; managed deploy (Render/Railway) stays a Phase-4 decision.
  AWS App Runner, Pomerium, and Buildkite are removed; the recovered ~1.5–2 h guarantees rung 3 and
  buys arena depth + rehearsal.

Riskiest assumption shifts from Buildkite setup (resolved GO, now moot) to **a live arena variant
clearing baseline on stage** — mitigated by pre-generated variants, warm caches, and the honest
no-winner → revert fallback. Detail doc rewritten in place (`.ai-work/hackathon-loop-ideas/IDEA_DETAIL.md`).

## Addendum 2026-07-17 — Knotica Dashboard + MCP Apps (ext-apps) scoping

*(Surface renamed "dashboard" — one name, one artifact: `scripts/loop_dashboard.py` is the umbrella;
the loop pane is its first pane.)*

User addition: a central console managing all wiki functionality, ideally embedded as an MCP App
(modelcontextprotocol/ext-apps). Researcher spike verdict (sources in the ephemeral RESEARCH_FINDINGS.md):

- **Claude Desktop (Chat tab) / claude.ai render MCP Apps — GO.** Server registers a `ui://` resource
  (`text/html;profile=mcp-app`); sandboxed iframe calls the server's own MCP tools over a postMessage
  bridge. Sandbox CSP blocks all network — data flows through tool calls only.
- **Claude Code — NO-GO as a rendering surface** (terminal and desktop Code tab); its Browser pane
  previews localhost apps natively instead.
- **Python/FastMCP path unproven** (no example exists; protocol is language-agnostic) — real risk,
  bounded by scoping.

Scoping decision: the dashboard is the **localhost web app** (`loop_dashboard.py`, umbrella over the
loop, arena, golden review, and vault status panes) — Claude Code sees it via the Browser pane.
A minimal Desktop-only `ui://` status card (one read-only pane + one `wiki_status` tool, 45-min
timebox, cut without ceremony) proves the MCP-App path. The full MCP-App dashboard is a post-hackathon
roadmap item; the dashboard's data layer is written behind a thin fetch adapter so the later swap to
bridge tool-calls is mechanical ("same HTML, two mounts").

**Frontend authoring — long-term decision (2026-07-17, user directive to optimize for the long
term, superseding the same-day hackathon-scoped "vanilla JS" call):** the dashboard's permanent
architecture is (1) **one data contract — the MCP tools**: the dashboard is a pure MCP client; every
pane reads through deterministic tools; no parallel REST API; (2) **one artifact, two transports** —
a `ToolClient` seam with a postMessage-bridge implementation (`ui://` mount, Claude Desktop/claude.ai)
and an MCP streamable-HTTP implementation (browser mount: localhost now, the Phase-4 Railway/Render +
OAuth 2.1 deploy later; Claude Code views this mount in its Browser pane); (3) **TypeScript in a
`dashboard/` npm workspace** (Vite + `vite-plugin-singlefile` + ext-apps SDK; TS types generated from
tool JSON schemas), with the single-file HTML built in CI and force-included into the wheel like
`vault-template/` — no Python user needs node, cold-start (dec-013) untouched; (4) **dec-007 is
revocable, held under an explicit reversal trigger** (user directive 2026-07-17: revoke past decisions
when the long-term solution demands it): at dashboard implementation start, if the official `mcp` SDK
still lacks SEP-1865/Apps support and hand-rolling exceeds ~a day or fights host interop, supersede
dec-007 (full swap to PrefectHQ `fastmcp`, or a split: official SDK on the stdio launch path,
`fastmcp` on an off-launch-path HTTP mount) via the standard supersession protocol; verify then
whether ext-apps' Python examples use the official SDK or PrefectHQ `fastmcp` (spike digest
ambiguous). Supporting evidence from the second spike: no
CDN/no-bundler path for the ext-apps SDK; server/app language decoupling confirmed; hosts pass
theme/displayMode/CSS vars (Solarized adapts via its dark twin). Day-one sequencing may start panes
in vanilla JS behind the `ToolClient` seam, with all new data needs as MCP tools, so the TS port is a
re-housing, not a rewrite. **Promote to an ADR draft when implementation starts.**

## Candidates considered

7 ideas generated by promethean (full breakdowns were in the ephemeral
`.ai-work/hackathon-loop-ideas/IDEA_PROPOSAL.md`): Self-Healing Wiki (24/25), Prompt Evolution
Arena (22), Regression Sentinel (21), Golden-Set Grower (20), Loop Cockpit (19), External-Source
Ingest Loop (19 — strongest sponsor honesty, rejected for two-external-platform schedule risk),
Cost/Quality Pareto Optimizer (16). The selected synthesis composes the brain of #1, the corrective
of #2, and the spine of #5.

## Build-session pointers (tomorrow)

- Detail doc (demo script, hour-by-hour plan, cut lines, sponsor mapping):
  `.ai-work/hackathon-loop-ideas/IDEA_DETAIL.md` (ephemeral — on this worktree only).
- Spike findings: `.ai-work/hackathon-loop-ideas/RESEARCH_FINDINGS.md`.
- Riskiest assumption — *resolved GO*: fresh Buildkite account → local macOS agent → first green
  build in ≤45 min (estimated 15–25).
- Biggest gotcha: Pomerium's "quick start" is a Docker Compose + IdP + managed-subdomain/TLS flow
  that can eat the whole 30-min slot — hard cutoff, cut first, sponsor count stays ≥3.
