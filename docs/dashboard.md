# Dashboard

The dashboard is a Preact web app that drives the self-improvement loop through knotica's MCP
tools — no parallel REST API, no separate backend. It talks to the process-lane dispatchers
(`compile`, `loop`, `arena`, `branches`, `datasets`, `notes`), flat tools (`query`, `wiki_status`,
`metrics_read`, `curate_example`, `baseline_probe`, `prompt_diff`, `suggestions_read`,
`suggestions_review`, `ingest_activity_read`, `gaps_read`, `gapfill_discover`), and the `vault`
and `open_dashboard` tools — all callable by any MCP client.

## Contents

- [Open it](#open-it)
- [Query parameters](#query-parameters)
- [Panes and lanes](#panes-and-lanes)
- [Pane reference](#pane-reference)
- [Lane reference](#lane-reference)
- [Shared components](#shared-components)
- [Obsidian links](#obsidian-links)
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
| `?pane=` | `tend` | Initial pane. Legacy routing for bookmarks and old links — maps to the pane or lane that absorbed its content. Accepts: `ask`, `sources`, `ingest`, `improve`, `tend`, and deprecated aliases (`home`→`tend`, `learn`→`ingest`, `answer`→`ask`, `fill`→`sources`, `loop`/`arena`/`datasets`/`golden`→`improve`, `vault`/`notes`→`tend`). Anything unrecognized falls back to `tend`. **Deprecated by `lane=`** — use `lane=` instead. |
| `?lane=` | (none) | Initial process lane. Accepts `home`, `learn`, `answer`, `improve`, `fill`, `tend`. Unrecognized values fall back to `tend`. Each lane is a process surface with an ordered rail of stages. Use with `focus=` to anchor a stage within the lane. |
| `?focus=` | (none) | Stage or object ID within the active lane. Unrecognized values degrade to the lane's default view. Requires `?lane=` to be meaningful. |
| `?mcp=` | `http://127.0.0.1:8765/mcp` | HTTP-mount MCP endpoint override — point the client at a different streamable-HTTP server. |
| `?mount=` | (auto) | `bridge` or `http` forces the transport, overriding the framed-window auto-detect. |

## Panes and lanes

Navigation top-level surfaces: **Ask → Sources → Ingest → Improve → Tend**. The first three are
panes (single view each); the last two are process lanes (multi-stage rails).

### Pane reference

#### Ask

Query the vault with grounded questions. See [self-improvement.md](self-improvement.md) for how
the query engine compiles and serves answers.

- Textarea + **Ask** queries the vault against the live compiled `query_engine`. **Pin as Before**
  freezes the current answer as a baseline card; asking the same question again renders an
  **After** card once the text differs.
- Each answer card offers **Save as good** / **Save as bad**, which curates the question into
  the trainset. Citations render as clickable Obsidian links.
- Contextual banners: "Flywheel ready" (compile-ready, not yet compiled), "Compiled engine is
  live" (re-ask hint), "Gate is red" (links to `improve` lane's Gate stage).

#### Sources

Diagnose and approve knowledge gaps. See [gap-fill.md](gap-fill.md) for the diagnose → discover →
approve pipeline this pane sits in (stages P1 and P3).

- **Open gaps** lists diagnosed gaps that discovery has not drained yet — the P1 queue, read via
  `gaps_read`. Each card carries the fault class, the filed date, a gap-origin badge, the
  unanswered question, its reference pages, and (on a `reported` gap) the reason given for filing
  it. There is nothing to approve on a gap, so the cards carry no actions; run
  `knotica fill discover --topic <topic>` to search for sources. Generation and scalar are
  deliberately not shown: both are constant zeros on a reported or retracted gap, and rendering
  `gen-0` beside a hand-filed gap presents a placeholder as a measurement.
- Filter tabs: **pending** / **approved** / **all**, with count badges: approved, a topic-wide
  **refused** count (approved suggestions whose latest gate pass failed, awaiting rework), and
  ingested. The tabs filter *suggestions* only — the open-gap list is unaffected by them.
- Each card shows the fault class, generation, rank, a gap-origin badge (measured / reported /
  retracted), a source-reputability badge, the failed question, and the suggested source
  (title, link, venue/authors/citations, DOI, open-access signal).
- Actions on an undecided suggestion: **Approve**, **Reject…** (requires a reason before
  confirming), **Defer**.

> [!IMPORTANT]
> There is no "mark as ingested" button anywhere in this pane. Marking a suggestion ingested is
> reachable only by calling the underlying tool directly, not from the UI.

- Decided suggestions show the recorded decision. A rejected-by-gate outcome additionally shows
  the score delta and reason, with an expandable diff of the worst-regressed golden questions.
- The list paginates with a **Load more** cursor.

#### Ingest

Watch an ingest or curate run live. See [gap-fill.md](gap-fill.md) and
[tutorial.md](tutorial.md) for what triggers a run.

- A stage rail across the top highlights the reached and current stage. Ingest runs show the
  8-stage pipeline (resolve topic → read schema → fetch → parse → plan → store source → write
  page → complete); curate runs show 2 stages (curate → complete).
- **Runs** (left) lists the 20 most recent runs for the topic; click one to pin the timeline to it.
- **Timeline** (right) is a chronological event stream that auto-scrolls unless you've scrolled
  up. Bursty stages collapse into a **Show each checkpoint** expandable group. Events reported
  out of order get a "· late" badge but stay in time order.

### Lane reference

Process lanes are multi-stage workflows anchored in the `core/process_model.py` declaration. Each
lane stages its work as an ordered rail where you progress through numbered stages; a stage is
complete when its work finishes, blocked when it requires human input or external action, or
pending when waiting for a prior stage. The rail renders stage glyphs (✓ / ! / 1-N) and accepts
clicks to expand a stage and trigger its actions.

#### Improve

Improve is the topic-scoped iterative loop: design → implement → test → promote. It merges the
observability and healing workflows, replacing the old tabbed Loop/Arena/Heal surface.

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
5. **Promote** — move the merged prompt to production. **Prove in Ask** jumps to the Ask pane so
   you can re-ask your question against the now-merged prompt (disabled until something has merged).
6. **Gate** — review pending `loop/c/*` candidate branches. Lists pending candidates with a diff
   link per row; **Gate next candidate now** runs the full LLM eval. Requires a frozen baseline and
   a pending candidate.

A runner-liveness chip shows "runner: watching · pid N" or "runner: off". A chart plots the gate
scalar over generations.

#### Tend

Tend is the per-vault mechanical checklist: health checks, repairs, and notes. It merges the old
VaultPane Checks surface with personal marginalia.

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

## Shared components

Embedded inside other panes or lanes, not top-level:

| Component | Lives in | What it does |
|-----------|----------|--------------|
| Vault stat tiles | Tend lane (Migrate stage) | Vault-wide counts: Topics, Pages, Curated, Lint hits, Unpushed, Last lint. **+ New KB** opens a form (path / name / optional topic) to create and switch to a new vault. Topics picker list shows one card per topic with health glyph; click to select. Active topic shows **Bootstrap trainset** (before compile-ready) with "synthesizing page k/n" label. |
| Scoreboard | Ask pane, Improve lane (Observe/Prove/Gate stages) | Per-topic baseline summary (frozen state, gate state, source path); sections for open compile candidates (promote/delete), compile history (delete-only), loop candidates (promote + diff-on-select), observation history (merged `loop/r/*` pointers, delete-only, auto-pruned beyond 5), and read-only arena variants. Promote/delete both preview before applying. |
| Metadata tree | Tend lane (Doctor stage) | Collapsible tree of the vault's metadata substrate — root `SCHEMA.md`/`log.md`, the vault-root `.knotica/` tree, then the selected topic's `SCHEMA.md` and `.knotica/` — one level deep by default, with a hover tooltip explaining each file's purpose; links open in Obsidian. |
| Prompt diff | Ask pane, Improve lane (Prove/Gate stages), Scoreboard rows | Collapsible unified diff — either across git refs of `query.md`, or between the vault's `query.md` and the full compiled runtime program (with demo count and artifact filename). |
| LaneRail | Improve, Tend lanes | Shared infrastructure for rendering the stage rail, deriving stage states from MCP tool outputs, and coordinating armed-confirm affordances for two-phase actions (billed eval, repairs, promotions). Defined in `dashboard/src/lanes/laneRailState.ts` and `LaneRail.tsx`. |

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
