# Dashboard

The dashboard is a Preact web app that drives the self-improvement loop through knotica's MCP
tools — no parallel REST API, no separate backend. It is structured as six process lanes: one
cross-topic attention inbox (`home`) and five ordered workflows, each dispatching to lane-specific
and shared tools.

## Contents

- [Open it](#open-it)
- [Query parameters](#query-parameters)
- [Chrome](#chrome)
- [The six lanes](#the-six-lanes)
- [Lane reference](#lane-reference)
- [Handoff Stages](#handoff-stages)
- [Legacy `?pane=` routing](#legacy-pane-routing)
- [Shared components](#shared-components)
- [Design language](#design-language)
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
| `?pane=` | (none) | **Deprecated.** Legacy routing for bookmarks and old links — maps to the lane that absorbed the pane's content. See [Legacy `?pane=` Routing](#legacy-pane-routing) for the full table. Accepts old pane names (`ask`, `sources`, `ingest`, `improve`, `tend`) and deprecated aliases. Anything unrecognized falls back to `home` (the default landing). Use `lane=` instead. |
| `?lane=` | (none) | Initial process lane. Accepts `home`, `learn`, `answer`, `fill`, `improve`, `tend`. Unrecognized values fall back to `home`. `home` is a flat cross-topic inbox with no stages; other lanes stage their workflows as ordered rails. Use with `focus=` to anchor a stage within a lane. |
| `?focus=` | (none) | Stage name or object ID within the active lane. Unrecognized values degrade to the lane's default view. Requires `?lane=` to be meaningful. |
| `?mcp=` | same-origin `/mcp` on an `http:`/`https:` page; `http://127.0.0.1:8765/mcp` otherwise (e.g. a `file://` open of the built artifact) | HTTP-mount MCP endpoint override — point the client at a different streamable-HTTP server. The same-origin default follows whatever `--port` the server was started with, so it never polls a stale server the way a hardcoded port would. |
| `?mount=` | (auto) | `bridge` or `http` forces the transport, overriding the framed-window auto-detect. |

## Chrome

The header (`<header class="app-chrome">`) is two rows: a **context row** and a **pill nav**.

**Context row** — left side is the brand block: the `◈` mark, `knotica`, a vault picker
(a plain `<select>`, replacing the earlier "New KB" heading + picker pair once a vault list
exists — falls back to a static, Obsidian-linked heading when there is nothing to pick from),
a topic picker, and a `⊕` **creation-drawer trigger** (`aria-expanded` mirrors the drawer's open
state). Right side is a row of **status chips** — server LLM, gate baseline, and compile flywheel
— each a `<span class="health-chip ...">` tone badge paired with a sibling `InfoPopover` inside a
`.chrome-chip` wrapper (never merged into one control, to avoid two components fighting over
hardcoded dimensions), plus the mount/last-updated meta text.

The `⊕` trigger opens the **creation drawer**, which renders inline below the context row and
holds the "New knowledge base" and "New topic" forms together in one panel — the two were
previously separate inline forms, each with its own show/hide toggle. The drawer stays mounted
(so in-progress field text survives a close/reopen) and simply returns nothing when closed.

**Pill nav** (`<nav class="pane-tabs">`, second row) is six icon-plus-label buttons, one per
lane, in `process_model.py`'s canonical `LANES` order (Home, Learn, Answer, Improve, Fill, Tend).

**InfoPopover**, the non-modal `ⓘ` overlay behind every status chip, lane card, and rail stage
explanation, is a shared primitive (`InfoPopover.tsx`): click to open, click outside / press
Escape / focus-out to close, and only one is ever open at a time (a module-level signal closes
whatever else was open). It renders up to three slots — *What this is*, an optional
*What the states mean*, an optional *What to do next* — and positions itself via one of three
static alignment variants (`start`/`end`/`center`), never a measured/portaled popover.

## The six lanes

The dashboard is structured around six process lanes, each declared once in
`src/knotica/core/process_model.py`; the dashboard is a projection of that declaration.

**`home`** is the cross-topic attention inbox: a flat, rail-less lane listing what needs your
attention across all topics right now, without choosing a single topic first. The other five
lanes (`learn`, `answer`, `fill`, `improve`, `tend`) are each a complete workflow from trigger to
completion, arranged as an ordered rail of stages.

A stage is `complete` when its work finishes, `active` when it is the position the process has
reached, `blocked` when it requires human input or external action, `pending` when waiting for a
prior stage, or `unknown` — the server found no evidence either way. `unknown` is deliberately not
a guess: it is what a stage reads when nothing was recorded for it at all, rendered neutral rather
than as a warning, and it is distinct from `pending` (which asserts the stage genuinely has not
been reached yet). The rail accepts clicks to expand a stage and trigger its actions. A
**Handoff Stage** pauses the lane and hands the work back to your client's own LLM via a slash
command (`/knotica:fill`, `/knotica:ingest`) — when that work completes, you resume the lane.

Every railed lane also carries a **loop strip** above its rail — a row of small state-icon nodes
that mirrors the rail's stages, with a one-line headline (e.g. `IMPROVE · GATE ACTIVE — in
progress`) and its own `ⓘ` explaining the rail's shape and the five-state vocabulary. The strip's
shape follows the lane: `improve` is a **cycle** (its last node draws a return arc back to the
first, annotated "Prove returns to Instrument"); `tend` is **checks** (independent peer chips,
no track); every other railed lane is a straight **sequence**. Today, `improve` is the only lane
where a strip node or a rail row's disclosure toggle actually opens that stage's body (including
a "Start here" cue on the stage most in need of attention) — the other four railed lanes render
the strip as a read-only overview above their (always-visible) rail rows.

### Lane reference

#### Home

**Home** is the cross-topic attention inbox — a flat landing page showing what needs your attention
right now, across every topic in the active vault, without filtering to a single topic. It is the
default view when opening the dashboard or navigating to an unrecognized lane.

**No stages.** Home is not a rail-based workflow. It displays:

1. **Lane card grid** — six icon-led cards, one per lane, in canonical `LANES` order. Each card is
   a whole-card button (name, blurb, and a one-line stat drawn entirely from the same attention
   payload — e.g. "`N` ready · `N` running" for Improve, "`N` pending · `N` refused" for Fill) plus
   a sibling `ⓘ` explaining the lane. `Learn` and `Answer` show `—` with a popover noting they carry
   no cross-topic signal yet; open the lane to see its own state.
2. **Attention queue** — an urgency-tinted table (`AttentionTable`) listing every open signal, one
   row per signal rather than one per topic (a topic can appear more than once). Each row shows an
   urgency icon plus its word (`blocked`, `waiting`, or `running` — never colour alone), the topic,
   what needs attention, and an `[Open]` (or `[Watch]` for a running loop) button that jumps to that
   lane. When there is nothing to show, the queue is replaced by an empty state ("Nothing needs
   you") with a button into `Improve`.
3. **Drift row** — a fixed statement below the queue ("Note drift — not checked...") with a `ⓘ`
   explaining what drift means and offering a copyable `knotica notes drift --topic <topic>`
   command. There is no button that runs the check from Home — resolving anchors is the one cost
   the attention view does not pay unconditionally, so the honest affordance is the CLI command,
   not a client-side action with nothing wired behind it.

**Poll behavior**: Home polls `wiki_status(view="attention")` every 10 seconds (paused while the
browser tab is hidden, resuming on return) — independent of the other lanes' 2-second
`view="summary"` poll. The lane-card stats and the attention queue both read this single payload;
Home makes no other call.

#### Learn

**Learn** is the topic-scoped ingest-and-review lane: **Source → Fetch / parse → Pages → Curate**.
It absorbs the ingest journal's own progress reporting, folding its finer-grained stages onto
these four rail positions.

**Four stages, in order:**

1. **Source** — the topic and its schema are resolved and the source is stored in the vault.
2. **Fetch / parse** — the full text is fetched and parsed into page-sized sections. Runs on its
   own once a source is stored.
3. **Pages** — a **Handoff Stage** (`/knotica:ingest`): Claude writes the parsed sections into
   wiki pages under the topic while the dashboard watches and reports progress. Reaching this
   stage's terminal condition (a committed page) is the lane's actual outcome.
4. **Curate** — reviewing the written pages into a training example is its own workflow,
   deliberately decoupled from the ingest run: `Pages` reads `complete` as soon as its run ends,
   even with no curation yet, so an un-curated ingest is never "stuck" on this rail.

#### Answer

**Answer** is the topic-scoped question-answering lane: **Ask → Cite → React**.

**Three stages, in order:**

1. **Ask** — a question textarea and an `Ask` button. Submitting calls the compiled `query_engine`
   for this topic.
2. **Cite** — once the answer resolves, shows it together with the pages it cites (`AnswerCard`).
   Before that, reads "Ask a question to see its answer and citations."
3. **React** — four actions, all of which terminate inside Answer rather than navigating elsewhere:
   **Good example** / **Bad example** (curates the question into the trainset), **Note it**
   (captures the exchange as a personal note), and **Report gap** (files a knowledge gap for the
   `Fill` lane to pick up later).

#### Improve

**Improve** is the topic-scoped iterative loop, and the one **cycle**-shaped lane: **Instrument →
Observe → Gate → Heal → Promote → Prove**, with Prove looping back to Instrument.

Every stage body below is built from one shared **stage-body grammar** — a small set of
presentation primitives (`SectionCard`, `Stat`/`StatGrid`, `StateList`, `TermHint`) reused across
all six stages rather than each stage inventing its own layout; see
[Shared components](#shared-components). Every billed control (a call that spends model tokens or
writes files) carries a visible `billed` chip next to its button, and either arms on the first
click and bills on an explicit second confirm (`ArmedButton`), or previews on the first click and
bills on an explicit second confirm (the server-nonce two-phase flow) — never a single click.

**Six stages, in order:**

1. **Instrument** — build the golden set. Three cards: **PIPELINE** (candidates / reviewed /
   held-out counts, a seal chip, and the **Bootstrap** / **Freeze golden** controls), **TRAINSET**
   (the trainset count and **Bootstrap trainset**), and **FILES & OVERLAPS** (the train∩held-out /
   train∩reviewed / train∩candidates overlap counts, plus — behind a disclosure — the per-role
   breakdown, grouped into its `GOLDEN PIPELINE` and `LOOP CORPORA` families, drawn as the
   candidates → reviewed → held-out chain, each role name explaining itself on click).
   **Bootstrap**,
   **Bootstrap trainset**, and **Freeze golden** are billed, each behind a `billed` chip and a
   two-click armed→confirm control. **Freeze golden** refuses a reviewed set that overlaps the
   trainset.
2. **Observe** — baseline and eval cadence. Three cards: **MEASUREMENT** (latest generation,
   scalar, and the frozen baseline), **EVAL RUN** (the four `[loop]` settings and the billed eval
   trigger), and **SCALAR TREND** (the chart, behind a disclosure). **Run eval now (billed)** is a
   two-phase billed action behind a `billed` chip: the first click only quotes worker, judge, and
   thread count; the second, explicit confirm executes (**Cancel** discards, nothing bills).
   EVAL RUN prints cadence, window, threads, and the arena **SCORER** — each label carries an ⓘ
   naming the `[loop]` key and its default — and switches the scorer in place: **Use eval scorer**
   is a two-click armed→confirm control behind an `arms billing` chip (the click itself spends
   nothing; it arms one golden-set eval per variant on every future race), while switching back to
   the heuristic is a single click. Both write `[loop] arena_scorer` in
   `~/.config/knotica/config.toml`; see [configuration](configuration.md#loop-eval-cadence-and-the-arena-scorer).
3. **Gate** — review pending `loop/c/*` candidate branches. One **PENDING CANDIDATES** card lists
   them as a state list (branch name and a right-aligned sha), each pending row carrying a
   **Show query.md diff** toggle; **Gate next candidate now** is a billed, two-phase action behind
   a `billed` chip. Requires a frozen baseline and a pending candidate.
4. **Heal** — live arena variant race. Two cards: **ARENA** (the race's stage word, variant count,
   gate baseline, the scorer that produced the scalars, recent-race count, and the live variant
   standings as a state list) and **COMPILE** (the billed compile trigger). **Compile now** sits
   behind a `billed` chip and a two-click armed→confirm control, since a fresh compile has no free
   preview leg. When a race **aborts** — the heuristic scorer's scalars share no scale with the
   eval-derived gate baseline, so the arena refuses to rank rather than ranking meaninglessly — a
   warn-toned **WHY THE RACE ABORTED** card appears between the two, carrying the server's own
   reason verbatim and the same **Use eval scorer** control Observe offers, so the fix is applied
   where the problem is reported. The hand-edit it performs and the prerequisites (a frozen golden
   set, the `evals` extra) stay named below it.
5. **Promote** — move a merged compile candidate to production. One **BRANCH UNDER REVIEW** card
   shows the branch's identity (name, sha, status), its score / delta / baseline stats, the
   beats-or-misses-baseline verdict, and a prompt diff. **Preview delete** sits left of **Preview
   promote** — the destructive control is deliberately never the rightmost, closest-to-thumb
   target — and each previews before a second, explicit apply.
6. **Prove** — compile and validate the new prompt against a real question, closing the loop back
   to Instrument. One **COMPILED PROGRAM** card shows the compiled version, scalar, and a prompt
   diff against the live program; one **PROBE** card lets you ask the compiled program directly —
   **Probe it** carries a `costs tokens` chip and stays a single click, since a probe is a read
   with nothing to confirm; a **BEFORE / AFTER** card compares a pinned answer against the latest
   one once you have asked.

A runner-liveness chip shows "runner: watching · pid N" or "runner: off". A chart plots the gate
scalar over generations.

Each stage's rendered state is derived server-side from vault evidence — dataset counts for
`Instrument`, the last recorded eval scalar for `Observe`, a pending candidate branch for `Gate`,
an unpromoted compile-history entry for `Promote` — rather than a single hand-advanced watermark.
When none of that evidence exists at all, every stage on this rail reads `unknown` instead of the
misleading `pending` ("nothing has run yet" would be a guess the server cannot back up).

#### Fill

**Fill** is the per-topic gap-filling workflow: **Gap → Discover → Approve → Ingest → Gate**. See
[gap-fill.md](gap-fill.md) for the full context.

**Five stages, in order:**

1. **Gap** — browse diagnosed gaps waiting for sources. Lists open gaps from the `gaps_read` tool.
   Each card shows the fault class, filed date, the unanswered question, and reference pages.
   There is no approval action here — gaps are read-only until sources exist.
2. **Discover** — runs source discovery directly from the dashboard, not a handoff: **Discover
   sources…** is a two-phase billed action (a preview click quotes how many gaps would be drained
   and the estimated cost; a second, explicit confirm runs it and stages ranked suggestions).
3. **Approve** — triage discovered source suggestions. One toolbar carries the filter pills
   **pending** / **accepted** (the underlying filter value is `approved`; the label reads
   "accepted" to avoid colliding with the row's own `Approve` button) / **all** with their counts,
   a **Refresh** control, the topic-wide **refused** and **ingested** outcome chips, and the sort
   order.
   - **Sort**: **priority** (the default — reputability score descending, ties broken by discovery
     rank, unrated sources last) or **newest** (the server's own order). Sorting is client-side
     over the records already loaded; when the read has more to give, the toolbar says so.
   - Each suggestion is a collapsed row: a reputability-tier-toned left edge with the tier word,
     the source title as the link, `rep` and `rank` as tabular values, and one provenance line
     (venue · citations · open access · gap-origin badge). The row's disclosure expands the failed
     question, references, snippet, authors, DOI, reputability signals, and fault class /
     generation / provider.
   - Actions on an undecided suggestion: **Approve**, **Reject…** (requires a reason), **Defer** —
     three equal-weight quiet buttons, distinguished by tone rather than by weight. Opening the
     reject form expands the row, so the reason is written against visible evidence.
   - Decided suggestions show the recorded decision inline; rejected-by-gate outcomes show the
     score delta and worst-regressed questions behind the disclosure.
   - The list paginates with a **Load more** cursor.

   > [!IMPORTANT]
   > There is no "mark as ingested" button here. Marking a suggestion ingested is reachable only
   > by calling the underlying tool directly, not from the UI.

4. **Ingest** — a **Handoff Stage** (`/knotica:fill`): Claude writes approved sources and their
   pages into the vault, then gates the ingest. When complete, you resume.
5. **Gate** — see the ingest result (merged, refused with regressed questions, or blocked pending
   a baseline). No action — the result is final.

#### Tend

**Tend** is the per-vault maintenance lane: **Doctor → Lint → OKF → Migrate → Drift**. It merges
the old VaultPane Checks surface with personal marginalia.

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

Two stages carry **Handoff Stages**, where the dashboard pauses and hands work back to your
client's own LLM via a slash command. When the command completes, you resume the lane.

The Handoff Stage carries:
1. **Narration** — a plain-English explanation of what just happened and what needs to happen next.
2. **Dispatch button** — if your host supports it (Claude Desktop Chat with the ext-apps bridge),
   a clickable button that invokes the `/knotica:*` command directly.
3. **Copyable command** — the full slash-command text, always copyable, for any client or host.

**Handoff targets:**
- `/knotica:ingest` — continues the `Learn` lane's **Pages** stage: writes the stored source's
  content into wiki pages. See [../commands/ingest.md](../commands/ingest.md).
- `/knotica:fill` — continues a gap-fill session from the `Fill` lane's **Ingest** stage: writes
  an approved source's pages into the vault and gates the ingest. `Discover` (Fill's second
  stage) is *not* a handoff — it runs as a two-phase billed dashboard action instead. See
  [../commands/fill.md](../commands/fill.md) and [gap-fill.md](gap-fill.md) for the full protocol.

## Legacy `?pane=` Routing

The dashboard's earlier panes (`ask`, `sources`, `ingest`, `improve`, `tend`) are now lanes. Old
bookmarks and URLs using `?pane=` are automatically rerouted to the lane that absorbed their
functionality. The mapping is:

| Legacy `?pane=` | Routed to | Lane | Stage |
|---|---|---|---|
| `ask` | `Answer` | `answer` | Ask |
| `sources` | `Fill` | `fill` | Approve |
| `ingest` | *(removed — no dashboard surface)* | — | — |
| `improve` | `Improve` | `improve` | Observe |
| `tend` | `Tend` | `tend` | Doctor |
| `loop`, `arena`, `datasets`, `golden` | `Improve` | `improve` | Observe |
| `vault`, `notes` | `Tend` | `tend` | Doctor |
| `home`, `learn`, `answer`, `fill` | corresponding lane | — | default stage |

Any unrecognized `?pane=` value defaults to `home` — the same landing a bare URL gets.

## Shared components

Embedded inside other lanes, not top-level:

| Component | Lives in | What it does |
|-----------|----------|---------------|
| Icon set (`icons.tsx`) | Everywhere | 26 inline stroke-SVG glyphs (lane marks, stage-state marks, six Improve stage marks, plus utility icons) — no icon font, no external fetch, always `aria-hidden` and paired with visible or `sr-only` text. |
| InfoPopover / CopyBlock / EmptyState | Chrome, Home, every railed lane's `ⓘ` explanations | The non-modal `ⓘ` overlay ([Chrome](#chrome)), a mono code block with a copy button, and the shared icon/title/sentence/one-action template used for empty and zero states. |
| LoopStrip | Every railed lane | The state-icon strip above the rail — see [The six lanes](#the-six-lanes). Draws its lane/stage copy from `lanes/laneMeta.ts` and `lanes/stageMeta.ts`. |
| Stage-body grammar (`SectionCard`, `Stat` / `StatGrid`, `StateList`, `TermHint`) | Every Improve stage; Answer's React card; Fill's Ingest/Gate stage | The shared layout primitives every stage interior is built from: a titled card with header/footer slots, a label/value stat grid, a list of state-tinted rows, and a click-to-open term explainer that pairs with `InfoPopover`. |
| Scoreboard, prompt diff, promote/delete preview (`ScoreboardPanel`, `PromptDiff`, `PromotePreview`, `DeletePreview`) | Improve lane (Promote stage mainly; `PromptDiff` also in Gate and Prove) | Per-topic baseline summary, unified prompt diffs, and preview-before-apply confirmation for promoting or deleting a candidate branch. |
| Note-promote dialog (`NotePromoteDialog`) | Tend lane (Drift stage) | Offers **Training example** always and **Knowledge gap** for dispute/gap/question notes, from `DriftStage`. |

## Design language

The dashboard is dark-first, mono-voiced, and grayscale-led — a structural grammar (borders,
spacing, uppercase micro-labels) carrying most of the hierarchy, with a small set of semantic
hues (danger/warning/success/info/neutral) reserved for state, never for decoration. A light theme
is still supported via `prefers-color-scheme`; all tokens live in `dashboard/src/theme.css`.

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
