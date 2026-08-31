# Notes

Notes are your marginalia — reflections, disputes, and open questions pinned to the exact
passage of the wiki that provoked them. They live next to the knowledge base but never inside
it: nothing you write here feeds the eval score or wakes the loop, and a search never returns
them unless you ask for them by name.

## Contents

- [Where notes live](#where-notes-live)
- [Anchoring a note](#anchoring-a-note)
- [Reading a note: the resolution ladder](#reading-a-note-the-resolution-ladder)
- [When the wiki changes: supersession vs. rewrite](#when-the-wiki-changes-supersession-vs-rewrite)
- [The drift queue and resolving it](#the-drift-queue-and-resolving-it)
- [Promoting a note](#promoting-a-note)
- [Never scored: the mechanism](#never-scored-the-mechanism)
- [Tools and commands](#tools-and-commands)
- [Dashboard](#dashboard)

## Where notes live

Every note is a file at `notes/<topic>/<note-id>.md`, at the vault root. The filename stem is
also the note's frontmatter `id` and is never renamed after capture. Its shape is
`<YYYYMMDD-HHMMSS>-<slug>`, the slug taken from the note's first heading or opening words (e.g.
`20260805-143022-caching-strategy.md`); a same-second collision gets a letter suffix.

Frontmatter is a fixed field set — an extra key you add survives a reanchor or detach (both
append raw bytes and never rewrite the frontmatter), but the next archive drops it, since that
operation re-serializes the whole note and only ever writes the fields it knows:

| Field | Default | Notes |
|---|---|---|
| `type`, `id`, `topic`, `created` | — | Required to parse at all |
| `schema_version` | `1` | Never re-stamped down on a round-trip |
| `intent` | `reflection` | Writer-stamped: `reflection`, `dispute`, `gap`, `question`. A reader accepts any string, so a hand-typed intent it has never seen still parses |
| `updated` | = `created` | |
| `status` | `active` | `active` or `archived` |
| `tags` | `[]` | |
| `promoted` | `none` | `none`, `gap:<id>`, or `eval:<id>` — see [Promoting a note](#promoting-a-note) |

The body is free text, then an optional `## Anchors` section. A literal `## Anchors` heading
inside your own prose is escaped when the note is written, so prose can never forge an anchor.

## Anchoring a note

Capture is conversational. The caller passes your words verbatim as the note text, the passage
it displayed as a quote, and a best-first list of pages it believes that passage came from. The
server verifies the claim against the vault and pins the strongest anchor it can prove, in
order: **span** (the exact passage) → **page** (the whole page) → **topic** (no page at all).

> [!NOTE]
> Anchoring never fails the capture. A quote it can't find, a missing page, or several matching
> pages just degrades the fidelity and rides back as an `ANCHOR_DEGRADED` warning on an
> otherwise successful save — several matches degrade to topic fidelity rather than guess, with
> the candidates returned as `alternatives`. Capturing with no quote (or no pages at all)
> degrades silently: a normal outcome, not a warning. The only true failures are an unknown
> topic, empty note text, or an unrecognized intent.

Each capture writes exactly one anchor bullet in one commit, and is idempotent by content —
resending the same note against the same quote returns `duplicate: true` rather than a copy.
The grammar, one bullet per anchor of record followed by its verbatim quote (a page-less,
topic-fidelity anchor just omits the wikilink):

```
- [[<vault-path>[#<Heading>]]] — `<fidelity>` · pinned@`<sha>`[ · at=<int>][ · <kind>]
  > <quote>

- `topic` · pinned@`a3f9c21`
  > the passage the user was reacting to, preserved verbatim
```

Anchors are **append-only**: reanchoring or detaching appends a new record
(`kind=reanchored`/`detached`); no earlier record's bytes are touched. The first record (index
0) is the anchor of record and never moves — which anchor is currently trusted is computed
fresh from the history on every read, not stored.

## Reading a note: the resolution ladder

An anchor's status is never cached — every read recomputes it fresh from the quote's location
when pinned, the page's text now, and two configured thresholds, so a resolver improvement
benefits every note already on disk, retroactively.

| Status | What happened | Fidelity |
|---|---|---|
| `exact` | Quote found verbatim, same offset as when pinned | `span` |
| `shifted` | Quote found verbatim, different offset | `span` |
| `fuzzy` | No verbatim match; a scored candidate clears `guess_threshold` | `span` |
| `orphaned` | No confident match. Page gone from the vault entirely → `topic` fidelity, before any scoring runs; original heading still there → guess is that section; heading gone too → guess (if any) is the scorer's best pick | `topic`, `section`, or `page` |
| `unanchored` | The anchor never pointed anywhere — a topic-only note | `topic` |
| `anchor-invalid` | The quote isn't in the *historical* text either — hand-edited or forged | none |

The two thresholds live under `[notes]` in `~/.config/knotica/config.toml` (see
[configuration](configuration.md)): `guess_threshold` defaults to `0.75`,
`complete_orphan_threshold` to `0.35`. Both must be numbers in `[0.0, 1.0]`, and
`complete_orphan_threshold` must sit strictly below `guess_threshold` or the graded-recovery band
between them is empty. A missing file or missing `[notes]` table is not an error — both
thresholds just default.

## When the wiki changes: supersession vs. rewrite

When a page an anchor points at changes, knotica classifies *why* it drifted — never *where* it
lands, which the resolution ladder above already owns. A change counts as **supersession** — the
page was replaced outright, not edited — only when **both** fire together: page similarity below
`0.35`, and zero shared headings between old and new text. Measured in practice, ordinary
rewrites sit at 0.885–0.997 similarity; one real wholesale replacement measured 0.161 and alone
accounted for 85% of observed drift. Anything short of both conditions reports as an ordinary
**rewrite**, however much text changed.

> [!IMPORTANT]
> A deleted page is *not* a supersession — nothing replaced it — and is reported as a rewrite by
> default. Read "rewrite" here as "not a proven wholesale replacement," not "the text changed."

## The drift queue and resolving it

Only three of the six statuses ever queue: `fuzzy`, `orphaned`, `anchor-invalid`. `exact`,
`shifted`, and `unanchored` never do — a note that self-healed, or never pointed anywhere, isn't
drift. Each item carries the pinned quote (always present, verbatim), the live quote (only when
confidently placed), an overlap score, the classified cause (`superseded`/`rewritten`), and any
scored alternative. Rebuilding this queue is a convenience, not a dependency — a read always
resolves an anchor fresh regardless of whether the queue is current.

Resolution happens on the `notes` dispatcher:

| Action | Effect |
|---|---|
| `reanchor` | Re-pins one anchor by its 0-based index. Pass `page` + `quote` to pin explicitly, or leave both empty to accept the anchor's current projection — the queue's one-click accept. Rejects a deleted target page with `PAGE_NOT_FOUND`; the fix names `detach` |
| `detach` | Appends a terminal record saying the anchor no longer points anywhere. The file is kept |
| `archive` | Flips `status` to `archived` and bumps `updated`; no anchor index, no `## Anchors` change, and the file is kept. It is the one note operation that rewrites the frontmatter, so an unmodelled key is dropped. Archiving twice is a no-op |

`reanchor` and `detach` act only on a **live** anchor (not superseded, not detached); a dead or
out-of-range index is rejected before any write. Every mutating action defaults to
`mode=dry-run` (a decision envelope, no write); `mode=apply` performs exactly one commit and
never fires from detection alone.

## Promoting a note

`tend action=notes notes_action=promote` is the only notes action that writes outside the notes layer:

| `target` | What it does | Gate |
|---|---|---|
| `trainset` (default) | Files a curated training example | Needs a grounding page, a question, and the grounded `answer` — a blank answer is rejected; `verdict` must be `good` or `bad` |
| `gap` | Files a reported gap | Only `dispute`/`gap`/`question` notes qualify — a `reflection` is rejected |
| `golden` | Always rejected | Trainset and golden must stay disjoint; promote via golden review instead |

There is no `pages_used` parameter — grounding pages are always derived from the note's own
currently-live anchors (newest record per anchor lineage, excluding a detached lineage, filtered
to anchors that name a page). A `trainset` promotion with zero live grounding pages is rejected
outright; a `gap` promotion is not — it files with no reference pages. Promoting without an
explicit `question` uses the note's own text when its `intent` is already `question`. A `gap`
promotion points back at `note:<path>#0` — anchor index 0, the anchor of record. A `trainset`
promotion records provenance the other way round: `qa.jsonl` carries no note pointer, and the
note itself is stamped `promoted: eval:<qa-id>` in the same commit.

**Contamination rule**: a note's file path never reaches the trainset, the gap queue,
`log.md`, or a commit subject — the one deliberate exception is the `note:<path>#0` pointer on a
filed gap, which exists to trace it back to its source note. The note's *body* is narrower: it
reaches `log.md` only as the capture commit's own title, and reaches the trainset only when
you promote a `question`-intent note whose text you let stand as the question.

## Never scored: the mechanism

Two **independent** mechanisms enforce this, and conflating them is a mistake:

1. **Omission from the scored families.** The eval scalar is fed by two content families —
   `page` and `source`. `note` is not one of them, by design.
2. **Explicit filtering at every point of use.** `notes` is a reserved top-level name, so topic
   enumeration skips it outright. Separately, every knowledge-base-facing surface is expected to
   walk pages through the *filtered* path (which excludes `notes/`), never the raw one. Six
   separate defects have previously come from a surface calling the raw walk and picking up a
   note as if it were a knowledge page — reported as malformed, rewritten by a repair, shipped in
   an export, counted in a lint violation.

Both have to hold: omission keeps notes out of the eval scalar's two families; filtering keeps
notes out of the walks knowledge-base surfaces use regardless of family. Simplifying the
reserved-name check, or reaching for the raw walk instead of the filtered one, would silently
readmit personal notes into scored territory — no error, no failing test. Corollary:
**`search` does not return your notes by default** — its corpus is the scored families, and the
ordinary recall path is `tend action=notes notes_action=list`; a caller that asks for them explicitly
(`families=['note']`) can search them. And because an anchor's quote is verbatim knowledge-base
prose, `reanchor`, `detach`, and `archive` title their commit and `log.md` entry from the note's
id alone (`note <id>`), never the quote. `note_capture` is the exception: it titles both with the
note's own text, collapsed to 72 characters.

## Tools and commands

**`note_capture`** — a flat tool, not a dispatcher action, so capture stays free of an extra
selection step. Params: `topic` and `note` (both required, `note` verbatim), `quote` (`""`),
`pages` (`[]`), `intent` (`reflection`), `tags` (`[]`), `vault` (`""`, selects among configured
vaults). Returns the note's id, path, resolved anchors, any `alternatives`, and a pre-composed
`placement` sentence stating exactly where the note landed.

**`notes`** — everything else, seven actions:

| Action | Effect | Read-only? |
|---|---|---|
| `list` | Paginated recall, filterable by `intent` and anchor `status` | Yes |
| `read` | One note in full, recorded and resolved anchor state | Yes |
| `drift` | The review queue | Yes |
| `reanchor` | Re-pin one anchor | No |
| `detach` | Mark one anchor no longer live | No |
| `promote` | Cross into the trainset or gap queue | No |
| `archive` | Flip a note's status | No |

`list` paginates with an opaque cursor, 20 per page (50 max), and reports `intent_counts` and
`status_counts` for the whole topic alongside the results.

**`/knotica:note <your note>`** — the plugin alias for capture. It infers the topic from the
conversation, recovers the quote and pages from what was just shown to you, calls
`note_capture`, and reports the returned `placement` line verbatim. To browse afterward, it
points you at the `tend` lane's **Drift** stage or `tend action=notes notes_action=list`.

## Dashboard

The **Notes pane** has two views: **browse** (filterable by intent —
`all`/`reflection`/`dispute`/`gap`/`question` — and anchor status —
`all`/`exact`/`unanchored`/`shifted`/`fuzzy`/`orphaned`) and **drift** (the review queue), each
paginated 20 at a time. The Notes tab carries a badge counting notes with at least one drifted
anchor (`fuzzy` or `orphaned`) — a note-level count, so it reads lower than the per-anchor drift
queue — telling you when the queue needs attention without opening it.
