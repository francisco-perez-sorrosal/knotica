# INTERFACE_DESIGN — notes-overlay

**Agent:** interface-designer (pipeline mode, shadowing systems-architect)
**Task slug:** `notes-overlay`
**Date:** 2026-07-29
**Repo:** `/Users/fperez/dev/knotica` @ `bde9385` (main) — **Vault:** `~/dev/data/knotica`

> **Rev 2 (2026-07-29)** — revised after the orchestrator-mediated adjudication of C1–C4 against
> `SYSTEMS_PLAN.md`. C1, C2, C4 adopted as written. **C3 adopted on the mechanism, overruled on
> the destination**: `promote` now targets the **trainset** via `curate_example`, not golden
> staging. Four architect constraints absorbed: anchors append (no edit operation); tool-captured
> and hand-written notes are indistinguishable to every read path; gap-filing is human-only and
> intent-gated; every orphan ships a best guess **and** the historical text. Changed sections:
> §1 (dispatcher schema + description), §4, §6 (promotion modal, types, `NoteDrift` contract),
> §7 (superseded anchors, indistinguishability invariant), §8 (three new rows), and the
> `## Architecture Challenges` outcomes.

## Scope

I own **how the notes layer is reached**: tool decomposition, the capture handshake, the
protocol-or-not call, skill routing, the slash command, the dashboard pane, the on-disk shape a
human sees in Obsidian, the error grammar, and CLI parity.

The systems-architect owns storage layout, the anchor data model, the re-anchoring algorithm,
and the eval-bridge internals. I design against the handed-down contract:

```
anchor.fidelity ∈ { span, block, section, page, topic }      # how precisely it is pinned
anchor.status   ∈ { exact, shifted, fuzzy, orphaned }        # how the live projection stands
```

Everything below treats that as a parameter. I do not invent the algorithm.

### Hats applied

| Hat | Why |
|---|---|
| `agentic-interface-design` | **Primary.** The consumer at the decisive moment is an LLM mid-answer. |
| `tui-design` | CLI parity + the SessionStart nudge channel. |
| `web-ui-design` | Dashboard NotesPane (five UI states, WCAG, never-color-alone). |
| `api-design-craft` | Bloch review lens on the tool surface; error contract; pagination. |

### The one variable everything is optimized for

**Capture friction.** Annotation systems die when capture costs more than the thought itself.
Every trade below is stated with what it cost. Where a design virtue lost, I say so.

---

## 1. Tool decomposition

### The split

Note **capture** and note **management** are different acts with different consumers, and
`dec-045` already gives the rule for each.

| | Capture | Management |
|---|---|---|
| Selected by | the client model, mid-answer, unprompted | a human at a dashboard / an explicit operator ask |
| Frequency | the most conversational act in the product | rare, deliberate |
| `dec-045` ruling | conversational core → **flat/thin tool** | operator/long-tail → **`action=` dispatcher** |

### Decision

- **One new flat tool: `note_capture`.**
- **One new dispatcher: `notes`,** with 7 actions.
- Net tool count: 31 → **33** (24 flat + 9 dispatchers).

**Why a new dispatcher and not an action on an existing one.** Notes are a genuinely new domain.
`vault` is config-level (never touches vault contents beyond its one-time scaffold);
`datasets`/`golden` are eval-corpus surfaces and notes are *never scored*; `vault_health` is
mechanical conformance and notes are exempt from the page contract. Folding notes into any of
them would import the notes lifecycle into a domain whose invariants exclude it. `vault` itself
is the precedent for a new dispatcher appearing post-consolidation.

**Why capture does not become `notes action=capture`.** Two reasons, and the second is the
load-bearing one:

1. A dispatcher costs the model an extra reasoning hop — select the dispatcher, then pick the
   action from an enum of 7, then fill the union-of-all-actions argument set. That hop is fine
   for a deliberate operator act. It is not fine for the one act that must feel free.
2. `dec-045`'s own carve-out: a capability that *is* conversationally selected belongs in the
   flat core (the precedent set by `gap_report`, `source_ingest_open`, `suggestions_read` — all
   flat despite post-dating the consolidation).

**Registered objection to my own decision.** The flat core is *already* over `dec-045`'s
~20–25 selection-quality ceiling at 31 tools. `note_capture` makes it worse by one. I am
spending that slot anyway because capture friction is the feature's survival condition and the
dispatcher hop is a direct tax on it. The honest accounting: **I traded tool-surface economy for
capture friction, deliberately, once.** The corresponding discipline is that *nothing else*
about notes gets a flat tool — recall included (see below).

**Why recall (`"what did I note about X?"`) does not get a second flat tool.** Recall is
deliberate and user-initiated; the extra hop is affordable there. It routes to
`notes action=list`. Note that this is the *only* recall path: notes live at vault root
`notes/<topic>/`, which is outside the retrieval corpus and outside topic-scoped `search`
scan dirs — so `search` will never surface a note. The `notes` description leads with `list`
for exactly this reason, and the skill carries the routing.

### `note_capture` — flat, conversational

```jsonc
{
  "name": "note_capture",
  "inputSchema": {
    "type": "object",
    "required": ["topic", "note"],
    "properties": {
      "topic":   { "type": "string",
                   "description": "The topic this note belongs to (always explicit)." },
      "note":    { "type": "string",
                   "description": "The user's reflection, VERBATIM. Never paraphrase or tidy it." },
      "quote":   { "type": "string", "default": "",
                   "description": "The verbatim passage you displayed that provoked the note. Copy it exactly from your own output -- do not reconstruct it from the pages." },
      "pages":   { "type": "array", "items": {"type": "string"}, "default": [],
                   "description": "The pages you actually synthesized that passage from, best-first. Your honest provenance claim, not a guess." },
      "intent":  { "type": "string", "enum": ["reflection", "dispute", "gap", "question"],
                   "default": "reflection",
                   "description": "reflection = private, stays in the notes layer forever (default). dispute/gap/question mark it as promotable, but promotion is still a separate, human-gated act." },
      "tags":    { "type": "array", "items": {"type": "string"}, "default": [] },
      "vault":   { "type": "string", "default": "" }
    }
  }
}
```

**Description string** (the executable interface — written in the codebase's voice, guard clause
included):

