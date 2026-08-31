# Build a knowledge base from scratch

A knowledge base is several topics that grow together. This walkthrough builds one — **LLMs**, split
into five topics — from an empty disk to a wiki that evaluates and defends itself, using only
**Claude Desktop** and the **dashboard**. No CLI.

The companion [tutorial](tutorial.md) takes a *single* topic all the way around the flywheel with a
real paper. Read that one to understand compile; read this one to understand how a whole KB is
assembled and where the two surfaces differ.

Jump to: [What you are building](#what-you-are-building) · [Which surface does what](#which-surface-does-what) ·
[1 Create the KB](#step-1-create-the-knowledge-base) · [2 Add the topics](#step-2-add-the-remaining-topics) ·
[3 Ingest](#step-3-ingest-sources-into-each-topic) · [4 Confirm](#step-4-confirm-what-landed) ·
[5 Ask and curate](#step-5-ask-and-curate) · [6 Golden set](#step-6-freeze-a-golden-set) ·
[7 Compile](#step-7-compile-and-promote) · [8 Defend it](#step-8-set-a-baseline-and-let-the-loop-defend-it) ·
[9 Close gaps](#step-9-close-what-is-missing) · [Where to go next](#where-to-go-next)

## What you are building

A vault named `llms` holding five sibling topics:

| Topic | What goes in it |
|---|---|
| `llm-basics` | tokenization, embeddings, attention, the transformer block, scaling laws |
| `pretraining` | corpora and filtering, objectives, compute-optimal scaling, curricula |
| `mid-training` | continued pretraining, domain adaptation, long-context extension |
| `post-training` | SFT, preference optimization (RLHF/DPO), tool use, safety tuning |
| `reasoning` | chain-of-thought, self-consistency, verifiers, RL on reasoning traces, test-time compute |

Topics are **siblings, not a hierarchy** — the vault has one flat level of topics, and the
relationships between them live in wikilinks between pages, not in nested directories. Each topic
carries its own schema overlay, prompt overrides, dataset, and metrics, created only when that
topic's data justifies it. That is what lets `reasoning` diverge from `llm-basics` without dragging
the other four along.

> [!TIP]
> Five topics is a deliberate choice, not a minimum. Start with one, and add the others as you
> actually have sources for them — an empty topic costs nothing but tells you nothing either.

## Which surface does what

Both surfaces drive the same MCP tools, so the choice is ergonomics — with one real exception.

The dashboard is six process **lanes**, not panes: `home`, `learn`, `answer`, `fill`, `improve`,
and `tend`. Each lane stages its workflow as an ordered rail, so the column below names the lane and
the stage on it.

| Step | Claude Desktop | Dashboard |
|---|---|---|
| Create the KB | `vault action=create` | Vault header → **＋ New KB** |
| Switch active KB | `vault action=use` | KB picker |
| Create a topic | `learn action=create_topic` | Topic bar → **＋ New topic** |
| **Ingest a source** | **yes — chat does the reading** | `learn` lane, monitor only |
| See what landed | `wiki_status` | `tend` lane **Doctor** stage; the topic header everywhere |
| Ask a grounded question | `query` | `answer` lane **Ask** stage |
| Curate an answer | `curate_example` | `answer` lane **Ask** stage → **Save as good** |
| Bootstrap / review / freeze golden | `improve action=datasets datasets_action=…` | `improve` lane **Instrument** stage |
| Compile and promote | `improve action=compile compile_action=…` | `improve` lane **Promote** stage |
| Baseline, eval, loop | `improve action=loop loop_action=…` | `improve` lane **Observe** / **Gate** / **Heal** stages |
| Diagnose, discover, approve a gap | `fill action=gaps_read`, `fill action=gapfill_discover`, `fill action=suggestions_review` | `fill` lane **Gap** / **Discover** / **Approve** stages |
| **Ingest an approved source** | **yes — the handshake needs a model** | no |

> [!IMPORTANT]
> There is no alias layer. The old flat verbs (`create_topic`, `gaps_read`, `datasets`, `loop`, …)
> return an unknown-tool error; each is reachable only as `<lane> action=<verb>`. A verb that owns
> its own `action` parameter takes it as `<verb>_action` on the lane. Full mapping:
> [reference](reference.md#operator-verbs-lane-actions-only).

> [!IMPORTANT]
> **Ingest is the one step the dashboard cannot do for you**, and that is by design rather than an
> omission. Ingest is *client-as-brain*: your Claude reads the source, decides what the entities are,
> and drives `store_source` and `write_page` itself. The dashboard is a deterministic MCP client with
> no model of its own, so it can show you an ingest in flight (the `learn` lane's rail) but cannot
> perform one. Everything else on this page works from either surface.

## Before you start

Install first — [install](install.md) — then, from a checkout:

```bash
make start       # code
make dashboard   # UI at http://127.0.0.1:8765
```

`make ps` tells you what is running. `make creds` tells you which credential the headless steps will
use, without printing it.

Steps 1 through 4 call no model server-side and cost nothing. Steps 5 through 9 do: `query`,
`compile`, dataset bootstrap, eval, and gap-fill discovery each run a model **in the server
process** and need `CLAUDE_CODE_OAUTH_TOKEN` (subscription, preferred) or `ANTHROPIC_API_KEY`
(metered). Every billed action is two-phase — a first call previews model, thread count, and cost
estimate and returns a nonce; only a second call carrying that nonce spends anything. A single click
never bills.

## Step 1. Create the knowledge base

**Dashboard** — in the vault header, click **＋ New KB**, give it a path (say
`~/dev/data/llms`), optionally a name, and optionally a first topic. Leave `make_default` on so the
new KB becomes active immediately.

**Claude Desktop** — ask in plain language:

> Create a new knotica knowledge base at `~/dev/data/llms`, name it `llms`, with a first topic
> `llm-basics`, and make it the active one.

Claude calls `vault action=create`. It scaffolds a **bare** vault — a constitution and your first
topic, no demo content — `git init`s it, commits once, and registers it in
`~/.config/knotica/config.toml`.

> [!NOTE]
> `create` scaffolds a *new* vault. To register a directory that already exists, use
> `vault action=add` instead — it writes config only and never touches the directory's contents.

The vault is a git repo of plain Markdown at a path you chose. Open that folder in Obsidian now; you
will want to read what the wiki writes.

## Step 2. Add the remaining topics

Creating the KB seeds **one** topic. Add the other four.

**Dashboard** — click **＋ New topic** beside the topic picker, type the name, **Create**. The new
topic is selected as soon as the status refreshes. Repeat for each.

**Claude Desktop:**

> In the `llms` knowledge base, create topics `pretraining`, `mid-training`, `post-training`, and
> `reasoning`.

Each `learn action=create_topic` is one commit, and gives the topic an empty `SCHEMA.md` overlay, an empty
`.knotica/datasets/qa.jsonl`, empty `prompts/` and `compiled/` directories, and a section in the
vault's `index.md`. Creating a topic that already exists is a safe no-op.

## Step 3. Ingest sources into each topic

This is the Desktop-only step. Work one topic at a time, and prefer a few authoritative sources per
topic over many shallow ones — the golden set you build later can only be as good as what is on the
page.

> Ingest `https://arxiv.org/abs/2001.08361` into the `pretraining` topic of the `llms` knowledge
> base, citing it as `kaplan2020scaling`.

Claude pulls the ingest protocol itself with `read_protocol`, reads the resolved schema, fetches the
**full text** (never the abstract alone), `store_source`s the raw document under
`sources/pretraining/`, then `write_page`s one entity page at a time — leaf pages before the pages
that link to them, the source page last. Every mutating call is one git commit.

Cross-topic wikilinks are what make this a knowledge base rather than five unrelated wikis. A
`post-training` page on DPO should link the `llm-basics` page on the transformer block; say so when
you ingest, and Claude will write the links.

> [!WARNING]
> Desktop Chat can drop a large mutation at the transport layer — a book-length source split across
> many `write_page` calls may stop midway. Claude Code carries larger payloads; re-run the ingest
> there if Desktop stalls. Nothing in knotica caps the payload, so there is no setting to raise.

## Step 4. Confirm what landed

Switch topics with the dashboard's picker — pages, sources, lint state, and unpushed commits per
topic. The `tend` lane's **Doctor** stage is the vault-health read.
`http://127.0.0.1:8765/?topic=pretraining&lane=tend` deep-links straight to one.

In Desktop, ask for `wiki_status` on a topic. Either way, `pages` should be non-zero and
`to_compile_ready` should be counting down from 30.

## Step 5. Ask and curate

Ask questions whose answers you can check against what you just ingested. In the dashboard that is
the `answer` lane's **Ask** stage; in Desktop, ask Claude to call `query`.

> [!IMPORTANT]
> Only the `query` path is what compile improves. If you simply ask in chat, Claude answers with
> `search` and `read_page` — useful, free, and *not* the engine compile optimizes. Ask through
> `query` if Step 7 is to mean anything.

Judge each answer and save it: **Save as good** / **Save as bad** on the **Ask** stage, or ask Claude to
curate the exchange. Both land on `curate_example` — one appended row, one commit; re-saving an
identical `(query, answer, verdict)` is a safe no-op.

Curate **per topic**. The trainset that compile optimizes is the topic's own `qa.jsonl`, so thirty
good `reasoning` examples do nothing for `pretraining`.

## Step 6. Freeze a golden set

The golden set is an exam, not training data. The `improve` lane's **Instrument** stage walks it in
three moves, each unlocking when its precondition is met:

1. **Bootstrap** — synthesize candidate question/answer pairs from the topic's pages. Billed.
2. **Review** — a human act. Edit and keep the candidates that are actually fair questions, then
   **Save reviewed**.
3. **Freeze** — commit the reviewed candidates as the held-out set, sealed by a `MANIFEST.json`
   checksum.

Freeze refuses any question that overlaps the trainset. That refusal is the whole point: a score
computed on questions the program trained against measures nothing.

## Step 7. Compile and promote

Compile refuses unless three gates hold — health (no `FAIL`, clean worktree), a trainset of at least
**30** query-style examples, and a **frozen** golden set of at least **20** records. "Query-style" is
exact: verdict `good` or `corrected`, and the query does not begin with `ingest `.

The `improve` lane's **Promote** stage runs it. Compile clones the vault, optimizes the query program
with MIPROv2, post-evaluates compiled against baseline over the golden set, and fetches the result
back as a branch — **it never merges for you**. Compiled must *strictly* beat baseline; equal fails.

Then promote in two phases: **Preview merge**, then **Apply merge to main**. Promote takes the vault
lock and merges `--no-ff`, resolving conflicts on exactly two audit paths and aborting on anything
else.

Re-ask your question through `query` afterwards. Same tool, same arguments, different engine.

## Step 8. Set a baseline and let the loop defend it

A baseline is the bar new content must clear. On the `improve` lane's **Observe** stage, run an eval
and set the resulting scalar as the topic's baseline. From then on the loop can observe changes,
compare them against that bar on the **Gate** stage, and — when a change makes answers worse — race
prompt variants in the arena on the **Heal** stage.

A baseline cannot be frozen above what the corpus currently measures: both
`improve action=loop loop_action=set_baseline` and `loop_action=rebaseline mode=best` refuse an
unreachable bar and name `mode=latest` as the exit, because such a bar refuses every future
candidate by construction.

Two things worth internalizing before you trust the chart:

- **`unknown` is a real state.** When the last eval's harness version differs from the baseline's, or
  no baseline exists, the chart reads `unknown` rather than `fail`. Cross-instrument scalars are not
  comparable and the UI refuses to pretend otherwise.
- **The loop is opt-in and it bills.** Running it unattended is `make daemon-install`, a deliberate
  act you take when ready — nothing registers it for you.

Baselines are per topic. Five topics means five baselines, each frozen when that topic's pages are
worth defending.

## Step 9. Close what is missing

When a regression traces to *missing knowledge* rather than a bad prompt, healing the prompt cannot
help. The gap-fill pipeline diagnoses that case, writes the gap to a queue, and — only when you ask
— searches for candidate sources and shows them to you before anything is ingested.

Read the queue with `fill action=gaps_read` or the `fill` lane's **Gap** stage; drain it with
`fill action=gapfill_discover` or the **Discover** stage, which is billed and two-phase. Discovery
needs a search-provider key rather than a model token. You approve or reject each suggestion from
either surface — the **Approve** stage, or `fill action=suggestions_review`.

Ingesting an approved source is the same Desktop-only story as [Step 3](#step-3-ingest-sources-into-each-topic),
for the same reason. It is a four-step handshake — `fill action=source_ingest_open`, then
`store_source` and `write_page` against the returned candidate handle, then
`fill action=source_ingest_submit` dry-run and apply —
and the middle step is your Claude reading the source. Approval and ingest are MCP-only by design.
Note that apply runs the gate synchronously and bills, and returns `blocked` without publishing if the
topic has no frozen baseline. Full pipeline: [gap-fill](gap-fill.md).

This is the step that makes a KB compounding rather than static: the wiki tells you what it does not
know, in the vocabulary of questions it failed to answer.

## Where to go next

| To learn | Read |
|---|---|
| One topic all the way around the flywheel, with a real paper | [tutorial](tutorial.md) |
| How the loop observes, gates, and heals on its own | [self-improvement](self-improvement.md) |
| What each dashboard lane and stage does | [dashboard](dashboard.md) |
| Closing gaps end to end | [gap-fill](gap-fill.md) |
| Private notes that are never scored | [notes](notes.md) |
| Retracting a claim that no longer holds | [guillotine](guillotine.md) |
| Every command, tool, action, and default | [reference](reference.md) |
| Config keys, models, cadence, multiple KBs | [configuration](configuration.md) |
