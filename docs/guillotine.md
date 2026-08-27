# Memory Guillotine

A wiki that only ever accumulates content eventually accumulates claims
nobody still believes — nothing in an ingest-only pipeline ever asks "is this
still true?" The Memory Guillotine is Knotica's answer: name a claim, and it
puts every mention of that claim inside one topic on trial.

It finds every passage mentioning the claim, classifies what each passage
*does* with it (asserts, refutes, hedges, merely quotes), scores an overall
risk, and recommends a verdict — from a clean `KEEP` to a full
`DELETE_UNSUPPORTED_SYNTHESIS`. The result is a report, an evidence diff, and
a JSON sidecar, written into the vault for a human to read.

## Contents

- [What it never does](#what-it-never-does)
- [Run a trial](#run-a-trial)
- [Pipeline stages](#pipeline-stages)
- [Passage roles](#passage-roles)
- [Verdicts and the risk score](#verdicts-and-the-risk-score)
- [Dry run vs. `--apply`, and where artifacts land](#dry-run-vs---apply-and-where-artifacts-land)
- [Closing the loop: retracted gaps](#closing-the-loop-retracted-gaps)
- [From pitch to shipped](#from-pitch-to-shipped)
- [CLI reference](#cli-reference)

## What it never does

> [!IMPORTANT]
> The guillotine **never rewrites wiki page prose** — the analysis pipeline is
> read-only, and the only things it writes are its own report artifacts plus
> one catalog bullet in vault-root `index.md`. The `.diff` artifact it
> produces is *evidence for a human*, not a patch the tool applies. Replacing
> a weakened claim with grounded prose happens through the normal
> [gap-fill pipeline](gap-fill.md) instead — a person or the client's LLM
> writes the new content, never the guillotine.
> This was a deliberate reversal from the original design; see
> [From pitch to shipped](#from-pitch-to-shipped).

## Run a trial

```bash
# Dry run (default): report + diff + JSON sidecar only, nothing filed
knotica tend guillotine "reasoning-only systems hallucinate more" --topic agentic-systems

# Force a verdict instead of the recommendation
knotica tend guillotine "<claim>" --topic <topic> --verdict retract --dry-run

# Commit the verdict and open the re-grounding loop
knotica tend guillotine "<claim>" --topic <topic> --apply
```

`--topic` is required and scopes the search to one topic. The guillotine is a
**CLI-only surface** — there is no MCP tool and no dispatcher action for it.
A plugin alias exists, `/knotica:guillotine "<claim>"`, which shells out to
the CLI with `--dry-run` and a topic baked in; point it at your own topic
before running it.

## Pipeline stages

`run_guillotine` composes eight deterministic stages — no LLM call anywhere
in this path:

1. **Scope resolution** — `<topic>/`, plus `sources/<topic>/` by default, plus
   the topic's own `reports/guillotine/` only with `--include-reports` — a
   trial does not cite its own prior reports unless asked.
2. **Lexical search** — expands the claim into keyword variants (full claim,
   token windows, hyphen/space forms), case-insensitive, capped at
   `--max-results`.
3. **Context windows** — extracts ±2 lines around each hit, merging
   overlapping ranges per file.
4. **Classification** — assigns each passage a [role](#passage-roles),
   strength, modality, and risk, from marker heuristics only.
5. **Relevance filter** — drops `IRRELEVANT` passages whose token overlap
   with the claim falls below 50%.
6. **Evidence graph** — sorts survivors into supporting, contradicting,
   qualified, interested, and uncited-source buckets.
7. **Scoring and recommendation** — sums weighted
   [risk factors](#verdicts-and-the-risk-score) into a 0–100 score, mapped to
   a verdict band.
8. **Patch localization** — renders a unified diff showing where a non-`KEEP`
   verdict would touch the page. This diff is never applied.

## Passage roles

Every classified passage gets exactly one role:

| Role | Meaning | Suggested action | Risk |
|---|---|---|---|
| `ASSERTS` | States the claim as fact, no attribution or caveat | `retract` | high |
| `QUALIFIES` | Hedges or attributes the claim | `keep` | medium |
| `CONTRADICTS` | Asserts something incompatible with the claim | `keep` | none |
| `REFUTES` | Explicitly argues the claim is wrong, unsupported, or too broad | `keep` | none |
| `QUOTES` | Quotes the claim without endorsing it | `keep` | low |
| `MENTIONS` | Mentions the claim without asserting or refuting it | `keep` | low |
| `IRRELEVANT` | Overlapping words, unrelated topic | `ignore` | low |

A passage under `sources/` is always treated as raw material, never a
synthesis edit target: its role can only land on `QUOTES` or `MENTIONS`, its
suggested action is always `keep`, and its risk is always `none`.

## Verdicts and the risk score

Seven verdicts exist: `KEEP`, `QUALIFY`, `DEMOTE`, `DISPUTE`, `RETRACT`,
`QUARANTINE_SOURCE`, `DELETE_UNSUPPORTED_SYNTHESIS`. The score-driven
recommendation reaches six of them — the five bands below, plus `DEMOTE` via
the no-refutation downgrade; only `QUARANTINE_SOURCE` needs `--verdict`. The
risk score is clamped to 0–100 and mapped to a band:

| Score band | Verdict |
|---|---|
| 0–25 | `KEEP` |
| 26–45 | `QUALIFY` |
| 46–65 | `DISPUTE` |
| 66–80 | `RETRACT` |
| 81–100 | `DELETE_UNSUPPORTED_SYNTHESIS` |

`QUARANTINE_SOURCE` is never a band output — reach it with `--verdict`.
`DEMOTE` is not a band output either, but the score logic still reaches it: it
downgrades a `DISPUTE`-range score to `DEMOTE` when no actual refutation was
found, since disputing without counter-evidence overstates what the evidence
shows. No assertions and no refutations at all forces `KEEP` regardless of
score.

<details>
<summary>Score factors (what pushes the number up or down)</summary>

Additive: unqualified/universal wording (+25); one supporting source or none
(+20); vendor- or interest-aligned source (+20); high-strength wording (+15);
on an index/overview/synthesis-like page (+15); no counterevidence in scope
(+10); affects user agency or safety framing (+10); claim on 2+ synthesized
pages (+10); synthesized assertions lack citations (+10).

Subtractive: explicitly attributed or qualified wording (−15); already
marked disputed in the wiki (−20); substantial quote or refutation presence
(−20); 2+ independent supporting sources (−10).

The report prints the raw sum, the clamped score, every fired factor, and the
matched band.

</details>

## Dry run vs. `--apply`, and where artifacts land

Both modes run the identical pipeline and write the identical artifacts to
`<topic>/reports/guillotine/`, in a single commit — named from the date and a
slug of the claim. Neither one edits a wiki page: `--apply` records the
verdict as adjudicated, it does not apply the diff.

- `<date>-<slug>.md` — the human-readable report: claim, verdict, risk score
  with every applied factor, claim inventory, synthesis graph, proposed
  changes (shown as a strikethrough diff, never applied), rollback, receipt.
- `<date>-<slug>.diff` — the unified diff. Evidence display only.
- `<date>-<slug>.json` — the same result as structured data.
- One bullet upserted into vault-root `index.md`'s **Reports** section:
  `Guillotine dry-run — verdict X (N/100).` or `Guillotine applied — verdict
  X (N/100).`

| | Dry run (default) | `--apply` |
|---|---|---|
| Report `run_status` | `dry-run` | `applied` |
| Report `## Rollback` section | "not applied; no rollback needed" | a `git revert <sha>` command |
| Precondition | none | rejected if the trial produced zero patches — nothing to adjudicate |
| Retracted gap filed | no | yes, for a weakening verdict |

`--apply` implies `--no-dry-run` — passing both, `--dry-run --apply`, applies.

## Closing the loop: retracted gaps

Four verdicts weaken existing knowledge: `RETRACT`, `DEMOTE`, `DISPUTE`, and
`DELETE_UNSUPPORTED_SYNTHESIS`. `KEEP`, `QUALIFY`, and `QUARANTINE_SOURCE`
file nothing — they don't leave a hole behind.

On a successful `--apply` with a weakening verdict, the guillotine files a gap
with `origin: retracted` — the weakened claim text becomes the gap question
verbatim, and the reason records the verdict plus a pointer to the report.
That gap flows through the normal [gap-fill pipeline](gap-fill.md): diagnose
→ discover → approve → gated ingest, so the claim gets re-grounded in a real
source instead of just vanishing. Filing the gap is isolated from the trial
commit — a failure there leaves the applied verdict standing; re-file by hand.

## From pitch to shipped

The guillotine started as a hackathon pitch. The core argument survived
intact; one design commitment did not — and it's the most important
correction on record:

| Pitched | Shipped |
|---|---|
| "Execute a reversible demotion or removal patch"; wiki pages get edited | No page prose is ever rewritten — the diff is evidence only |
| The claim is removed; that's the end state | An applied weakening verdict opens a gap that flows back through discovery and approval to be re-grounded |
| `DISPUTE` / `DEMOTE` as one undifferentiated band | `DISPUTE` only fires when a refutation actually exists; otherwise it degrades to `DEMOTE` |

Server-side prose rewriting would have violated the client-as-brain invariant
— deterministic server code editing editorial content — and the gap →
suggestion → approved-ingest path already existed to do it properly. Full
reconciliation, verified claim by claim, lives in
[`pitch/memory-guillotine/README.md`](../pitch/memory-guillotine/README.md);
the decision is recorded in
[`dec-033`](../.ai-state/decisions/033-guillotine-verdict-report-only.md).

## CLI reference

| Flag | Default | Behavior |
|---|---|---|
| `claim` (positional) | required | Claim text to search and adjudicate |
| `--topic NAME` | required | Limits the search to this topic |
| `--dry-run` / `--no-dry-run` | `true` | Generate report and patch only |
| `--apply` | off | Commit the verdict and file a retracted gap; implies not dry-run |
| `--verdict NAME` | recommendation | Override the verdict: `keep`, `qualify`, `demote`, `dispute`, `retract`, `quarantine_source`, `delete_unsupported_synthesis` |
| `--json` | off | Structured JSON on stdout instead of the text summary |
| `--include-sources` / `--no-include-sources` | `true` | Include `sources/<topic>/` in scope |
| `--include-reports` | off | Include the topic's own prior guillotine reports in scope |
| `--max-results N` | `50` | Cap on candidate passages |
| `--out PATH` | — | Reserved; not implemented. Artifacts always go to the vault's `reports/` directory |

**Exit codes:**

| Code | Meaning |
|---|---|
| 0 | Trial completed, dry-run or applied |
| 1 | No mentions — or no relevant mentions — of the claim in the topic, or no such topic directory |
| 2 | Bad verdict name, bad topic shape, or invalid arguments |
| 3 | Patch generation failed, or the vault is not configured |
| 4 | The artifact write failed — the apply transaction errored, or the dry-run report could not be written |
