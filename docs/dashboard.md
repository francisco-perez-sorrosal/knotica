# Dashboard

The dashboard is a Preact web app that drives the self-improvement loop through knotica's MCP
tools — no parallel REST API, no separate backend. It talks to the same nine dispatchers
(`loop`, `branches`, `compile`, `datasets`, `arena`, `golden`, `notes`, `vault`, `vault_health`)
plus the flat tools `query`, `wiki_status`, `metrics_read`, `curate_example`, `baseline_probe`,
`prompt_diff`, `suggestions_read`/`suggestions_review`, and `ingest_activity_read` — all of them
callable by any MCP client.

## Contents

- [Open it](#open-it)
- [Query parameters](#query-parameters)
- [Panes](#panes)
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
> End-user Desktop install lives in [`CLAUDE_DESKTOP.md`](CLAUDE_DESKTOP.md). The Ask, Loop and
> Compile panes call headless MCP tools and need LLM credentials in Desktop's MCP `env` block —
> see that guide's headless-credentials section. The Arena pane is read-only and needs none.

## Query parameters

Read once at load, in `App.tsx`. Pane/topic/vault selection round-trips into the URL as you
navigate, so a reload preserves your view.

| Param | Default | Effect |
|-------|---------|--------|
| `?topic=` | (vault-wide) | Initial topic. Reconciled against the vault's real topic list on load; falls back to the first real topic if the requested one doesn't exist. When opening a lane pane, topic narrows the data to that topic only. |
| `?vault=` | (none) | Initial vault name. It wins on load but does not pin the selection: once the active vault changes from another client (e.g. `/knotica:use`), the picker follows it. |
| `?pane=` | `vault` | Initial pane. Accepts `vault`, `ask`, `loop`, `sources`, `notes`, `arena`, `ingest`, `datasets`; `golden` is a legacy alias that normalizes to `datasets`. Anything else falls back to `vault`. **Deprecated by `lane`** — use `lane=` instead. |
| `?lane=` | (none) | Initial process lane. Accepts `home`, `learn`, `answer`, `improve`, `fill`, `tend`. An unrecognized lane degrades to the default vault view. Use with `focus=` to anchor a stage or object within the lane. |
| `?focus=` | (none) | Stage or object ID within the active lane. An unrecognized `focus` degrades to the lane's own landing view. Requires `lane=` to be meaningful. |
| `?mcp=` | `http://127.0.0.1:8765/mcp` | HTTP-mount MCP endpoint override — point the client at a different streamable-HTTP server. |
| `?mount=` | (auto) | `bridge` or `http` forces the transport, overriding the framed-window auto-detect. |

## Panes

Tab order: **Vault → Ask → Loop → Sources → Notes → Arena → Ingest → Datasets**. Sources and
Notes carry a numeric badge (pending suggestions; drifted-anchor count).

### Vault — default pane

- Vault-wide stat tiles: Topics, Pages, Curated, Lint hits, Unpushed, Last lint.
- **+ New KB** opens a form (path / name / optional topic) to create and switch to a new vault.
  A picker `<select>` appears as soon as at least one vault is configured.
- **Topics** list, one card per topic, health glyph from lint hits / curated-vs-threshold /
  last eval. Click a card to select it. The active topic's card shows **Bootstrap trainset**
  (only before it's compile-ready), with a live "synthesizing page k/n" progress label while the
  loop's bootstrap phase runs.
- Embeds the metadata tree, the compile panel, and the scoreboard (see
  [Shared components](#shared-components)).
- **Checks** — four tabs, each with a health chip and a remediation column:

  | Tab | What it does |
  |-----|--------------|
  | Doctor | Run/refresh; "Show fix guidance" (CLI commands only, not an automatic restore); "Repair dry-run" lists tracked/untracked paths with checkboxes, then **Apply selected** or **Apply all tracked** (both confirm before running). |
  | Lint | Scope picker (topic / whole vault); inspect-only, no auto-repair — fix flagged pages via Claude or Obsidian. |
  | OKF | Check, then dry-run / apply repair (apply confirms; it writes files and creates one git commit). |
  | Loop | **Process one candidate** gates the next pending `loop/c/*` branch; may run a full LLM eval. Requires a frozen baseline and a pending candidate. |

### Ask

- Textarea + **Ask** queries the vault. **Pin as Before** freezes the current answer as a
  baseline card; asking the same question again renders an **After** card once the text differs.
- Each answer card offers **Save as good** / **Save as bad**, which curates the question into
  the trainset. Citations render as clickable Obsidian links.
- Contextual banners: "Flywheel ready" (compile-ready, not yet compiled), "Compiled engine is
  live" (re-ask hint), "Gate is red" (links to Arena).

### Loop

Mirrors `knotica improve loop --topic <t>` — see [self-improvement.md](self-improvement.md) for what the
watcher does. A gate chip (pass/fail/unknown) and baseline scalar sit above an interactive
Observe → Gate → Heal → Merged stepper.

- **Observe** — before any score exists: **Set cold start (0)**, a naive zero-floor baseline
  with no LLM call. Once a score exists: **Freeze at current score** / **Re-freeze at current
  score** / **Raise bar to current score**, plus an advanced override field for a custom scalar.
  A **defend policy** toggle switches the gate between `latest` (tracks reality) and `best`
  (ratchets a high-water mark) — **Re-freeze at best (X)** appears only when metrics history
  shows a scalar more than `1e-6` above the current baseline. Cadence fields (min interval
  hours / window / threads) write on blur. **Run eval now** is a two-step billed action: the
  first click previews worker/judge/thread count and cost without billing; a second, explicit
  **Confirm — run and bill** click executes it (**Cancel** discards the preview).
- **Gate** — lists pending `loop/c/*` candidates with a diff link per row; **Gate next
  candidate now** runs the same action as the Vault pane's Loop check.
- **Heal** — shows the live arena stage/race when one is running; **Open Arena** enables once a
  race is live, racing, or healed.
- **Merged** — **Prove in Ask** jumps to the Ask pane so you can re-ask your question against the
  now-merged prompt (disabled until something has merged).

A runner-liveness chip shows "runner: watching · pid N" or "runner: off". Below the stepper, a
chart plots the gate scalar over generations.

### Sources — open gaps and the gap-fill suggestion queue

See [gap-fill.md](gap-fill.md) for the diagnose → discover → approve pipeline this pane sits in
(stages P1 and P3).

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

### Notes — personal marginalia

Browse your own [notes](notes.md) and resolve anchor drift. Two views:

- **Browse** — filter by intent (all / reflection / dispute / gap / question) and anchor status
  (all / exact / unanchored / shifted / fuzzy / orphaned). The header shows a total count and a
  "drifted" badge (fuzzy + orphaned). Each card shows intent, date, tags, an overall
  anchor-status badge, the note text, and one row per anchor (page/heading, status glyph, quoted
  passage, pin timestamp). Per-anchor: **Re-anchor** (shifted/fuzzy only), **Review drift →**
  (fuzzy/orphaned/anchor-invalid only), **Detach** (always). Card-level: **recheck anchors**
  (re-resolves live), **Promote…**, **Archive** (hidden once already archived).
- **Review drift** (via "Review drift →") — one item per anchor needing attention: a
  pinned-quote-vs-live-quote diff, "superseded" vs "rewritten" framing, alternative-placement
  candidates when no confident match exists, and an overlap percentage. Actions: **Re-anchor
  here**, **Re-anchor to selected**, **Keep the old pin** (dismisses locally only — nothing is
  written, the item resurfaces next fetch), **Detach**.

Every mutating action here (archive / detach / re-anchor) previews on the first click and applies
only on the second. **Promote…** offers **Training example** always and **Knowledge gap** only
for dispute/gap/question notes — the golden set is never offered, because the underlying tool
always rejects it.

### Arena

- Leaderboard: variants ranked by scalar, each a horizontal bar with a baseline marker, rank,
  delta vs baseline, and a status badge (pending / scored / winner / lost).
- History: the last 12 races, newest first.
- **Refresh** re-polls on demand; the pane also auto-polls every 2.5 seconds.
- Contextual banners: "Winner promoted" → **Prove in Ask** on a completed race; "No winner" →
  **Back to Loop** on a reverted one.

### Ingest

Watch an ingest or curate run live. See [gap-fill.md](gap-fill.md) and
[tutorial.md](tutorial.md) for what triggers a run.

- A stage rail across the top highlights the reached and current stage. Ingest runs show the
  8-stage pipeline (resolve topic → read schema → fetch → parse → plan → store source → write
  page → complete); curate runs show 2 stages (curate → complete).
- **Runs** (left) lists the 20 most recent runs for the topic; click one to pin the timeline to it.
- **Timeline** (right) is a chronological event stream that auto-scrolls unless you've scrolled
  up. Bursty stages collapse into a **Show each checkpoint** expandable group. Events reported
  out of order get a "· late" badge but stay in time order.

### Datasets

Manage the [trainset and golden set](self-improvement.md) for a topic — `?pane=datasets`
(`?pane=golden` is a legacy alias for the same pane).

- Two tables: **Loop corpora** (`trainset` / `held_out` / `seal` — read-only expand) and
  **Golden pipeline** (`candidates` / `reviewed` — editable expand).
- A Bootstrap → Review → Freeze breadcrumb lights up each step once its precondition is met.
- **Bootstrap** synthesizes golden candidates from entity pages, then auto-expands Candidates.
- Expanding `candidates`/`reviewed` loads editable cards: question/answer text, a
  duplicate-of-trainset flag, and a Discard/Restore toggle.
- **Save reviewed** (enabled once you've made changes) writes the staged reviewed set.
- **Freeze** (confirms before running) writes the sealed golden set and its manifest. It is
  disabled only for the one condition that genuinely blocks it — a reviewed set overlapping the
  trainset — and when there is nothing reviewed to freeze. Below the recommended floor it stays
  **enabled** and warns instead, inline and in the confirm: the floor governs how noisy the eval
  scalar will be, not whether the set is valid, and `evals/golden.py::freeze` freezes under it by
  design ("the human is the gate"). Disabling it there stranded a small vault with no way to
  establish a first baseline, and so no way to run the gated ingest that requires one. Note this
  floor is separate from compile's hard **≥ 20 frozen records** precondition
  ([self-improvement](self-improvement.md)) — freezing 9 is allowed; compiling on 9 is not.
- A contamination banner surfaces train∩held-out / train∩reviewed / train∩candidates overlap
  counts whenever nonzero — freezing refuses any overlap between the reviewed set it freezes and
  the trainset.

## Shared components

Embedded inside other panes, not top-level tabs:

| Component | Lives in | What it does |
|-----------|----------|--------------|
| Compile panel | Vault | Trainset-vs-threshold meter, **Compile** (disabled once already compiled or not ready), a live trial/trial-total poll while compiling, and **Preview merge** → **Apply merge to main** once done. Shows which optimizer ran (MIPRO or bootstrap, with a fallback-reason tooltip when MIPRO was unavailable). |
| Scoreboard | Vault, Loop | Per-topic baseline summary (frozen state, gate state, source path); sections for open compile candidates (promote/delete), compile history (delete-only), loop candidates (promote + diff-on-select), observation history (merged `loop/r/*` pointers, delete-only, auto-pruned beyond the newest 5), and read-only arena variants. Promote/delete both preview before applying. |
| Metadata tree | Vault | Collapsible tree of the vault's metadata substrate — root `SCHEMA.md`/`log.md`, the vault-root `.knotica/` tree, then the selected topic's `SCHEMA.md` and `.knotica/` — one level deep by default, with a hover tooltip explaining each file's purpose; links open in Obsidian. |
| Prompt diff | Loop, Compile, Scoreboard rows | Collapsible unified diff — either across git refs of `query.md`, or between the vault's `query.md` and the full compiled runtime program (with demo count and artifact filename). |

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
npm run build
```

This regenerates `dist/index.html` and copies it into `src/knotica/dashboard/app.html`, the file
the server reads at runtime (falling back to `dashboard/dist/index.html` for a source checkout
without a packaged build).

Local HTTP preview, with the loop watcher running alongside so the Loop pane has live data:

```sh
knotica mcp --http --port 8765 &
knotica improve loop --topic agentic-systems &
open 'http://127.0.0.1:8765/?topic=agentic-systems'
```

See [architecture.md](architecture.md) for how the dashboard's two mounts fit into the MCP
server as a whole.
