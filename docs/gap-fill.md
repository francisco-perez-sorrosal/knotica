# Gap-fill

The loop can heal a bad prompt. It cannot invent knowledge the vault does not have. Gap-fill is the
path for the other failure: the wiki got a question wrong because the page that would answer it was
never written. Four stages — **diagnose** a regression into a fault class, **discover** sources for
the real knowledge gaps, **approve** what you want ingested, **ingest** it behind the same gate that
guards every other change to the vault.

A regression is not always a knowledge gap. When a golden question scores worse than last generation,
several things could have happened and only two are fixable by adding content. Racing prompt variants
in the [arena](self-improvement.md) against a missing-content regression is worse than useless — a
variant can win by learning to bluff. So P1 classifies first, and the class picks the route.

- [P1 — Diagnose](#p1--diagnose)
- [Where gaps come from](#where-gaps-come-from)
- [P2 — Discover](#p2--discover)
- [P3 — Approve](#p3--approve)
- [P4 — Gated ingest](#p4--gated-ingest)
- [Entry points](#entry-points)

## P1 — Diagnose

Fault classification runs **only** inside a loop regression cycle — it has no CLI or MCP entry point
of its own. It reads the eval clone's manifest, never the live vault, and assigns every regressed
golden id exactly one class by first match down this cascade.

| # | Fault class | Matches when | Route |
|---|---|---|---|
| 1 | `unclassified` | No reference pages are recorded for the golden id | Nothing |
| 2 | `genuine_gap` | The reference pages do not exist in the vault | Filed as a gap |
| 3 | `generation_fault` | A reference page appears in the retrieval trace — the generator had the context and still failed | Arena heal |
| 4 | `dilution` | A reference page is in `pages_removed` and `pages_added` is non-empty — an existing page was displaced by a competitor | Filed as a gap |
| 5 | `retrieval_fault` | Reference pages exist but were not retrieved | Arena heal |

Only `genuine_gap` and `dilution` are ever written to disk; the other three are classifier-internal
states that appear in no record. If *every* regressed id is a knowledge cause, the cycle's route is
`REDIRECT` and the loop skips the arena; any generation- or retrieval-fault id in the mix routes to
`HEAL` — but a mixed regression still persists its knowledge-cause gaps, because the gap write
happens before the route is consulted.

> [!NOTE]
> A newly frozen golden id has no prior generation to regress against, so it would never classify. It
> enters the cascade anyway when its *current* `qa_accuracy` or `quality` falls below **0.5**.
> Freezing a question about content you never wrote does get noticed.

Classification needs a manifest at schema version 2 or higher with a non-null `held_out_delta`; with
anything older no classification is attempted at all and the loop falls through to the plain heal.
Gap records land at `<topic>/.knotica/gaps/gaps.jsonl`, one commit per non-empty write, and a gap
whose `(qa_id, fault_class)` pair is already open is dropped — a regression that persists across
cycles does not spam the queue.

Read that queue back with `gaps_read` — in conversation, or in the dashboard's **Sources** pane,
which lists open gaps above the suggestion cards. This matters most right after you file one by
hand: a gap has no candidate sources until [discovery](#p2--discover) runs, so the suggestion list
is legitimately empty and the gap is the only evidence anything happened.

## Where gaps come from

Every gap record carries an `origin`. Two of the three never touch the classifier.

| Origin | Filed by | What it means |
|---|---|---|
| `measured` | The P1 classifier | A real golden id regressed, with real deltas from the eval manifest |
| `reported` | The `gap_report` MCP tool | You told the wiki, in conversation, that it cannot answer something |
| `retracted` | The [guillotine](guillotine.md) apply path | A claim was retracted, demoted, disputed, or deleted as unsupported synthesis, leaving a hole |

`reported` and `retracted` gaps are synthetic: always `genuine_gap`, all-zero evidence, no backing
manifest, and a `qa_id` that is a deterministic hash of the question or claim text — so filing the
same report twice collides and is silently dropped as already-open. Both refuse an empty question or
claim, and neither fabricates content.

## P2 — Discover

Discovery turns open `genuine_gap` records into ranked source candidates.

```bash
knotica fill discover --topic agentic-systems --max-gaps 3
```

| Flag | Default | Meaning |
|---|---|---|
| `--topic NAME` | *required* | Topic whose gap queue to drain |
| `--max-gaps N` | none — drains every open gap | Cap the number of gaps queried |
| `--vault PATH` | resolved knotica config vault | Vault root override |

The gap's failed question **is** the search text, verbatim — no LLM rewrites it — and each query asks
for up to 10 results. New candidates are deduped against **every** existing suggestion at any status
on `(gap_id, source_key)`: a source you rejected is never proposed again.

**`dilution` gaps are never drained** — a dilution gap's reference page still exists, so there is
nothing to go find, and those records sit in the queue inert. **Under a cap**, gaps carrying a
non-zero `quality_delta` rank by descending `|quality_delta|`, while gaps carrying a zero one —
every `reported`/`retracted` gap by construction, plus any `measured` gap whose delta came out zero
(a newly frozen id, or one that fell only on `qa_accuracy`) — rank by recency, with one slot always
reserved for that bucket. So a human report is not starved behind a run of measured regressions,
though a more recent zero-delta measured gap can claim the reserved slot first.

### Providers and credentials

Configure the chain under `[gapfill.search]` in `~/.config/knotica/config.toml` (see
[configuration](configuration.md)). Keys resolve separately, at use time.

| Setting | Default | Notes |
|---|---|---|
| `provider` | `["youcom"]` | String or ordered fallback list; each name must be recognized |
| `mailto` | unset | Contact email for the OpenAlex polite pool |
| `youcom` credential | — | `KNOTICA_YOUCOM_API_KEY`; the only shipped adapter |
| `exa` credential | — | `KNOTICA_EXA_API_KEY`; recognized by config validation, **no adapter exists** |

> [!WARNING]
> Setting `provider = ["exa"]` passes config validation and then silently builds no provider, even
> with `KNOTICA_EXA_API_KEY` exported. Exa was cut; only the env-var table remembers it.

A key is looked up when a provider is actually built, first hit wins, in this order: the
**process environment**, then `./.env` (relative to the working directory), then
`~/.config/knotica/.env`. The drain reads that one chain, and so does the `discover_on_regression`
probe below — so a key kept only in a `.env` file both switches the loop-side default on and lets
the drain search. Keys never come from `config.toml` or the vault, and are never logged.

Search is a fallback chain: a provider that fails is skipped, not fatal, and the first one returning
at least one candidate wins. Survivors are deduped by `source_key` (DOI first, normalized URL
otherwise), enriched by the keyless OpenAlex `works` endpoint — which stamps citation count, venue,
open-access status, FWCI, and publication date onto anything with a resolvable DOI, and degrades to
un-enriched rather than failing when rate-limited — then scored deterministically on metadata alone
(venue tier, citations, recency; never the title or snippet) and ranked in an explicit total order.

**Offline behaviour is a clean no-op.** With no key configured, no open gaps, or every provider
failing, the drain writes nothing and exits `0` with an informational message — the honest empty
state, not an error.

### Draining automatically after a regression

The loop can run the same drain itself, in its own transaction, right after P1 files gaps. Opt in
under `[gapfill]`; a discovery failure on this path is swallowed, since the CLI is the
error-surfacing entry point.

| Key | Default | Notes |
|---|---|---|
| `discover_on_regression` | conditional — on when a valid discovery key resolves, off otherwise | An explicit `false` is always off; an explicit `true` with no usable key **fails closed to off** and logs it |
| `max_gaps` | `5` | Positive integer; anything else is a config error |

## P3 — Approve

Suggestions live at `<topic>/.knotica/suggestions/suggestions.jsonl` and move through five states.

| Action | Legal from | Moves to | Reason |
|---|---|---|---|
| `approve` | `pending`, `deferred` | `approved` | — |
| `reject` | `pending`, `deferred` | `rejected` | **Required**, non-empty |
| `defer` | `pending` | `deferred` | Optional |
| `mark_ingested` | `approved` | `ingested` | — |

Anything else is rejected with an actionable message. `suggestions_review` defaults to
`mode="dry-run"`, and preview and commit share one pure validation function, so a dry-run can never
disagree with apply. Reads sort newest `proposed_at` first, then rank, then id; page size defaults to
20 and caps at 50, behind an opaque cursor that fails closed if reused under a different filter.

> [!IMPORTANT]
> `status="all"` shows `pending`, `approved`, and `deferred` only — it hides terminal `rejected` and
> `ingested` records. The full five-way breakdown comes back separately as `status_counts`.

The gate stamps its own verdicts, which are **not** human decisions. A `merged` verdict requires the
suggestion to be `approved` and auto-advances it to `ingested`. A `refused` verdict changes no status
at all — the suggestion stays `approved` and re-workable, with a `gate_outcome` attached.

## P4 — Gated ingest

Ingest is a multi-turn handshake so a half-built ingest is never evaluated. The work happens on a
private branch the loop cannot see; only an explicit submit publishes it where the gate looks. The
`source-` infix is how the loop tells a source candidate from a prompt candidate, by branch name
alone.

| Namespace | Role |
|---|---|
| `loop/wip/<topic>/source-<id8>` | Private in-progress ingest session, invisible to the loop |
| `loop/c/<topic>/source-<id8>` | Published candidate awaiting the gate |
| `loop/x/<topic>/source-<id8>` | Quarantined — the gate refused it |
| `loop/r/<sha>` | Result pointer for a completed cycle |

1. **Open.** `source_ingest_open(topic, suggestion_id)` requires the suggestion to be `approved`,
   checked before any worktree is touched. It creates a git worktree under
   `<vault>/.knotica/worktrees/<topic>/source-<id8>/` on a fresh `loop/wip/` branch and returns a
   candidate handle plus provenance. Re-opening **resumes**, never restarts: the response says
   whether source and pages are already present, and surfaces any prior gate outcome so a client
   does not blindly re-ingest a source already proven dilutive. That holds for a *refused* session
   as much as an interrupted one — a refusal renamed the branch into `loop/x/`, so re-opening
   branches from the quarantine ref and reports `resume.restored_from` naming it. The quarantine ref
   is branched from, never moved, so the audit trail survives and a second rework starts from the
   same place.
2. **Write.** Drive `store_source` and `write_page` with `candidate=<handle>`. Each call is its own
   commit on the WIP branch.
3. **Dry run.** `source_ingest_submit(..., mode="dry-run")` reports lint cleanliness (the same
   `lint_check` rules the vault is held to), whether source and pages exist, and whether the topic
   has a frozen baseline, then returns `would_evaluate`. Zero side effects, and it always runs —
   even when a prior verdict is replayable.
4. **Apply.** `mode="apply"` refuses to run without source *and* pages, and returns
   `verdict: "blocked"` without publishing if no baseline is frozen. Otherwise it renames the WIP
   branch into `loop/c/` — **that rename is the readiness boundary**, the first moment the loop can
   see the work — then drives the gate synchronously, polling up to 20 cycles before failing loud
   rather than hanging.

Submit is idempotent **over the question it actually answered**. A stamped verdict is replayed —
flagged `cached: true`, with the key on the response — only while the four things it was computed
from are unchanged: the candidate tree, the golden manifest, the baseline scalar, and the harness
version. Move any one of them and the next submit evaluates afresh. Keying the replay on the
suggestion id alone, as this once did, meant a rebuilt candidate measured against a replaced golden
set and a corrected baseline still returned the original verdict quoting the original bar.

A `merged` verdict is the exception: it is terminal, so it always replays. The work is on the
default branch and the suggestion has advanced to `ingested` — there is no candidate left to re-gate.

An unpublished session is abandoned outright — worktree removed, WIP branch force-deleted; nothing
that was never submitted is ever quarantined, and orphaned WIP worktrees older than 24 hours are
swept best-effort.

### Pass, refuse, quarantine

The gate is one comparison: the candidate's scalar against the topic's frozen
[baseline](self-improvement.md). Source candidates **never** go to the arena on either outcome — that
is the reward-hacking guard, since a content-dilution regression is not prompt-fixable and a prompt
race against it risks a variant that masks the dilution.

On **pass**, the candidate fast-forwards onto the default branch, the suggestion advances
`approved → ingested`, and a best-effort trainset grower runs afterwards over exactly the entity
pages the merge changed. That grower is the one place an LLM call happens *implicitly* inside a
client-driven tool call — `compile action=run`, `datasets action=bootstrap`, and
`loop action=run_eval` reach a model in the server process too, but only because you asked them to.
A missing credential or any failure in the grower is logged and swallowed, never rolling back a
merge that already committed.

On **refuse**, the candidate branch is *renamed* to `loop/x/` — kept, never deleted, full history
intact — and a bounded per-question dilution diff, the 10 worst `quality_delta` questions, is
committed as JSON to `<topic>/.knotica/quarantine/source-<id8>.json` on the quarantine branch itself.
The suggestion stays `approved` with a `refused` gate outcome, so you can read why it lost and rework
it. Quarantine branches are pruned beyond the newest 5 per topic.

**Reworking a refusal**: re-open the ingest (it resumes from the quarantine ref with the source and
pages intact), fix what the diff blamed, and resubmit — the rewritten tree expires the stored verdict,
so the gate evaluates rather than replaying. If instead you decide against the source altogether,
`suggestions_review(action="withdraw")` returns it to `pending` without asserting an ingest that
never happened; `mark_ingested` was previously the only exit from `approved`, which meant releasing
a suggestion required writing a false record.

Note that `pending_candidates` stays empty while a rework is in flight, beside
`refused_awaiting_rework: 1`. That is correct: `loop/wip/` is private until submit publishes it,
which is the guarantee that the gate never evaluates a half-written candidate. `loop action=run_once`
says so explicitly rather than reporting a bare "no pending loop branches".

## Entry points

| Surface | Call | Stage |
|---|---|---|
| CLI | `knotica fill discover --topic NAME [--max-gaps N] [--vault PATH]` | P2 |
| MCP | `gapfill_discover(topic, max_gaps=0, confirm="", vault="")` | P2 (billed, two-phase) |
| MCP | `gap_report(topic, question, reason="", reference_pages=None, vault="")` | P1 (reported gap) |
| MCP | `gaps_read(topic, status="open", cursor="", limit=20, vault="")` | P1 (read the queue) |
| MCP | `suggestions_read(topic, status="pending", cursor="", limit=20, vault="")` | P3 |
| MCP | `suggestions_review(topic, suggestion_id, action, mode="dry-run", reason="", vault="")` | P3 |
| MCP | `source_ingest_open(topic, suggestion_id, vault="")` | P4 |
| MCP | `source_ingest_submit(topic, suggestion_id, mode="dry-run", vault="")` | P4 |

There is no `gapfill decide` or `gapfill ingest` subcommand: approval and ingest are MCP-only by
design — the client's LLM does the judging, the server exposes deterministic tools. `discover` is on
**both** surfaces: it is a pure batch drain with no judgement in it, so the CLI form is a plain
batch command, while the MCP form is two-phase because it spends. A gap is filed and read on the
MCP surface, so leaving the one step between those two on the CLI alone meant a gap could be seen
and never acted on without dropping to a terminal. Per-topic queue counts also
surface in `wiki_status`, including `refused_awaiting_rework` (approved suggestions whose last gate
outcome was a refusal); the [dashboard](dashboard.md) renders the same data.

Code: [`core/gap_classifier.py`](../src/knotica/core/gap_classifier.py),
[`core/gapfill.py`](../src/knotica/core/gapfill.py),
[`core/source_ingest.py`](../src/knotica/core/source_ingest.py),
[`core/source_gate.py`](../src/knotica/core/source_gate.py),
[`discovery/`](../src/knotica/discovery).
