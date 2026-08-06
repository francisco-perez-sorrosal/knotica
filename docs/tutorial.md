# Tutorial: the flywheel, end to end

Build one topic from nothing and prove it got better. You will ingest the *Agent Workflow Memory*
paper into a fresh `agentic-systems` topic, ask a question you can check, curate the good answers,
compile them into an optimized query program, promote it, and re-ask. Everything here works in
**Claude Desktop Chat** or **Claude Code**; where the two differ, both paths are shown.

Jump to: [Before you start](#before-you-start) ·
[1 Create the topic](#step-1-create-the-topic) · [2 Ingest](#step-2-ingest-the-paper) ·
[3 Confirm](#step-3-confirm-what-landed) · [4 Dashboard](#step-4-open-the-dashboard) ·
[5 Ask](#step-5-ask-the-prove-question) · [6 Curate](#step-6-curate-until-compile-ready) ·
[7 Compile](#step-7-compile-onto-a-review-branch) · [8 Promote](#step-8-promote-the-branch) ·
[9 Re-ask](#step-9-re-ask-and-compare) · [Where to go next](#where-to-go-next)

## Before you start

You need knotica installed and one vault registered and ready:

```bash
knotica doctor --quick
```

No `FAIL` rows means you are set; an unconfigured vault means start at [install](install.md).
Steps 1 through 4 call no model server-side and cost nothing. Steps 5 through 9 do — the exact list of
what bills is in [self-improvement](self-improvement.md).

## Step 1. Create the topic

Ask your client, in either Desktop Chat or Code:

> Create a topic called `agentic-systems` in my knotica vault.

Claude calls `create_topic`. That one commit gives the topic an empty `SCHEMA.md` overlay, an empty
`.knotica/datasets/qa.jsonl`, empty `prompts/` and `compiled/` directories, and a section in the
vault's `index.md`.

> [!NOTE]
> A fresh topic is scaffolded **empty** — no pages, no sources, no `metrics.jsonl`. The
> `agentic-systems` pages you may have seen in the repo's `vault-template/` are fixture data;
> scaffolding strips the demo before your vault is created. You are building this topic from scratch.

## Step 2. Ingest the paper

This is **client-as-brain**: your client's LLM reads the paper and drives deterministic tools. No
server-side credentials are involved.

**Claude Code** — the plugin ships a slash command:

```
/knotica:ingest https://arxiv.org/html/2409.07429 agentic-systems
```

**Claude Desktop Chat** — Desktop does not surface MCP prompts, so ask in plain language and let
Claude pull the protocol itself with the `read_protocol` tool:

> Ingest `https://arxiv.org/html/2409.07429` into the `agentic-systems` topic, citing it as
> `wang2024awm`.

Either way the sequence is the same: read the resolved schema, fetch the full text (never the abstract
alone), `store_source` the raw paper to `sources/agentic-systems/wang2024awm.md`, then `write_page` one
entity page at a time — leaf pages before the pages that link to them, the source page last. Each
`write_page` upserts its own catalog line when you pass `index_entry` (the ingest protocol requires
it), and every mutating call is one git commit subject-formatted
`knotica(<op>): <topic> — <title>`.

> [!WARNING]
> Desktop Chat can drop a large mutation at the transport layer — a book-length source split across
> many `write_page` calls may stop midway. Claude Code carries larger payloads; re-run the ingest
> there if Desktop stalls. Nothing in knotica caps the payload size, so there is no setting to raise.

## Step 3. Confirm what landed

```bash
knotica status --topic agentic-systems
```

In Claude Code, `/knotica:status agentic-systems` runs the same command and summarizes it.

`pages` should be non-zero and `to_compile_ready` counts down from 30. `curated` is the query-style
trainset count, not every row in `qa.jsonl` — see [Step 6](#step-6-curate-until-compile-ready). Add
`--json` for a machine-readable subset. The pages themselves are ordinary Markdown with wikilinks;
open the vault in Obsidian and read them.

## Step 4. Open the dashboard

Ask your client to open the knotica dashboard. On a host that renders MCP Apps — Claude Desktop Chat,
claude.ai — `open_dashboard` renders it inline in a sandboxed iframe. On any other host the tool
returns the standalone URL instead, which you serve yourself:

```bash
knotica mcp --http --port 8765
```

Then browse `http://127.0.0.1:8765/?topic=agentic-systems`. The tabs are Vault, Ask, Loop, Sources,
Notes, Arena, Ingest, and Datasets; `?pane=ask` preselects one. Details in [dashboard](dashboard.md).

## Step 5. Ask the prove question

Use a question whose answer you can check against the source you just ingested.

> How does Agent Workflow Memory improve web agents without changing model weights, and what relative
> gains does it report on Mind2Web and WebArena?

**The grounded answer to look for.** AWM induces reusable workflows from past trajectories and places
them in the agent's memory at inference time — offline from annotated examples, online from its own
predictions judged correct by an evaluator. Reported relative success-rate gains: **24.6% on
Mind2Web** and **51.1% on WebArena**. Citations must include `wang2024awm`.

Two ways to ask, and they are not equivalent:

| Path | How | Requires |
|---|---|---|
| `query` tool | Dashboard **Ask** pane, `/knotica:query` in Code, or ask Claude to call `query` | a server-side model: the `evals` extra plus `CLAUDE_CODE_OAUTH_TOKEN` (preferred) or `ANTHROPIC_API_KEY` |
| Client-as-brain | ask in chat; Claude uses `search` and `read_page` | nothing |

> [!IMPORTANT]
> Only the `query` path is what compile improves. Promoting a compiled artifact swaps the engine
> behind `query`; an answer your client synthesizes in chat never touches it. Ask through `query` if
> Step 9 is to mean anything.

In Claude Code the plugin's MCP server is lean by default. Run `/knotica:headless on`, then reconnect
the server or start a new session — dependencies are chosen when the server process launches, so a
running lean server cannot gain them in place. Then, in the dashboard's Ask pane, submit the question
and click **Pin as Before**: that freezes this answer for the Step 9 comparison.

## Step 6. Curate until compile-ready

Compile refuses unless all three gates hold:

| Gate | Requirement | How to check |
|---|---|---|
| Health | `doctor --quick` reports no `FAIL` **and** the vault worktree is clean | `knotica doctor --quick` — it runs only the config and schema rows; check the tree separately with `git -C <vault> status`, since a dirty tree is a `WARN`, never a `FAIL` |
| Trainset | at least **30** query-style examples in `qa.jsonl` | `knotica status --topic agentic-systems` — `to_compile_ready` reaches 0 |
| Golden set | a **frozen** held-out set of at least **20** records | the Datasets pane, or `datasets action=inventory` |

"Query-style" is exact: verdict `good` or `corrected`, and the query does not begin with `ingest `.
Curating an ingest is useful, but it does not count toward the 30.

**Fill the trainset.** Ask more questions, then save each answer you judge — the Ask pane's **Save as
good** / **Save as bad** button, `/knotica:curate agentic-systems good` in Code, or asking Claude to
curate the exchange. All land on `curate_example`, which appends one row and commits once;
re-submitting an identical `(query, answer, verdict)` is a safe no-op.

**Freeze the golden set.** The Datasets pane walks Bootstrap → Review → Freeze, each step unlocking
when its precondition is met. The CLI covers the two ends:

```bash
knotica eval --bootstrap --topic agentic-systems   # synthesize candidates into staging (bills)
knotica datasets freeze --topic agentic-systems    # commit reviewed candidates as the held-out set
```

Review sits between them and is a human act — edit and keep candidates in the Datasets pane, then
**Save reviewed**. Freeze refuses any question that overlaps the trainset, because the golden set is
an exam, not training data: it is sealed by a `MANIFEST.json` checksum and stays held out, while the
trainset is what compile optimizes against. Conflating the two is what makes a score meaningless.

## Step 7. Compile onto a review branch

```bash
knotica compile --topic agentic-systems
```

The dashboard's **Compile** button on the Vault pane does the same, as does asking Claude to call
`compile action=run`. Compile clones the vault, optimizes the query program with MIPROv2
(`auto="light"`), post-evaluates compiled against baseline over the golden set, writes
`agentic-systems/.knotica/compiled/query_v1.json` plus its manifest on the clone, and fetches the
result back as branch `compile/agentic-systems/<sha12>`. **It never merges for you.**

Two things that surprise people:

- **A MIPROv2 failure is not a failed compile.** Any exception falls back to a bootstrap artifact
  built from your topic's resolved `query.md` verbatim plus up to 8 few-shot demos distilled from the
  trainset. The fallback is never silent — the artifact records `optimizer` (`mipro` or `bootstrap`)
  and a `fallback_reason`.
- **Compiled must strictly beat baseline.** Equal fails, and `compile-state.json` records
  `error: "compiled_not_better"`.

## Step 8. Promote the branch

Promote through the deterministic tool. Do not merge the branch with raw git: promote takes the vault
lock, merges `--no-ff`, resolves conflicts on exactly two audit paths (`log.md` and the topic's
`compile-state.json`) by keeping the default branch's version and aborting the merge on anything else,
and appends a compile metrics record so the promoted scalar shows on the loop chart.

```bash
knotica compile promote --topic agentic-systems --branch compile/agentic-systems/<sha> --dry-run
knotica compile promote --topic agentic-systems --branch compile/agentic-systems/<sha> --apply
```

`--dry-run` and `--apply` are mutually exclusive and one is required; the dashboard's Compile panel
offers the same two phases as **Preview merge** then **Apply merge to main**. Promote refuses a dirty
worktree, a branch not prefixed `compile/agentic-systems/`, and a branch not present locally.

## Step 9. Re-ask and compare

Ask the same question through `query` again. Runner selection now prefers the healthy compiled artifact
over the baseline — same tool, same arguments, different engine. There is no second tool name and no
engine field in the answer envelope. In the Ask pane, re-ask and an **After** card renders beside your
pinned **Before** once the answer text differs.

What to look for: both figures still present with `wang2024awm` cited, and an answer shaped more like
the ones you saved — the compiled program was optimized against your own trainset. One cleanup before
you leave: `compile/*` is the one branch namespace knotica never prunes automatically, so delete the
branch via `branches action=delete` or the dashboard's Scoreboard panel.

## Where to go next

| To learn | Read |
|---|---|
| How the loop observes, gates, and heals on its own | [self-improvement](self-improvement.md) |
| Every command, flag, action, and default | [reference](reference.md) |
| What each dashboard pane does | [dashboard](dashboard.md) |
| Closing gaps the wiki cannot answer | [gap-fill](gap-fill.md) |
| Private notes that never touch the score | [notes](notes.md) |
| Config keys, models, and cadence | [configuration](configuration.md) |