> Save one personal note (marginalia) against a topic, anchored to the KB passage that provoked
> it. Pass the user's words VERBATIM as `note` -- never paraphrase, summarize, or improve them.
> Pass the passage you displayed as `quote`, copied exactly from your own output, and the pages
> you actually synthesized it from as `pages`; the server verifies that claim against the vault
> and pins the strongest anchor it can prove -- span, block, section, page, or topic. **The note
> is always saved: a weak or unprovable anchor degrades the pin and rides back as an
> `ANCHOR_DEGRADED` warning, never a failure.** Writes only under `notes/<topic>/` -- never a
> wiki page, never a dataset, never the loop; a note cannot change what the wiki says or how it
> scores. `intent` defaults to `reflection` (private, stays here); `dispute`/`gap`/`question`
> only *mark* a note as promotable -- crossing into the KB is a separate human-gated act. One
> commit; requires the lock. Idempotent by content: re-sending the same note for the same quote
> is a no-op. Call this when the user's message **is** the note -- an addressed remark ("note
> this", "worth remembering:", "I've never bought that argument") or an explicit reflective
> aside about what they just read. Never infer a note from the user merely reacting or thinking
> aloud, and never write one on their behalf; an unaddressed reaction routes to an offer ("want
> me to note that?") instead.

**On the guard clause — a deliberate, principled adaptation.** Every other mutating tool reads
"never call this from detection alone -- only after the user has explicitly confirmed the
write." Applied literally to capture, that mandates a confirmation round-trip on every note,
which doubles the cost of the act and kills the feature. The guard's *purpose* is that the user's
intent must be explicit, not that a second turn must occur. For capture, the user's intent is
explicit **in the message that is the note**. So the clause is re-specified on that axis:
addressed remark → capture; unaddressed reaction → offer. This is a narrowing of what counts as
explicit intent, not a weakening of the read/offer discipline. It is flagged for the architect
in `## Architecture Challenges` § C4 because it is the one place I depart from a
codebase-wide verbatim convention.

### `notes` — dispatcher, operator

Actions: `list` · `read` · `drift` · `reanchor` · `detach` · `promote` · `archive`

```jsonc
{
  "name": "notes",
  "inputSchema": {
    "type": "object",
    "required": ["action"],
    "properties": {
      "action":  { "type": "string",
                   "enum": ["list","read","drift","reanchor","detach","promote","archive"] },
      "topic":   { "type": "string", "default": "" },
      "note_id": { "type": "string", "default": "" },
      "mode":    { "type": "string", "enum": ["dry-run","apply"], "default": "dry-run" },
      "intent":  { "type": "string", "default": "all",
                   "description": "list filter: reflection|dispute|gap|question|all (default all)." },
      "status":  { "type": "string", "default": "all",
                   "description": "list filter: exact|shifted|fuzzy|orphaned|all (default all)." },
      "anchor":  { "type": "integer", "default": 0,
                   "description": "reanchor/detach: which of the note's anchors, 0-based." },
      "page":    { "type": "string", "default": "",
                   "description": "reanchor: the page to re-pin to. Empty = accept the projected match." },
      "quote":   { "type": "string", "default": "",
                   "description": "reanchor: the new passage to pin to. Empty = accept the projected match." },
      "target":  { "type": "string", "enum": ["trainset","gap","golden"], "default": "trainset",
                   "description": "promote destination. 'trainset' appends a curated example to the topic's qa.jsonl (default). 'gap' files it in the gap queue -- only for notes whose intent is dispute, gap, or question. 'golden' is DEFERRED and always rejected: trainset-vs-golden is a one-way door (freeze() enforces disjointness), so it needs the golden_review gate, not this action." },
      "question":{ "type": "string", "default": "",
                   "description": "promote: the question the wiki should answer, as the user phrased it. Defaults to the note's own text when the note already is a question." },
      "answer":  { "type": "string", "default": "",
                   "description": "promote target=trainset: the grounded answer, cited from the anchored pages." },
      "verdict": { "type": "string", "enum": ["good","bad"], "default": "good",
                   "description": "promote target=trainset: whether the wiki's answer was good or bad." },
      "reason":  { "type": "string", "default": "" },
      "cursor":  { "type": "string", "default": "" },
      "limit":   { "type": "integer", "default": 20, "minimum": 1, "maximum": 50 },
      "vault":   { "type": "string", "default": "" }
    }
  }
}
```

**Description string:**

> Browse and maintain the personal notes layer for one topic -- the marginalia written with
> `note_capture` or by hand in Obsidian. `action=list` is the recall path ("what did I note
> about this?") -- notes live outside the wiki corpus, so `search` will never find them; filter
> by `intent` and anchor `status`, paginate with the opaque cursor (default 20, max 50).
> `action=read` returns one note in full with its anchors, the pages it points at, and the notes
> it links to. `action=drift` is the review queue: every note whose anchor no longer projects
> cleanly onto the live page, always showing the text you originally pinned next to the best
> surviving guess -- even a total orphan is never a dead end. `action=reanchor` re-pins one
> anchor; `action=detach` records that the note is no longer about that passage. Neither edits or
> removes anything: both **append** a new anchor record -- `detach` appends a terminal
> `detached` record -- and the newest record is the note's effective anchor, so the full pin
> history stays readable in the file and in git. `action=promote` crosses the boundary
> out of the notes layer: `target=trainset` (default) appends one curated
> (question, pages, answer, verdict) example to the topic's training set, grounded in the note's
> **anchored wiki pages** -- never the note itself, which is not part of the wiki corpus;
> `target=gap` files it in the research queue and is offered ONLY for notes whose intent is
> dispute, gap, or question, never a plain reflection; `target=golden` is deferred and always
> rejected -- the held-out set is a one-way door and needs its own review gate.
> `action=archive` retires a note without deleting it (notes are the user's; the server never
> destroys one). `mode=dry-run` previews; `mode=apply` performs exactly one commit. Every
> mutating action writes only under `notes/<topic>/` -- except `promote`, which by design writes
> one record into the topic's training set or gap queue. `mode=apply` never fires from detection
> alone -- only the dashboard operator invokes it, or the user has explicitly confirmed the
> change; an unconfirmed detection routes to `notes action=list` or an offer instead.

### Bloch review of the surface

| Principle | Check |
|---|---|
| Minimal surface area | 2 new tools total. Recall, graph, and prune all fold into existing actions rather than earning their own. No CLI subcommand (§9). |
| Names matter | `note_capture` says the act, not the noun. `drift` is the user's word for the problem, not `reanchor_candidates`. `archive` not `delete` — the name enforces the invariant. |
| Hard to misuse | `intent` defaults to the private value. `mode` defaults to `dry-run`. Anchoring cannot fail the call, so there is no "lost my note" failure mode to misuse. |
| Fail fast | `note_capture` validates topic/intent/note-emptiness before touching the lock. |
| Least astonishment | `promote` is the **only** action that can affect anything outside `notes/`, and its name says so. |
| Consistency | `dry-run`/`apply`, opaque cursor, `status_counts`, decision-envelope on dry-run — all mirror `suggestions_review`. |

---

## 2. The capture handshake

### One-shot. Not two-phase.

`source_ingest_open`/`submit` earns its ceremony because a whole phase of client cognition and
many writes sit between open and submit — the handle exists to scope those writes onto an
isolated branch. **Note capture has no such middle.** At the moment of capture the client already
holds every input: the passage it displayed, the pages it cited, the user's sentence, the intent.
A two-phase handshake here would add a round trip and a failure mode in exchange for nothing.

Two-phase would also be actively harmful: an `open` that can fail on anchor resolution puts the
user's thought at risk between the two calls. One-shot means **the note is durable before any
refinement is discussed.**

### The invariant that makes one-shot safe

> **`note_capture` fails only for reasons unrelated to anchoring.**

Hard errors: topic not found, empty `note`, invalid `intent`, `LOCK_BUSY`, `GIT_ERROR`.
Everything anchor-shaped — quote not found in the claimed page, quote found in three pages, the
claimed page does not exist, no quote supplied at all — **degrades the fidelity and returns a
warning on the success envelope.** This is the single most important design call in the document
and it is a direct application of "store first, refine optionally."

### Return payload

```jsonc
{
  "topic": "agentic-systems",
  "note_id": "2026-07-29-reward-hacking-is-goodhart",
  "path": "notes/agentic-systems/2026-07-29-reward-hacking-is-goodhart.md",
  "intent": "reflection",
  "anchors": [
    { "index": 0,
      "page": "agentic-systems/alignment-failures.md",
      "heading": "Reward hacking",
      "fidelity": "block",
      "status": "exact",
      "quote": "the model learns to satisfy the metric rather than the goal",
      "pinned_at": "a3f9c21" }
  ],
  "alternatives": [],
  "placement": "Saved as a reflection, anchored to the \"Reward hacking\" section of alignment-failures (block-level, exact).",
  "written": true,
  "duplicate": false,
  "commit": "7c1e04b"
}
```

**`placement` is a pre-composed sentence, not data to be composed.** This is the agent-ergonomics
call: the model must be able to tell the user where the note landed in one line, immediately,
without re-reasoning over `fidelity` × `status` × heading presence. Handing it a ready sentence
removes the chance of it inventing a wrong location and removes the tokens it would spend
formatting one. It costs the server a small string table. Worth it.

**`alternatives`** is populated only on ambiguity — a list of `{page, heading}` the agent can offer
as a one-word refinement *after* the note is already safe.

> **Corrected 2026-07-30, during Phase 2 implementation.** This paragraph said `{page, heading,
> quote}`, contradicting its own example below. The example is right: the quote is *identical* on
> every alternative — that is precisely what makes them ambiguous — so a per-entry copy is pure
> redundancy. There is deliberately no `overlap` either, unlike the drift queue's alternatives:
> every candidate here matched the quote **verbatim**, so nothing was scored, and an `overlap: 1.0`
> would invite a consumer to sort or threshold on a comparison that never ran.
>
> The example's `"status": "fuzzy"` on a `"fidelity": "topic"` anchor is also wrong and predates
> the shipped status vocabulary. An anchor that never pointed at a page is **`unanchored`**;
> `fuzzy` means a scored span match against a real page, and cannot co-occur with `topic` fidelity.

### Degraded example (ambiguous match)

```jsonc
{
  "note_id": "2026-07-29-i-never-bought-this",
  "anchors": [ { "index": 0, "page": "", "fidelity": "topic", "status": "fuzzy", "quote": "..." } ],
  "alternatives": [
    { "page": "agentic-systems/alignment-failures.md", "heading": "Reward hacking" },
    { "page": "agentic-systems/eval-design.md",        "heading": "Metric gaming" }
  ],
  "placement": "Saved as a reflection against the topic -- that passage appears in 2 pages, so I could not pin it precisely.",
  "warnings": [
    { "code": "ANCHOR_DEGRADED",
      "message": "the quote matched 2 pages, so the note is pinned at topic level rather than to a passage",
      "fix": "Offer the user the alternatives; call `notes action=reanchor` with the chosen page to pin it precisely." }
  ],
  "written": true, "duplicate": false, "commit": "b0d3f19"
}
```

### End-to-end transcript — the natural-language path

```
User    Give me a rundown of how reward hacking shows up in agentic evals.

Claude  [wiki_status(view="scope")] -> topic 'agentic-systems' covers this
        [query(topic="agentic-systems", question="how does reward hacking show up
               in agentic evals?")]

        Two mechanisms show up repeatedly. First, specification gaming: the model
        learns to satisfy the metric rather than the goal, so a rubric that rewards
        "cites a source" gets citations that do not support the claim. Second,
        eval-set memorization ...

        Sources: [[alignment-failures]], [[eval-design]]

User    huh — "learns to satisfy the metric rather than the goal" is just Goodhart
        with extra steps. we never test the case where the metric IS the goal.

Claude  [note_capture(
           topic  = "agentic-systems",
           note   = "just Goodhart with extra steps. we never test the case where
                     the metric IS the goal.",
           quote  = "the model learns to satisfy the metric rather than the goal",
           pages  = ["agentic-systems/alignment-failures.md"],
           intent = "reflection")]

        Noted. Saved as a reflection, anchored to the "Reward hacking" section of
        alignment-failures (block-level, exact).

User    actually that second bit is a real hole in the wiki, not just my opinion

Claude  Want me to file it as a gap so it enters the research queue? That crosses
        out of your private notes into the KB's work queue.

User    yeah

Claude  [gap_report(topic="agentic-systems",
                    question="How should an eval test the case where the metric
                              itself is the goal?",
                    reason="wiki has no coverage of metric-as-goal test design",
                    reference_pages=["agentic-systems/alignment-failures.md"])]

        Filed as gap g-0f21 (origin: reported). Your note stays exactly where it is.
```

**Why the escalation is a `gap_report`, not `notes action=promote target=gap`.** The note was
captured as a `reflection`, and gap-filing is intent-gated to `dispute | gap | question` —
`promote target=gap` would (correctly) reject it. That gate is doing real work here: a reflection
is the user's thinking, and the moment they reclassify it as *"a real hole in the wiki"* they are
making a fresh assertion about the KB, which is exactly what `gap_report` is for. `promote
target=gap` exists for the case where the note was *already* a dispute or question when written
— where the escalation is a promotion of something the user had already framed that way, not a
reframing. Two paths, and the note's own `intent` decides which one is available.

Turn count from thought to durable note: **one**. No confirmation round-trip, no protocol load,
no follow-up question. That is the whole design.

**What I traded.** The user is not shown a preview before the write. That violates the
dry-run-first convention every other mutating surface follows. I accept it because the write is
(a) confined to the user's own private layer, (b) never destructive, (c) reversible by editing
the file in Obsidian, and (d) content-idempotent. A preview here would buy a safety property
that is already free and would cost the only thing that matters.

---

## 3. Protocol or no protocol

### Decision: **no fifth `read_protocol` operation.** The tool description plus one skill symptom block carry it.

`read_protocol(operation, topic)` currently serves `ingest | query | lint | curate`. Adding
`note` is a 6-file coupled change (`core/prompts.py` `OPERATIONS`, the vault template's
`.knotica/prompts/note.md`, the `Literal` on `tools_guide.py`, an `@mcp.prompt` registration,
`wiki-maintenance`, `cli/prompt.py`) **plus a vault migration** — every existing vault raises
`NOT_CONFIGURED` for the new operation until re-migrated.

Four reasons it does not earn that:

1. **It would be a drift-prone second copy.** The four operations are multi-step protocols over
   the KB. Note capture is one deterministic call. A `note.md` prompt whose body is "call
   `note_capture` with these four arguments" is exactly the duplication `server.py`'s comment
   forbids for `_INSTRUCTIONS` — one source of truth, and here it is the tool description.
2. **Latency at the worst possible moment.** A protocol load is a round trip that lands between
   the user's thought and the note. On the single act where friction is fatal, we would be
   inserting the most expensive interaction in the system.
3. **There is nothing to optimize.** The operation prompts are the DSPy/SIA-evolvable substrate
   — they are optimized against a metric. Notes are **never scored** (locked, and a health
   guard). A `note` prompt would sit in the evolvable substrate with no gradient, permanently
   dead weight in the thing the product's whole thesis is built on.
4. **The migration tax is real and buys nothing.** Every existing vault would need a file
   created before an operation that needs no vault-resident text works at all.

**Where the judgment goes instead.** There *is* judgment in this feature — but it is routing
judgment (note vs. gap vs. source) and review judgment (promote or not, reanchor or detach), not
step-sequence procedure. `wiki-maintenance` explicitly owns judgment and defers procedure to the
prompts. Both pieces belong there. §4 writes them.

**Objection I am registering against the likely instinct.** The project's pattern is "every
client-as-brain capability is a protocol," and applying that reflex here is wrong: it confuses a
*multi-step protocol* with a *single call with judgment about when to make it*. The four existing
operations are the former; note capture is the latter. If the architect wants notes in the
evolvable substrate, the honest place is a section inside the existing `query.md` prompt
("offer to note a reflection at the end of an answer") — additive, no migration, no new
operation, and it puts the flywheel offer exactly where the flywheel already lives. I recommend
that as the follow-on, not a fifth operation.

ADR fragment: `dec-056`.

---

## 4. `wiki-maintenance` skill routing

Three writes look alike from the outside and land in three different layers. Getting this wrong
writes to the wrong layer, so the disambiguation is stated as a single axis, not a keyword list.

### The disambiguation axis: **what is the user asserting about?**

| The user is asserting about… | Route | Layer touched |
|---|---|---|
| **their own thinking** — a connection, a doubt, a thing to revisit | `note_capture` (`intent=reflection`) | `notes/` only |
| **the wiki being wrong or thin, and they want it fixed** | `gap_report` | gap queue → research |
| **external material worth capturing** (URL, paper, doc) | ingest / `store_source` | `sources/` + pages |

**The tie-breaker question:** *does the user want the KB to change?*

- Yes → `gap_report`.
- No, they want their disagreement on the record → `note_capture(intent="dispute")`.
- Unclear → **note it, then offer to escalate.** The notes layer is the only write that cannot
  affect the wiki, so it is the safe default; a note can be promoted later, a gap cannot be
  un-filed as cheaply. Make this asymmetry explicit in the skill — it converts an ambiguous
  routing decision into a safe one.

### Exact frontmatter `description` addition

Append to the existing symptom list (before the final "It teaches how to…" sentence):

> …; **a personal reaction to something the wiki just said — a connection the user drew, a doubt,
> a disagreement, or a "worth coming back to" aside;**

### Exact body addition — new bullet in "Detecting wiki-relevant conversation"

Insert as the fourth bullet, before "Loop / suggestion / status talk":

```markdown
- **A personal reaction** — the user responds to something *you just told them from the wiki*
  with a thought of their own: a connection ("this is just Goodhart with extra steps"), a doubt
  ("I've never bought that argument"), or a marker ("worth revisiting when we redo the eval").
```

### Exact body addition — new section after "When to reach for which operation"

```markdown
## Notes: the user's own layer

A **note** is the user's marginalia on the wiki — their thinking, not the wiki's knowledge. It
lives in `notes/<topic>/`, is never scored, never feeds an eval, and never changes what the wiki
says. That makes it the one write that is always safe.

Capture with `note_capture` when the user's message **is** the note: an addressed remark ("note
this", "worth remembering:") or a reflective aside about what they just read. Pass their words
verbatim, the passage you displayed as `quote`, and the pages you synthesized it from as
`pages` — the server pins the strongest anchor it can prove and tells you where it landed in one
line. Never infer a note from the user thinking aloud, and never write one for them; an
unaddressed reaction gets an offer ("want me to note that?"), not a write.

**Note vs. gap vs. source — one question decides it: does the user want the KB to change?**

- No, they want their own thought on the record → `note_capture`. Disagreement with the wiki
  that is theirs to hold, not a fact to fix, is `intent="dispute"`.
- Yes, the wiki is wrong or missing and should be fixed → `gap_report`. Do not file the same
  thing twice: a note and a gap are different acts.
- They handed you external material to capture → ingest.

**When you cannot tell, note it and offer to escalate.** A note is private and reversible; a gap
enters a research queue. Promotion (`notes action=promote`) crosses that boundary explicitly and
only with the user's go-ahead — it is never implied by `intent`, and filing a gap this way is
offered only for notes whose intent is dispute, gap, or question. A plain reflection is not
promotable to a gap; if the user decides the wiki really is wrong, that is a fresh `gap_report`.

Promotion's other destination is the topic's **training set** — a curated (question, answer,
verdict) example grounded in the pages the note is *anchored to*, never in the note itself. Notes
are not part of the wiki corpus, so an eval question drawn from one must still be answerable from
the wiki.

To recall notes, use `notes action=list`: notes sit outside the wiki corpus, so `search` will
never find one.
```

**Cost:** three text edits to one existing file. No new file, no `references/` split.

---

## 5. `/knotica:note`

**Shape: agent-guided (the `create.md` pattern), but minimal.** The thin CLI-delegating shape
(`ingest.md`) is structurally wrong here — it shells out to `knotica prompt <op>` to fetch
protocol text, and there is no protocol (§3). More decisively: the capture's most valuable
inputs — the passage just displayed and the pages it came from — exist **only in the
conversation**. A CLI bang-line cannot see them, so a delegating command would produce
topic-fidelity notes every time.

The command's one discipline: **it must not ask a question it can infer.** Every
`AskUserQuestion` is a turn, and turns are the cost being minimized.

### `commands/note.md`

```markdown
---
description: Save a personal note (marginalia) against a KB topic, anchored to the passage that provoked it.
argument-hint: "<your note>"
allowed-tools:
  - AskUserQuestion
  - mcp__plugin_knotica_knotica__*
---
Save the user's note. Their words are `$ARGUMENTS`. Do not paraphrase, tidy, or expand them.

1. **Infer, do not ask.** Take the note text from `$ARGUMENTS`; if it is empty, ask once for it
   and nothing else. Take the topic from the conversation — the topic you last queried or
   discussed. Only if no topic is inferable, call `wiki_status(view="scope")` and ask once,
   offering the vault's topics.
2. **Recover the anchor from your own output.** If you displayed a passage from the wiki that
   this note is reacting to, pass it verbatim as `quote` and the pages you synthesized it from
   as `pages`. If there is no such passage (the user is noting cold), pass neither — the note
   anchors to the topic and that is a valid outcome, not a failure.
3. Call `note_capture` with `topic`, `note`, `quote`, `pages`, and `intent` (`reflection`
   unless the user's words are plainly a disagreement -> `dispute`, or a question -> `question`).
4. Report the returned `placement` line verbatim — one line, nothing added. If `alternatives`
   came back, offer the choice in one sentence: the note is already saved, this only sharpens
   the pin.

To browse or re-anchor existing notes, use the dashboard's Notes pane or `notes action=list`.
```

**Cost:** one new file, ~20 lines, no registration step (commands are directory-discovered).

---

## 6. Dashboard NotesPane

### Honest cost, and the trade I made to control it

`SourcesPane` is 427 lines for **one** card type and four actions. A NotesPane with four
full views (browse, drift queue with diff rendering, graph, promotion) is realistically
**650–800 lines** plus a graph-layout dependency the dashboard does not currently carry. That is
the largest single cost in this design and it is not worth paying in v1.

**What I cut and why:**

| View | v1 | Rationale |
|---|---|---|
| Browse / filter | **Ship** | The pane's reason to exist. |
| Drift review queue | **Ship** | The only surface where drift is actionable; without it, `A3` has no home. |
| Promotion | **Ship, as a card action** — not a view | It is one button plus a confirm, not a screen. |
| Notes graph | **Degrade to a text "Linked" line on the card** | A rendered graph needs a layout library and earns nothing a backlinks list does not. Obsidian already renders the real graph, natively, over the same wikilinks (§7). Building a second, worse graph next to a native one is the definition of an unearned element. |

**v1 estimate: ~460–520 lines** (two sub-views + one card component + one diff renderer), in
line with the `SourcesPane` precedent. The graph view is deferred, not designed away — if it
returns, it returns as `notes action=graph`, and it should have to justify itself against
Obsidian's.

### View 1 — Browse

```
┌─ Notes ─ agentic-systems ──────────────────────────── 34 notes · 3 drifted ─┐
│                                                                             │
│  [ all ] [ reflection ] [ dispute ] [ gap ] [ question ]      ⟳ refresh      │
│  anchor:  [ all ] [ exact ] [ shifted ] [ fuzzy ] [ orphaned ]              │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │ ✎ reflection · 2026-07-29 · ● exact · block                           │ │
│  │                                                                       │ │
│  │ "just Goodhart with extra steps. we never test the case where the     │ │
│  │  metric IS the goal."                                                 │ │
│  │                                                                       │ │
│  │ ↳ anchored to  alignment-failures › Reward hacking                    │ │
│  │   "the model learns to satisfy the metric rather than the goal"       │ │
│  │                                                                       │ │
│  │ Linked: 2026-06-02-goodhart-in-evals                                  │ │
│  │                                                                       │ │
│  │ [ Open in Obsidian ]   [ Promote… ]   [ Re-anchor ]   [ Archive ]     │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │ ⚑ dispute · 2026-07-21 · ◌ orphaned · topic     promoted → gap g-0f21 │ │
│  │                                                                       │ │
│  │ "the citation-validity metric can't see a citation that supports the  │ │
│  │  wrong claim."                                                        │ │
│  │                                                                       │ │
│  │ ⚠ the passage this pointed at no longer exists on eval-design.        │ │
│  │                                                                       │ │
│  │ [ Open in Obsidian ]   [ Review drift → ]   [ Archive ]               │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                                                             │
│  [ Load more ]                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Empty state** (never a blank pane):

```
┌─ Notes ─ agentic-systems ───────────────────────────────────── 0 notes ─┐
│                                                                         │
│                                   ✎                                     │
│               No notes on this topic yet.                               │
│                                                                         │
│   Notes are your own marginalia — private, never scored, never part of  │
│   the wiki. Write one from a conversation ("note this…"), with          │
│   /knotica:note, or by hand in notes/agentic-systems/ in Obsidian.      │
│                                                                         │
│                        [ Open notes folder ]                            │
└─────────────────────────────────────────────────────────────────────────┘
```

### View 2 — Drift / orphan review queue

The hard view. The loop rewrote the page under the note; the user must decide what the note now
points at. Old text, new text, and three choices.

```
┌─ Notes › Drift ─ agentic-systems ──────────────────── 3 notes need review ─┐
│                                                                            │
│  ┌──────────────────────────────────────────────────────────── 1 of 3 ───┐ │
│  │ Your note (2026-07-21, dispute)                                       │ │
│  │   "the citation-validity metric can't see a citation that supports    │ │
│  │    the wrong claim."                                                  │ │
│  │                                                                       │ │
│  │ was anchored to  eval-design › Citation validity   pinned@a3f9c21     │ │
│  │                                                                       │ │
│  │ ── what you pinned ──────────────────────────────────────────────────│ │
│  │ ▍"citation validity is scored as the fraction of claims carrying a   │ │
│  │ ▍ resolvable wikilink"                                               │ │
│  │                                                                       │ │
│  │ ── what is there now (rewritten 2026-07-27, loop/r/4c1) ─────────────│ │
│  │ ▍"citation validity scores the fraction of substantive claims that   │ │
│  │ ▍ carry a resolvable wikilink to a non-stale page"                   │ │
│  │                                                                       │ │
│  │ ◐ shifted · the passage moved and was reworded; 71% of it survives.  │ │
│  │                                                                       │ │
│  │   [ Re-anchor here ]   [ Keep the old pin ]   [ Detach ]              │ │
│  │     pins to the new       marks it historical,    records that this   │ │
│  │     text, exact           stops asking            note is no longer   │ │
│  │                                                   about that passage  │ │
│  │                                                                       │ │
│  │  Nothing is erased — each choice appends to this note's anchor        │ │
│  │  history. The pin above stays readable.                               │ │
│  └───────────────────────────────────────────────────────────────────────┘│
│                                                                            │
│  [ ← prev ]                                                     [ next → ] │
└────────────────────────────────────────────────────────────────────────────┘
```

Orphaned variant — nothing survives, so "Re-anchor here" is replaced by a best-guess picker. Per
the architect's constraint, an orphan at or above `complete_orphan_threshold` **always** ships
both the historical text (the "what you pinned" block above, unchanged) and at least one best
guess. The user is never shown a dead end:

```
│ ── what is there now ────────────────────────────────────────────────────│
│ ⌫ the section you pinned no longer exists on this page.                  │
│                                                                          │
│   closest surviving passages (best guess first):                         │
│   ● eval-design › Metric gaming      — 44% overlap                       │
│   ○ alignment-failures › Reward hacking — 31% overlap                    │
│                                                                          │
│   [ Re-anchor to selected ]   [ Keep the old pin ]   [ Detach ]          │
│                                                                          │
│   Nothing is erased — each choice appends to this note's anchor history. │
```

**Accessibility.** Anchor status is carried by **glyph + word + tone**, never colour alone —
matching the `TIER_TREATMENT` / `ORIGIN_TREATMENT` precedent in `SourcesPane`:

```ts
/** Anchor status -> (shape glyph, tone class, label) — never color alone (WCAG 1.4.1). */
const ANCHOR_TREATMENT: Record<AnchorStatus, {glyph: string; tone: string; label: string}> = {
  exact:    { glyph: "●", tone: "ok",   label: "exact" },
  shifted:  { glyph: "◐", tone: "warn", label: "shifted" },
  fuzzy:    { glyph: "○", tone: "warn", label: "fuzzy" },
  orphaned: { glyph: "⌫", tone: "bad",  label: "orphaned" },
};

/** Intent -> (shape glyph, tone class) — shape + label, never color alone. */
const INTENT_TREATMENT: Record<NoteIntent, {glyph: string; tone: string}> = {
  reflection: { glyph: "✎", tone: "" },
  dispute:    { glyph: "⚑", tone: "warn" },
  gap:        { glyph: "◆", tone: "warn" },
  question:   { glyph: "?", tone: "" },
};
```

### View 3 — Promotion (a card action, not a view)

```
┌─ Promote note → ───────────────────────────────────────────────────────┐
│  "the citation-validity metric can't see a citation that supports the  │
│   wrong claim."                                                        │
│                                                                        │
│  (•) Training example   adds a curated (question, pages, answer) example │
│                         to this topic's training set.                    │
│  ( ) Knowledge gap      files it in the research queue (origin=reported). │
│      ⓘ available because this note's intent is "dispute"                 │
│                                                                        │
│  Question the wiki should answer                                       │
│  ┌────────────────────────────────────────────────────────────────────┐│
│  │ Can citation-validity scoring detect a resolvable citation that    ││
│  │ does not support the claim it is attached to?                      ││
│  └────────────────────────────────────────────────────────────────────┘│
│                                                                        │
│  Grounded answer (cited from the anchored pages)                       │
│  ┌────────────────────────────────────────────────────────────────────┐│
│  │ No — scoring resolves the wikilink but never checks that the       ││
│  │ linked page supports the claim. [[eval-design]]                    ││
│  └────────────────────────────────────────────────────────────────────┘│
│  Verdict:  ( ) good answer    (•) bad answer                           │
│                                                                        │
│  Grounded in: eval-design          ← the note's anchored wiki page,     │
│                                       never the note itself             │
│                                                                        │
│  This crosses out of your private notes. The note itself stays where   │
│  it is and records the promotion.                                      │
│                                        [ Cancel ]   [ Promote ]        │
└────────────────────────────────────────────────────────────────────────┘
```

**Three things this dialog encodes, all from the C3 adjudication:**

- **Training example is the default and the only dataset destination in v1.** The held-out
  (golden) set is deliberately absent — not greyed out, absent. `freeze()` enforces
  trainset/golden disjointness, so the choice is a one-way door that belongs behind
  `golden_review`, not behind a card button. (The *tool* keeps a `golden` enum value that
  rejects with an explanatory error — see §8 — because a model will reach for the word; a
  *human* at a dashboard does not need to see a door that is closed.)
- **"Grounded in" names the anchored wiki page, never the note.** An eval question must be
  answerable from the KB corpus; a note path selects zero pages. The label says so out loud so
  the user can catch a bad promotion before it lands.
- **"Knowledge gap" is only offered for `intent ∈ {dispute, gap, question}`** and carries the
  inline reason why it is available. On a plain reflection the option is not rendered at all —
  filing a gap is human-only and intent-gated.

The dialog is the visible boundary crossing D2 demands. It is a modal — the only modal in the
pane — because it is the one irreversible-ish, cross-layer decision here. Everything else is
inline.

### The five UI states

| State | Treatment |
|---|---|
| Default | Card list above. |
| Loading | Skeleton cards (list load is >1s over MCP); spinner only on a single card during `mode=apply`. |
| Empty | Illustrated empty state above, with the three ways to write a note. Never a blank pane. |
| Error | Inline banner carrying the envelope's `message` **and** `fix` verbatim — the `fix` string is the recovery action and must not be swallowed. |
| Partial | `skipped_malformed: N` renders as `"N notes could not be read — open notes/<topic>/ to check them"`. A hand-edited note is expected to be malformed sometimes; the pane must not hide the rest. |

### `toolClient` methods + types

```ts
// dashboard/src/types.ts
export type PaneId = "vault" | "ask" | "loop" | "arena" | "datasets"
                   | "golden" | "ingest" | "sources" | "notes";       // + "notes"

export type NoteIntent      = "reflection" | "dispute" | "gap" | "question";
export type NoteIntentFilter= NoteIntent | "all";
export type AnchorFidelity  = "span" | "block" | "section" | "page" | "topic";
export type AnchorStatus    = "exact" | "shifted" | "fuzzy" | "orphaned";
export type AnchorStatusFilter = AnchorStatus | "all";
export type NoteAction      = "reanchor" | "detach" | "promote" | "archive";
export type AnchorKind      = "pinned" | "reanchored" | "kept" | "detached";
/** v1 offers only these two in the UI; the tool's `golden` value always rejects. */
export type PromoteTarget   = "trainset" | "gap";

/** Append-only. A note's anchors are a history; the newest record is the effective one. */
export interface NoteAnchor {
  index: number;
  kind: AnchorKind;             // "detached" is terminal — the note is no longer about it
  page: string;                 // "" when fidelity === "topic"
  heading: string;
  fidelity: AnchorFidelity;
  status: AnchorStatus;
  quote: string;                // what the user pinned
  pinned_at: string;            // commit sha the pin was taken against
  superseded_by: number | null; // index of the record that replaced this one
}

export interface NoteDrift {
  anchor_index: number;
  /** ALWAYS populated, orphans included — the historical text is never withheld. */
  pinned_quote: string;
  live_quote: string;           // what is there now ("" when orphaned)
  overlap: number;              // 0..1
  rewritten_at: string;         // ISO 8601
  rewritten_by: string;         // e.g. "loop/r/4c1"
  /** At least one entry whenever overlap is at/above complete_orphan_threshold:
   *  a total orphan still ships a best guess, so the user never faces a dead end. */
  alternatives: Array<{ page: string; heading: string; overlap: number }>;
}

export interface NoteRecord {
  note_id: string;
  topic: string;
  path: string;
  intent: NoteIntent;
  body: string;                 // truncated in list, full in read
  truncated: boolean;
  created: string;
  updated: string;
  tags: string[];
  anchors: NoteAnchor[];
  linked_notes: string[];
  promoted: string;             // "none" | "gap:<id>" | "eval:<id>"
  archived: boolean;
}

export interface NotesReadResult {           // action=list
  topic: string;
  intent_filter: NoteIntentFilter;
  status_filter: AnchorStatusFilter;
  notes: NoteRecord[];
  intent_counts: Record<NoteIntent, number>;
  status_counts: Record<AnchorStatus, number>;
  next_cursor: string;
  has_more: boolean;
  total_count: number;
  skipped_malformed: number;
}

export interface NotesDriftResult {          // action=drift
  topic: string;
  items: Array<{ note: NoteRecord; drift: NoteDrift }>;
  next_cursor: string;
  has_more: boolean;
  total_count: number;
}

export interface NoteActionResult {          // reanchor | detach | promote | archive
  mode: "dry-run" | "apply";
  topic: string;
  note_id: string;
  action: NoteAction;
  committed?: boolean;
  commit?: string;
  anchor?: NoteAnchor;                       // reanchor: the resulting pin
  promoted_to?: string;                      // promote: "gap:<id>" | "eval:<id>"
  // decision-envelope (dry-run), matching suggestions_review
  decision_id?: string;
  summary?: string;
  context?: Record<string, unknown>;
  options?: Array<{ action: string; preview: string; reversible: boolean }>;
  provenance?: Record<string, unknown>;
  reason_required?: boolean;
}
```

```ts
// dashboard/src/toolClient.ts — 1:1 with the dispatcher, no client-side aggregation
notesList(topic: string, intent?: NoteIntentFilter, status?: AnchorStatusFilter,
          cursor?: string, limit?: number, vault?: string): Promise<NotesReadResult>;
notesRead(topic: string, noteId: string, vault?: string): Promise<NoteRecord>;
notesDrift(topic: string, cursor?: string, limit?: number,
           vault?: string): Promise<NotesDriftResult>;
notesReanchor(topic: string, noteId: string, anchor: number, mode: "dry-run" | "apply",
              page?: string, quote?: string, vault?: string): Promise<NoteActionResult>;
notesDetach(topic: string, noteId: string, anchor: number,
            mode: "dry-run" | "apply", vault?: string): Promise<NoteActionResult>;
notesPromote(topic: string, noteId: string, target: PromoteTarget,
             mode: "dry-run" | "apply",
             fields?: { question?: string; answer?: string;
                        verdict?: "good" | "bad"; reason?: string },
             vault?: string): Promise<NoteActionResult>;
notesArchive(topic: string, noteId: string, mode: "dry-run" | "apply",
             vault?: string): Promise<NoteActionResult>;
```

Each is one `call("notes", { action, ... })`, mirroring `arenaStatus` → `call("arena", {action:
"status", ...})`. No new transport, no new envelope handling — `extractToolPayload` already
unwraps and throws.

**Tab badge.** `WikiStatus` gains `notes?: { total: number; drifted: number }` so the Notes tab
can carry `Notes · 3` the way Sources carries its pending count. This is the same field the
SessionStart nudge reads (§9) — one server-side count, three consumers.

---

## 7. Obsidian-native ergonomics

A note must be fully usable by a human with **no tooling** — readable, hand-writable, and
clickable in Obsidian. Design constraints, in priority order:

1. Frontmatter stays inside the **strict YAML subset** the vault parser accepts (scalars, flow
   lists, block lists — **no nested maps**), so a note is parseable by the same machinery even
   though it is exempt from the page contract.
2. Anchors must be **real `[[wikilinks]]`** — that is what makes them clickable, and what makes
   Obsidian's own graph render the notes↔KB relationship for free.
3. Anything the machine needs beyond the link is inline, in fixed field order, so it is both
   readable and deterministically parseable.
4. **The file format is the contract.** A note written by `note_capture` and a note typed by hand
   in Obsidian must be indistinguishable to every read path — no `origin`, no `created_by`, no
   server-only field, and every field but `id`/`topic`/`intent`/`created` defaultable on read.
   If a read path can tell which surface wrote a note, the format has leaked.
5. **Anchors are append-only.** `reanchor` and `detach` add a record; nothing is edited or
   removed. The list is a history and the last record is the effective anchor.

### The file

`notes/agentic-systems/2026-07-29-reward-hacking-is-goodhart.md`

```markdown
---
id: 2026-07-29-reward-hacking-is-goodhart
topic: agentic-systems
intent: reflection
created: 2026-07-29T14:22:11Z
updated: 2026-07-29T14:22:11Z
anchor_status: exact
anchor_fidelity: block
promoted: none
archived: false
tags: [metrics, incentives]
---

# Reward hacking is just Goodhart with extra steps

just Goodhart with extra steps. we never test the case where the metric IS the goal.

## Anchors

- [[alignment-failures#Reward hacking]] — `block` · `exact` · pinned@`a3f9c21`
  > the model learns to satisfy the metric rather than the goal

## Related notes

- [[2026-06-02-goodhart-in-evals]]
```

> **Corrected 2026-07-30, during Phase 2 implementation.** The anchor-bullet examples in this
> section are **stale** and must not be built from. They show an on-disk `` `exact` ``/
> `` `superseded` `` *status* token, `~~strikethrough~~` rendering, a `reanchored@` token variant,
> and a standalone `**detached**` bullet — none of which the shipped grammar accepts, and three of
> which are incompatible with decisions this document's own architecture rests on.
>
> The status token persists a projection, which `dec-058` makes derived-and-never-persisted (Phase
> 1's parser never accepted it). Strikethrough and a `superseded` token require **rewriting an
> earlier bullet**, which AC-09 forbids in terms — the anchor of record is never modified. A
> `reanchored@` token breaks the signature rule that a bullet is an anchor iff it carries
> backticked fidelity *plus* `pinned@<sha>`, which is what lets an older reader survive a
> later-generation file. And the `**detached**` bullet carries neither, so the shipped parser would
> skip it into `skipped_anchor_count` — silently losing the very record it exists to write.
>
> The binding grammar is a single bullet shape with an optional trailing `kind` token, where an
> absent token means `pinned` so every existing note parses unmigrated, and supersession is
> **derived** from document order rather than stored. Frontmatter `anchor_status`/`anchor_fidelity`
> are likewise not implemented — they would cache a projection that goes stale the moment the KB
> changes. The *intent* of this section stands unchanged: the anchor list is an append-only
> history, readable in plain Obsidian, and nothing is ever deleted.

**The anchor list is a history, not a field.** After a drift review that re-pinned once and then
detached, the same section reads:

```markdown
## Anchors

- ~~[[eval-design#Citation validity]]~~ — `block` · `superseded` · pinned@`a3f9c21`
  > citation validity is scored as the fraction of claims carrying a resolvable wikilink
- ~~[[eval-design#Citation validity]]~~ — `block` · `superseded` · reanchored@`c81f770`
  > citation validity scores the fraction of substantive claims that carry a resolvable
  > wikilink to a non-stale page
- **detached** — `2026-07-28` · this note is no longer about that passage
```

Struck-through text renders natively in Obsidian, so the history reads as history at a glance
while staying fully parseable. Nothing was deleted: the two superseded pins and the passages they
carried are still here, and still in git.

**Why this shape:**

- **The body is the user's sentence, unedited.** The `# heading` is generated from it and is
  freely editable — Obsidian shows it as the note's title. Nothing else is inserted between the
  user and their own words.
- **Anchors live in the body, not frontmatter.** Multiple anchors (A6) *and* the append-only
  anchor history are a list, which is natural in markdown and impossible in the flat-YAML subset
  without parallel arrays. As body content they are clickable; as frontmatter they would be dead
  text. This is also what makes append-only cheap: appending a line to a markdown list is
  something a human does by hand without thinking.
- **The anchor line is one readable sentence and one parseable record.** Fixed order —
  wikilink, ` — `, backticked fidelity, ` · `, backticked status, ` · pinned@` sha — with the
  pinned passage as a blockquote underneath. A human reads it as a citation. A parser reads it
  by position.
- **`anchor_status` / `anchor_fidelity` in frontmatter are the *worst* across all anchors**, so
  Obsidian's own search and Dataview-style queries can find drifted notes without parsing the
  body. Denormalized on purpose; the body is authoritative.
- **`promoted:` is a flat scalar** (`none` | `gap:<id>` | `eval:<id>`) — the audit trail for D2's
  opt-in boundary crossing, visible to the human who owns the note.
- **`archived: false` not deletion.** The server never removes a note file.
- **Note→note links are bare wikilinks** in `## Related notes` — same folder, so they resolve in
  both Obsidian and (if it ever mattered) knotica's same-directory rule.

**Bare-basename anchors are deliberate and depend on an architecture requirement.**
`[[alignment-failures#Reward hacking]]` is a bare target. Obsidian resolves bare wikilinks
vault-wide by basename, so this is exactly right for the human surface. knotica's core link graph
resolves bare targets *same-directory-only* — which would fail — **but that only matters if the
core graph walks `notes/` at all, and it must not** (a note creating an inbound link to a KB page
would make an orphaned page look linked, contaminating lint). See `## Architecture Challenges`
§ C2: the exclusion is a prerequisite for this ergonomics choice, not an optimization.

**Hand-written notes are first-class.** A human can create a file in `notes/<topic>/` with only
`id`, `topic`, `intent`, `created` and a body; every other field is defaulted on read. A note
with no `## Anchors` section is a valid topic-fidelity note. This is the fourth capture surface
(D4) and it costs nothing extra by construction.

---

## 8. Error grammar

Matching `core/errors.py`: one enum, grammar *"X failed because Y. To fix: Z."*, `fix` is the
exact next action, `retryable` only where retrying the same call can work.

### The governing rule

> **Anchoring never produces an error.** Every anchor-quality problem is a *warning on a success
> envelope* plus a degraded `fidelity`. `note_capture` fails only for reasons unrelated to
> anchoring.

This is why the table below has fewer error codes than the brief asked for — three of the five
named failures are, by design, not failures.

### Additions to `ErrorCode` — exactly two

The deferred-`golden` and intent-gated-`gap` rejections deliberately **reuse `INVALID_ARGUMENT`**
rather than earning a `NOT_IMPLEMENTED`/`DEFERRED` code. `INVALID_ARGUMENT`'s contract fix text
is *"Correct the named argument and call again"* — which is exactly the recovery in both cases
(`target=trainset`). A new code would add surface for no new branch.

**Why `golden` stays in the enum at all** (it always errors — normally a "hard to misuse"
violation): a model reasoning *"promote this as an eval question"* will reach for the word
`golden` whether or not the enum offers it. Without the value it gets a generic
`must be one of trainset|gap` that teaches nothing; with it, it gets the actual reason
(disjointness is a one-way door) and the actual alternative. The enum is documenting the domain,
and the description says up front that the value rejects. The dashboard, whose consumer is a
human who does not guess at vocabulary, does **not** render the option (§6).

| Code | Kind | Why it earns a slot |
|---|---|---|
| `ANCHOR_DEGRADED` | **warning-only** (joins `SECRET_SCRUBBED` in `WARNING_CODES`) | The write succeeded and the model must still learn the pin is weak so it can offer a refinement. Reusing an error code would force a failure envelope for a successful write. One code covers *every* degradation reason; the `message` carries the specific one. |
| `NOTE_NOT_FOUND` | error | Distinct id namespace with a distinct recovery action, exactly mirroring `SUGGESTION_NOT_FOUND`. Reusing `PAGE_NOT_FOUND` would send the model to `search`, which cannot see notes. |

```python
ANCHOR_DEGRADED = "ANCHOR_DEGRADED"   # WARNING_CODES; never retryable
NOTE_NOT_FOUND  = "NOTE_NOT_FOUND"

DEFAULT_FIX[ErrorCode.ANCHOR_DEGRADED] = (
    "The note is saved. Offer the user a precise anchor: call "
    "`notes action=reanchor` with the page and passage they pick."
)
DEFAULT_FIX[ErrorCode.NOTE_NOT_FOUND] = (
    "Call `notes action=list` to see current note_ids."
)
```

### The full grammar

| Situation | Envelope | Code | `message` | `fix` |
|---|---|---|---|---|
| Quote not found in any claimed page | **success + warning** | `ANCHOR_DEGRADED` | `the quote was not found in agentic-systems/eval-design.md, so the note is pinned at page level rather than to a passage` | `The note is saved. If it belongs to a different page, call \`notes action=reanchor\` with the right page.` |
| Quote matches several pages | **success + warning** | `ANCHOR_DEGRADED` | `the quote matched 2 pages, so the note is pinned at topic level rather than to a passage` | `Offer the user the alternatives; call \`notes action=reanchor\` with the chosen page.` |
| Claimed page does not exist | **success + warning** | `ANCHOR_DEGRADED` | `page 'eval-desing' is not in topic 'agentic-systems', so the note is pinned at topic level` | `Call \`list_topics\` or \`search\` to find the right page, then \`notes action=reanchor\`.` |
| No quote supplied | **success**, `fidelity: page\|topic` | — | — | — |
| Note's anchor no longer resolves (on read) | **success**, `status: orphaned` | — | — | — |
| Re-anchor target page deleted | error | `PAGE_NOT_FOUND` | `page 'agentic-systems/eval-design.md' no longer exists in the vault` | `Call \`search\` in this topic for the surviving page, or \`notes action=detach\` to keep the note without an anchor.` |
| Topic not in vault | error | `TOPIC_NOT_FOUND` | (existing `TopicNotFoundError`) | (contract default) |
| Unknown `note_id` | error | `NOTE_NOT_FOUND` | `no note 'abc' in topic 'agentic-systems'` | (contract default) |
| Empty `note` | error | `INVALID_ARGUMENT` | `note must be the user's own words, got an empty string` | `Pass the user's reflection verbatim as \`note\`.` |
| Bad `intent` / `action` / `mode` / `limit` | error | `INVALID_ARGUMENT` | (mirrors the `_validate_*` helpers) | (per-argument) |
| Promote a note with no question to ask | error | `INVALID_ARGUMENT` | `this note records a reflection, not a question the wiki should answer` | `Ask the user for the question the wiki should answer, then call \`notes action=promote\` again with it.` |
| `promote target=golden` | error | `INVALID_ARGUMENT` | `promoting to the held-out (golden) set is deferred: trainset and golden must stay disjoint, so the choice is one-way and needs its own review gate` | `Promote to the training set instead: \`notes action=promote target=trainset\`. Golden promotion runs through \`golden_review\`, not this action.` |
| `promote target=gap` on a `reflection` note | error | `INVALID_ARGUMENT` | `filing a gap needs a note whose intent is dispute, gap, or question; this one is a reflection` | `Ask the user whether the wiki is actually wrong. If it is, they can change the note's intent in Obsidian, or file it directly with \`gap_report\`.` |
| Promote grounded in a note path rather than a wiki page | (prevented by construction) | — | `pages_used` is always derived server-side from the note's anchored wiki pages; the caller cannot supply a note path | — |
| Lock contention / git failure | error | `LOCK_BUSY` (retryable) / `GIT_ERROR` | (existing) | (contract defaults) |

**Note the deleted-page asymmetry.** On *capture*, a bad page reference degrades. On *re-anchor*,
it errors — because the user is deliberately pointing at that page and a silent degradation would
discard their instruction. Same condition, opposite treatment, because the intent differs. That
asymmetry is the design, not an inconsistency.

---

## 9. CLI parity

**Decision: no new CLI subcommand in v1.** Extend the existing status surface instead.

Walking the candidate CLI verbs against what a terminal is actually good for:

| Verb | Verdict |
|---|---|
| capture | **No.** The CLI has no conversation, so it cannot supply `quote` or `pages` — every CLI-captured note would be topic-fidelity. The fourth capture surface (hand-writing in Obsidian) already covers "write a note without an agent," and does it better. |
| reanchor / promote / detach | **No.** These are review decisions that need the old text, the new text, and the overlap side by side. That is a screen, not a terminal line. |
| list / drift | **Useful, but not yet.** Real, but hypothetical until someone scripts against it — and a subcommand is easier to add later than to remove. |

**What ships instead — one additive field, three consumers:**

`wiki_status` gains `notes: { total, drifted }`. That single count feeds:

1. `knotica status --nudge` → the SessionStart hook's concern (f), which already exists to say
   "what needs my attention" and prints nothing when there is nothing to say:

   ```
   knotica · agentic-systems: 2 pending suggestions · 3 notes drifted
   ```

   Sub-second, no new subprocess, no new hook concern — it rides the combined read that is
   already happening.
2. The dashboard Notes tab badge.
3. Any future `knotica notes` subcommand, which will read the same field.

Cost: one field on an existing payload and one clause in an existing nudge string, versus a new
module plus a `COMMAND_NAMES` edit. Simplicity First, and the growth path stays open.

If `knotica notes` is later warranted, the shape is fixed now so it cannot drift:
`knotica notes list --topic <t> [--intent …] [--status …] [--json]` and
`knotica notes drift --topic <t> [--json]` — read-only (`core` read functions only, no
`core.lock` import, layering intact), `--json` always, colour off when not a TTY / `NO_COLOR`,
exit `0` clean / `1` error / `2` misuse / `3` nothing to report.

---

## Surface cost summary

| Surface | Files touched | Size |
|---|---|---|
| `note_capture` (flat tool) | new `mcp_server/tools_notes.py` + `server.py` wiring | ~120 lines |
| `notes` dispatcher | new `mcp_server/tools_dispatch_notes.py` + `server.py` wiring | ~180 lines |
| Error codes | `core/errors.py` (+2 enum members, +2 `DEFAULT_FIX`, +1 `WARNING_CODES`) | ~12 lines |
| `wiki-maintenance` | 3 text edits, one existing file | ~30 lines |
| `commands/note.md` | 1 new file, no registration | ~20 lines |
| NotesPane | `types.ts`, `toolClient.ts`, new `NotesPane.tsx`, `App.tsx` (4 sites), rebuild | ~520 lines |
| `wiki_status` + nudge | 1 field, 1 clause | ~10 lines |
| **Protocol** | **none** — deliberately | **0** |
| **CLI subcommand** | **none** — deliberately | **0** |

---

## Architecture Challenges

Four items. C1 and C3 are substantive and I expect them to change decisions upstream; C2 is a
hard prerequisite for a design choice I have already committed to; C4 is a convention departure
that the architect should ratify or reject.

### C1 — Anchors must never be written into KB pages

**Contested decision (anticipated):** the anchor model plants stable `^block-id` markers into
wiki pages so a note has something durable to point at. The vault has no per-block identifier
today, and this is the obvious way to create one.

**Proposed alternative:** anchors are recorded **entirely on the note side** — quote text plus a
`(page_path, commit_sha)` pin, projected forward at read time. `git`'s `read_file_at` /
`diff_between` already make that walkable without touching any page.

**Quality rationale:** if capture writes to a KB page, four locked properties break at once —
(a) a note changes scored content, violating the no-score-contamination guard; (b) a note write
becomes two commits, breaking one-commit-per-mutating-operation; (c) a page write on the default
branch wakes the loop's change detection, violating A5 and the no-loop-churn guard; (d) the
capture call's latency and failure surface both grow at the exact moment friction is fatal. My
entire capture design rests on `note_capture` touching **only** `notes/`; the tool description
states that as a promise to the model.

**Blast radius:** if the architect chooses page-side markers, `note_capture` must be redesigned
(two-phase, page-write guard, loop suppression) and the `dec-045` tool-count argument reopens.
This is the single upstream decision that can invalidate §2 wholesale.

**Recommendation:** **adopt** — anchors note-side only.

**Outcome (rev 2): ADOPTED, and independently corroborated.** The architect chose a bi-partite
anchor (immutable record `(page, commit, quote[, start])` plus a derived read-time projection)
with no `^block-id` injection in v1, having reached it without seeing this challenge. Block IDs
are gated behind a Phase-3 two-part spike (preservation **and** eval-delta) rather than rejected
permanently — which does not affect this design, because provisioning would run on the normal
page-write path, never at capture time. All four objections were about capture-time writes and
still hold.

### C2 — `notes/` must be excluded from the link graph, because notes use real wikilinks

**Contested decision (anticipated):** notes are excluded from *scoring* surfaces (retrieval
corpus, lint content-pages, loop watch) but `iter_page_paths` — the one primitive both
`links.py` and `lint.py` build on — keeps walking `notes/`, because it walks every non-dot
directory in the vault and has no folder-family concept.

**Proposed alternative:** the folder-family exclusion the health guard already calls for must
cover `iter_page_paths` specifically, not just the scoring surfaces.

**Quality rationale:** I have committed to anchors rendering as real `[[wikilinks]]` (§7) —
non-negotiable, because that is what makes a note clickable in Obsidian and what lets Obsidian's
native graph render the notes↔KB relationship for free (which is why I could cut the dashboard
graph view entirely, §6). Two consequences follow if `notes/` stays in the graph: (a) a KB page
whose only inbound link is from a private note stops looking orphaned, so `PAGE_ORPHANED` lint
silently degrades — contamination through the back door; (b) knotica's same-directory bare-target
resolution would mark every note anchor unresolved, generating noise about links that are correct
in the surface that matters.

**Blast radius:** small if handled in the folder-family generalization the guard already
mandates; large if discovered after implementation, because the fallback is switching notes to a
non-wikilink syntax — which costs Obsidian clickability, costs the native graph, and puts the
dashboard graph view back on the bill (~+200 lines).

**Recommendation:** **adopt** — fold `iter_page_paths` into the folder-family exclusion.

**Outcome (rev 2): ADOPTED.** A `core/vault_layout.py` folder-family module lands in Phase 0 and
the notes graph stays single-graph with the one consequential consumer scoped. The bare-basename
wikilink anchors of §7 are safe, and cutting the dashboard graph view (§6) stands.

### C3 — Promotion must not route a note path through `bootstrap(pages=[...])`

**Contested decision (anticipated):** the eval bridge reuses `evals.golden.bootstrap(...,
pages=[note_path])` — the cheapest path the eval-gap research identified, and correctly so *for
pages*.

**Proposed alternative:** `notes action=promote target=golden` deterministically emits **one
`QARecord` candidate** into the existing golden staging file — question text from the note, the
note's *anchored KB pages* as `pages_used`, `source` marking the note origin — and the existing
`golden_review` load/save/freeze flow provides the human gate D3 requires. No synthesis call, no
`pages=` restriction.

**Quality rationale:** two problems with the `pages=` route. First, mechanical: if notes are
excluded by omission (the health guard's whole point, and what C2 completes), a notes path
handed to `pages=` enumerates to nothing — the promotion silently produces zero candidates,
which is the worst possible failure mode for a user-initiated act. Second, semantic: `pages=`
asks an LLM to *synthesize questions from a document*. A promoted note already **is** the
question — the user wrote it. Synthesizing over it replaces the human's actual question with a
generated paraphrase, which discards exactly the signal the notes layer exists to harvest ("a
legitimate source of **real human questions**"). `curate_example`'s deterministic append is the
right precedent: it takes `(query, pages_used, answer, verdict)` directly, with no synthesis.

**Blast radius:** contained inside the promote action's implementation, but if it lands wrong the
symptom is a silent no-op, not an error — expensive to notice.

**Recommendation:** **adopt** — deterministic candidate emission, existing review gate, no
synthesis.

**Outcome (rev 2): ADOPTED on the mechanism, OVERRULED on the destination.** The architect
verified the failure independently and more precisely than I did — `bootstrap(pages=[...])`
intersects with `entity_pages(...)` first (`golden.py:559-561`, `train_bootstrap.py:93`), so a
note path selects zero pages: verified non-functional, not merely suspected. Deterministic
emission stands.

The destination changed: **trainset via `curate_example`, not golden staging.** I proposed golden
because the golden-review gate matched D3's "human review gate" wording most literally. Two
reasons outrank that symmetry, and both are better than my argument:

1. `freeze()` runs `verify_disjoint_from_trainset` and raises `GoldenSetContaminationError` —
   trainset-vs-golden is a genuine one-way door, so it must be chosen deliberately rather than
   defaulted by a card button.
2. Notes are a **biased sparse sample** — users annotate what surprised or annoyed them. That is
   an excellent regression probe and a dangerous headline benchmark. Defaulting the biased sample
   into the held-out set would have quietly skewed the number the whole loop optimizes against.

I did not weigh (2) and should have; it is the stronger objection to my own proposal. Golden
promotion is deferred to Phase 4 behind `golden_review`. §1, §4, §6, §8 revised accordingly.

### C4 — The read/offer guard is re-specified (not weakened) for capture

**Contested decision:** every mutating tool carries the verbatim clause *"never call this from
detection alone -- only after the user has explicitly confirmed the write."* Applied literally to
`note_capture`, it mandates a confirmation turn on every note.

**Proposed alternative:** on `note_capture` only, the clause is re-specified on the axis of
*what makes the intent explicit* — the user's message **is** the note (addressed remark or
explicit reflective aside) → capture; an unaddressed reaction → offer. Every other mutating
surface, including all four mutating `notes` actions, keeps the clause verbatim.

**Quality rationale:** the guard's purpose is that a mutation never happens on the model's
inference alone. Capture satisfies that purpose without a second turn, because the user's own
sentence supplies the intent. A confirmation turn here doubles the cost of the act (a note is one
sentence; the confirmation is another) on the one surface where friction determines whether the
feature is used at all — and buys a safety property the write already has for free: it is
private, non-destructive, content-idempotent, and reversible by editing a file in Obsidian.

**Blast radius:** narrative only — one tool description departs from a codebase-wide verbatim
convention. But it *is* a convention departure in a codebase that treats these strings as the
executable interface, so it should be ratified deliberately rather than noticed in review.

**Outcome (rev 2): RATIFIED.** The architect's constraint #1 independently states that capture is
one-shot and cannot fail on an unverifiable quote, degrading to `provenance: page` with a
reported degradation instead. The re-specified guard is the sanctioned reading, and the
one-shot/degrade-never-fail invariant of §2 is now stated on both sides of the boundary.

**Recommendation:** **adopt-with-modification** — accept the re-specification, and consider
adding the distinction ("explicit intent, not necessarily a separate turn") to the guard's own
definition so the next low-friction surface does not have to relitigate it.

---

## ADR fragments

| Fragment | id | Decision |
|---|---|---|
| `.ai-state/decisions/drafts/20260729-1219-fperez-main-notes-tool-decomposition.md` | `dec-057` | One flat `note_capture` + one `notes` dispatcher |
| `.ai-state/decisions/drafts/20260729-1219-fperez-main-notes-no-fifth-protocol-operation.md` | `dec-056` | No fifth `read_protocol` operation |

Both need a `LEARNINGS.md ### Decisions Made` entry when the pipeline reaches that stage.

## Assumptions I am adding

| # | Assumption | Load-bearing? | Reversible? |
|---|---|---|---|
| IA1 | The server can compute an anchor's live projection status cheaply enough to serve `notes action=drift` for a whole topic in one bounded, paginated call. If projection is expensive, the drift queue needs a durable marker instead. | Yes | Yes |
| IA2 | `wiki_status` is the right home for the notes counts (it is where every other "needs attention" count lives). | No | Yes |
| IA3 | A note's `note_id` equals its filename stem, so a hand-created file is addressable with no registry. | Medium | Yes |
| IA4 | Notes are topic-scoped, one topic per note. Cross-topic notes would break the `topic`-always-explicit convention every tool assumes; the multi-anchor mechanism (A6) covers the real need within a topic. | Yes | No — it is the topic-explicit invariant |
