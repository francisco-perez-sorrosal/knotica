# Dashboard

The dashboard is a Preact web app that drives the self-improvement loop through knotica's MCP
tools — no parallel REST API, no separate backend. It is structured as six process lanes: one
cross-topic attention inbox (`home`) and five ordered workflows, each dispatching to lane-specific
and shared tools.

## Contents

- [Open it](#open-it)
- [Query parameters](#query-parameters)
- [The six lanes](#the-six-lanes)
- [Lane reference](#lane-reference)
- [Handoff stages](#handoff-stages)
- [Shared components](#shared-components)
- [Obsidian links](#obsidian-links)
- [Legacy `?pane=` routing](#legacy-pane-routing)
- [Develop / rebuild](#develop--rebuild)

## Open it

One built artifact, two mount points. Use whichever your client supports.

| Mount | Audience | Invocation |
|-------|----------|------------|
| MCP App (`ui://knotica/dashboard`) | Claude Desktop Chat, claude.ai | Ask Claude to call the `open_dashboard` tool (args: `topic`, `vault`) |
| HTTP (`GET /`) | Any browser, Claude Code's Browser pane | `knotica mcp --http --port 8765`, then open `http://127.0.0.1:8765/?topic=agentic-systems` |

`open_dashboard` returns an MCP-Apps resource (`mime_type: text/html;profile=mcp-app`, SEP-1865)
that hosts supporting MCP Apps render inline in an iframe, talking to the server over the
`@modelcontextprotocol/ext-apps` postMessage bridge (`connect-src` is blocked there — no `fetch`
to `localhost` from inside the App). Hosts without Apps support get a text reply pointing at the
HTTP mount instead, which talks to the same tools over `StreamableHTTPClientTransport`. The app
auto-detects which transport to use; `?mount=bridge`/`?mount=http` forces one.

> [!NOTE]
> End-user Desktop install lives in [`CLAUDE_DESKTOP.md`](CLAUDE_DESKTOP.md). The `Ask` pane and
> the process lanes call headless MCP tools and need LLM credentials in Desktop's MCP `env` block —
> see that guide's headless-credentials section.

## Query parameters

Read once at load, in `App.tsx`. Pane/topic/vault selection round-trips into the URL as you
navigate, so a reload preserves your view.

| Param | Default | Effect |
|-------|---------|--------|
| `?topic=` | (vault-wide) | Initial topic. Reconciled against the vault's real topic list on load; falls back to the first real topic if the requested one doesn't exist. When opening a lane, topic narrows the data to that topic only. |
| `?vault=` | (none) | Initial vault name. It wins on load but does not pin the selection: once the active vault changes from another client (e.g. `/knotica:use`), the picker follows it. |
| `?pane=` | (none) | **Deprecated.** Legacy routing for bookmarks and old links — maps to the lane that absorbed the pane's content. See [Legacy `?pane=` Routing](#legacy-pane-routing) for the full table. Accepts old pane names (`ask`, `sources`, `ingest`, `improve`, `tend`) and deprecated aliases. Anything unrecognized falls back to `home` (the default landing). Use `lane=` instead. |
| `?lane=` | (none) | Initial process lane. Accepts `home`, `learn`, `answer`, `fill`, `improve`, `tend`. Unrecognized values fall back to `home`. `home` is a flat cross-topic inbox with no stages; other lanes stage their workflows as ordered rails. Use with `focus=` to anchor a stage within a lane. |
| `?focus=` | (none) | Stage name or object ID within the active lane. Unrecognized values degrade to the lane's default view. Requires `?lane=` to be meaningful. |
| `?mcp=` | `http://127.0.0.1:8765/mcp` | HTTP-mount MCP endpoint override — point the client at a different streamable-HTTP server. |
| `?mount=` | (auto) | `bridge` or `http` forces the transport, overriding the framed-window auto-detect. |

## The six lanes

The dashboard is structured around six process lanes, each declared once in `src/knotica/core/process_model.py`; the dashboard is a projection of that declaration.

**`home`** is the cross-topic attention inbox: a flat, rail-less lane listing what needs your attention across all topics right now, without choosing a single topic first. The other five lanes (`learn`, `answer`, `fill`, `improve`, `tend`) are each a complete workflow from trigger to completion, arranged as an ordered rail of stages.

Each lane stages its work where you progress through numbered stages; a stage is complete when its work finishes (✓), active when it is the position the process has reached, blocked when it requires human input or external action, or pending when waiting for a prior stage. A fifth word, unknown, means the server found no evidence either way — it is the honest absence of a position, not a failure, and it is rendered neutral rather than as a warning. The rail accepts clicks to expand a stage and trigger its actions. A **Handoff Stage** pauses the lane and hands the work back to your client's own LLM via a slash command (`/knotica:fill`, etc.) — when that work completes, you resume the lane.

### Lane reference

#### Home

**Home** is the cross-topic attention inbox — a flat landing page showing what needs your attention
right now, across every topic in the active vault, without filtering to a single topic. It is the
default view when opening the dashboard or navigating to an unrecognized lane.

**No stages.** Home is not a rail-based workflow. It displays:

1. **Attention rows** — organized by urgency class: `critical` (red), `high` (orange), `normal` (neutral).
   Each row shows the topic, what needs attention (e.g., "3 pending gaps", "compile ready"), and an
   action button (`[Open]` to jump to that lane in the same topic, or `[Watch]` for topic selection).
2. **Drift row** (default-collapsed) — personal notes with anchors that have resolved stale. Expands to
   show the count and offers a `[Check]` button to navigate to the `Tend` lane's **Drift** stage. The
   expanded count is resolved on-demand (not pre-fetched) to keep Home's polling cheap.

**Poll behavior**: Home polls for updates every 10 seconds (when the window is visible; polling pauses
when the browser tab is hidden, resuming when you return). This is independent of the other lanes'
2-second poll. Home's poll uses `wiki_status view="attention"`, which is faster than the full summary
view — it includes only per-topic gap suggestions, compile-ready status, and loop-runner liveness,
skipping the expensive lint pass and note-anchor resolution.

#### Learn

**Learn** is the topic-scoped read-only exploration lane. Its purpose is to understand the current
state of a topic — what pages exist, what the prompt covers, and what the compiled engine knows.

**Three stages, in order:**

1. **Understand** — browse the topic's pages and see the current compiled query engine state.
   Expand pages to read their content, see backlinks, and filter by page category.
2. **Review prompts** — inspect the vault's `query.md` and the live compiled prompt side-by-side
   with a unified diff. Understand what was added, removed, or refined in the last compilation.
3. **Baseline** — see the frozen baseline score and the eval cadence configuration. Read-only view
   of where the measuring stick is set; to change it, go to the `Improve` lane's **Observe** stage.

#### Answer

**Answer** is the topic-scoped question-answering lane. It mirrors the earlier `Ask` pane,
providing a place to query the vault and curate answers into the trainset.

**Three stages, in order:**

1. **Ask** — query the vault with grounded questions. Submit a question; the server's compiled
   `query_engine` returns an answer, citations, and a cost estimate if the question will trigger
   eval.
   - **Pin as Before** freezes the current answer as a baseline card; asking the same question
     again renders an **After** card once the text differs.
   - Each answer card offers **Save as good** / **Save as bad**, which curates the question into
     the trainset.
   - Contextual banners: "Flywheel ready" (compile-ready, not yet compiled), "Compiled engine is
     live" (re-ask hint), "Gate is red" (prompts a review of the `Improve` lane's Gate stage).
2. **Trainset** — review curated questions. Expand entries to see your judgment, the answer you
   saved, and a discard/restore toggle. Filter by recent or by judgment (good / bad).
3. **Done** — no further action needed. Re-ask in the **Ask** stage if the compiled engine updates.

#### Fill

**Fill** is the per-topic gap-filling workflow: diagnose knowledge gaps → discover sources →
approve and ingest. See [gap-fill.md](gap-fill.md) for the full context.

**Five stages, in order:**

1. **Gaps** — browse diagnosed gaps waiting for sources. Lists open gaps from the `gaps_read` tool.
   Each card shows the fault class, filed date, the unanswered question, and reference pages.
   There is no approval action here — gaps are read-only until sources exist. To find sources,
   run `/knotica:fill` from your client (instructions in the Handoff stage below).
2. **Handoff: discover sources** — pauses the lane. Your client runs `/knotica:fill` to search
   external databases and write source candidates into the vault. When complete, you resume this
   lane.
3. **Suggestions** — review discovered source suggestions. Filter tabs: **pending** / **approved**
   / **all**, with count badges. Each card shows fault class, generation, rank, gap-origin badge
   (measured / reported / retracted), source reputability, the failed question, and the suggested
   source (title, link, venue, DOI, open-access signal).
   - Actions on an undecided suggestion: **Approve**, **Reject…** (requires a reason),
     **Defer**.
   - Decided suggestions show the recorded decision; rejected-by-gate outcomes show the score
     delta and worst-regressed questions.
   - The list paginates with a **Load more** cursor.

   > [!IMPORTANT]
   > There is no "mark as ingested" button here. Marking a suggestion ingested is reachable only
   > by calling the underlying tool directly, not from the UI.

4. **Handoff: ingest approved sources** — pauses the lane. Your client runs `/knotica:fill` to
   write approved sources and their pages into the vault, then gate the ingest. When complete,
   you resume.
5. **Review gate** — see the ingest result (merged, refused with regressed questions, or blocked
   pending a baseline). No action — the result is final.

#### Improve

**Improve** is the topic-scoped iterative loop: observe → heal → instrument → prove → promote →
gate. It merges the observability and healing workflows, replacing the old tabbed Loop/Arena/Heal
surface.

**Six stages, in order:**

1. **Observe** — baseline and eval cadence. Set a cold-start baseline (score 0) or freeze at the
   current score. Adjust defend policy (`latest` tracks reality; `best` ratchets a high-water
   mark). Configure eval cadence (min interval, window, threads). **Run eval now** is a
   two-phase billed action: first click previews cost; second click executes (**Cancel** discards).
2. **Heal** — live arena variant race. The dashboard renders the active stage/race in real time when
   one is running. **Open Arena** enables once a race is live, racing, or healed.
3. **Instrument** — build the golden set. Two tables: **Loop corpora** (`trainset` / `held_out` /
   `seal` — read-only expand) and **Golden pipeline** (`candidates` / `reviewed` — editable expand).
   A Bootstrap → Review → Freeze breadcrumb lights up each step once its precondition is met.
   **Bootstrap** synthesizes golden candidates from entity pages; expanding `candidates`/`reviewed`
   loads editable cards with question/answer text and a discard/restore toggle. **Save reviewed**
   writes changes; **Freeze** (confirms before running) writes the sealed golden set and manifest.
   Freezing is disabled only when the reviewed set overlaps the trainset; below the recommended floor
   it stays enabled but warns instead. A contamination banner surfaces train∩held-out /
   train∩reviewed / train∩candidates overlap counts whenever nonzero.
4. **Prove** — compile and validate the new prompt. Trainset-vs-threshold meter, **Compile**
   (disabled once already compiled or not ready), a live trial/trial-total poll while compiling,
   and **Preview merge** → **Apply merge to main** once done. Shows which optimizer ran (MIPRO or
   bootstrap, with a fallback-reason tooltip when MIPRO was unavailable).
5. **Promote** — move the merged prompt to production. **Re-ask in Answer** jumps to the `Answer`
   lane's **Ask** stage so you can re-ask your question against the now-merged prompt (disabled
   until something has merged).
6. **Gate** — review pending `loop/c/*` candidate branches. Lists pending candidates with a diff
   link per row; **Gate next candidate now** runs the full LLM eval. Requires a frozen baseline and
   a pending candidate.

A runner-liveness chip shows "runner: watching · pid N" or "runner: off". A chart plots the gate
scalar over generations.

#### Tend

**Tend** is the per-vault maintenance lane: health checks, repairs, and personal notes overlay
management. It merges the old VaultPane Checks surface with personal marginalia.

**Five stages, in order:**

1. **Doctor** — vault mutation consistency check. Run/refresh; "Show fix guidance" (CLI commands
   only, not automatic restore); "Repair dry-run" lists tracked/untracked paths with checkboxes,
   then **Apply selected** or **Apply all tracked** (both confirm before running). Health chip:
   green (no warnings), yellow (warnings present), red (failures present).
2. **Lint** — schema and markup validation. Scope picker (topic / whole vault); inspect-only, no
   auto-repair — fix flagged pages via Claude or Obsidian. Health chip: green (no violations),
   yellow (<10 violations), red (10+).
3. **OKF** — consistency of owned-knowledge fields (OKF). Check, then dry-run / apply repair
   (apply confirms; it writes files and creates one git commit). Health chip: green (no issues),
   yellow (warnings), red (failures).
4. **Migrate** — schema evolution. When the vault's top-level `SCHEMA.md` changes, this stage
   signals that pages need refresh. No automated action — the stage remains pending, signalling
   that human review is warranted before running `knotica tend migrate --dry-run` to preview and
   `--apply` to execute.
5. **Drift** — personal notes anchor resolution. Browse your own [notes](notes.md) and resolve
   anchor drift. Two views: **Browse** (filter by intent and anchor status; recheck/promote/archive
   actions) and **Review drift** (one item per anchor needing attention; re-anchor/detach actions).
   Every mutating action previews on the first click and applies only on the second. **Promote…**
   offers **Training example** always and **Knowledge gap** only for dispute/gap/question notes.

## Handoff Stages

Two lanes — `Fill` and `Learn` — carry **Handoff Stages** where the dashboard pauses and hands work
back to your client's own LLM via a slash command. When the command completes, you resume the lane.

The Handoff Stage in both cases carries:
1. **Narration** — a plain-English explanation of what just happened and what needs to happen next.
2. **Dispatch button** — if your host supports it (Claude Desktop Chat with the ext-apps bridge),
   a clickable button that invokes the `/knotica:*` command directly.
3. **Copyable command** — the full slash-command text, always copyable, for any client or host.

**Handoff targets:**
- `/knotica:fill` — continues a gap-fill session from the `Fill` lane's **Gaps** and **Suggestions**
  stages. The command searches for sources, writes candidates, and gates the ingest. See
  [commands/fill.md](commands/fill.md) and [gap-fill.md](gap-fill.md) for the full protocol.

## Legacy `?pane=` Routing

The dashboard's earlier panes (`ask`, `sources`, `ingest`, `improve`, `tend`) are now lanes. Old
bookmarks and URLs using `?pane=` are automatically rerouted to the lane that absorbed their
functionality. The mapping is:

| Legacy `?pane=` | Routed to | Lane | Stage |
|---|---|---|---|
| `ask` | `Answer` | `answer` | Ask |
| `sources` | `Fill` | `fill` | Suggestions |
| `ingest` | *(removed — no dashboard surface)* | — | — |
| `improve` | `Improve` | `improve` | Observe |
| `tend` | `Tend` | `tend` | Doctor |
| `loop`, `arena`, `datasets`, `golden` | `Improve` | `improve` | Observe |
| `vault`, `notes` | `Tend` | `tend` | Doctor |
| `home`, `learn`, `answer`, `fill` | corresponding lane | — | default stage |

Any unrecognized `?pane=` value defaults to `home` — the same landing a bare URL gets.

## Shared components

Embedded inside other panes or lanes, not top-level:

| Component | Lives in | What it does |
|-----------|----------|--------------|
| Vault stat tiles | Tend lane (Migrate stage) | Vault-wide counts: Topics, Pages, Curated, Lint hits, Unpushed, Last lint. **+ New KB** opens a form (path / name / optional topic) to create and switch to a new vault. Topics picker list shows one card per topic with health glyph; click to select. Active topic shows **Bootstrap trainset** (before compile-ready) with "synthesizing page k/n" label. |
| Scoreboard | Answer lane (Trainset stage), Improve lane (Observe/Prove/Gate stages) | Per-topic baseline summary (frozen state, gate state, source path); sections for open compile candidates (promote/delete), compile history (delete-only), loop candidates (promote + diff-on-select), observation history (merged `loop/r/*` pointers, delete-only, auto-pruned beyond 5), and read-only arena variants. Promote/delete both preview before applying. |
| Metadata tree | Tend lane (Doctor stage) | Collapsible tree of the vault's metadata substrate — root `SCHEMA.md`/`log.md`, the vault-root `.knotica/` tree, then the selected topic's `SCHEMA.md` and `.knotica/` — one level deep by default, with a hover tooltip explaining each file's purpose; links open in Obsidian. |
| Prompt diff | Answer lane (Review prompts stage), Improve lane (Prove/Gate stages), Scoreboard rows | Collapsible unified diff — either across git refs of `query.md`, or between the vault's `query.md` and the full compiled runtime program (with demo count and artifact filename). |
| LaneRail | All lanes | Shared infrastructure for rendering the stage rail, deriving stage states from MCP tool outputs, and coordinating armed-confirm affordances for two-phase actions (billed eval, repairs, promotions). Defined in `dashboard/src/lanes/laneRailState.ts` and `LaneRail.tsx`. |

## Obsidian links

Most file, page, and citation references render as clickable `obsidian://` links once the vault
path is known — citations, lint hits, doctor paths, and the metadata tree open directly in
Obsidian instead of as inert text. A few paths stay plain text by design: a note's own path, a
suggestion's reference pages, and the dataset file paths.

## Develop / rebuild

The dashboard is a single Preact app (Vite, inlined into one self-contained
`dist/index.html`) packaged into the wheel at build time.

> [!TIP]
> An installed end user needs no Node/npm toolchain. The built artifact ships pre-built inside
> the wheel; only someone rebuilding the dashboard itself needs the steps below.

```sh
cd dashboard
npm install
npm test
npm run build
```

This regenerates `dist/index.html` and copies it into `src/knotica/dashboard/app.html`, the file
the server reads at runtime (falling back to `dashboard/dist/index.html` for a source checkout
without a packaged build).

Local HTTP preview, with the loop watcher running alongside so the Improve lane has live data:

```sh
knotica mcp --http --port 8765 &
knotica improve loop --topic agentic-systems &
open 'http://127.0.0.1:8765/?lane=improve&topic=agentic-systems'
```

See [architecture.md](architecture.md) for how the dashboard's two mounts fit into the MCP
server as a whole.
