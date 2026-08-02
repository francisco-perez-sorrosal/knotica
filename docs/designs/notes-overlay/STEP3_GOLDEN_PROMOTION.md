# Phase 4 Step 3 — the required inputs for the golden-promotion decision, measured

**Date:** 2026-08-02. **Cost:** zero — no eval run, no LLM call, no vault mutated.
**Method:** read-only `git clone --no-hardlinks` of both configured vaults; clones deleted after
counting. No live vault was read, written, or locked.

The Phase 4 brief named three inputs as **required before deciding** whether to revisit `dec-059`'s
golden-promotion deferral, and flagged the third as the one nobody has measured:

> *how many note-derived questions exist, what fraction are `dispute`/`gap`/`question` intent, and
> how many have already been routed to the trainset and are therefore permanently ineligible. That
> last number is the cost of continued deferral, and nobody has measured it.*

All three are now measured. The result makes the architect pass premature rather than urgent.

---

## Result

| input | measured |
|---|---|
| note-derived questions that exist | **0** |
| fraction `dispute`/`gap`/`question` intent | **undefined (n=0)** |
| note-derived questions already routed to the trainset (permanently ineligible) | **0** |

**The cost of continued deferral is exactly zero.** The one-way door has not been walked through
once.

### Both configured vaults, not one

`~/.config/knotica/config.toml` declares two vaults, and the **active** one is not the one the
notes work has assumed:

| vault | path | notes |
|---|---|---|
| `main` | `~/dev/data/knotica` | none, ever |
| `decision-making` (**active**) | `~/dev/data/decision-making` | none, ever |

`notes/` does not exist at either vault root, and `git log --all -- notes` returns **zero commits**
in both histories. The notes overlay has shipped through four phases and has never been used on a
real vault. A count taken from `main` alone would have been an incomplete answer to the same
question.

### Dataset state (vault `main`, topic `agentic-systems`)

| dataset | records | provenance |
|---|---|---|
| `datasets/qa.jsonl` (trainset) | 36 | `seed_train` 30, `curate_example` 6 |
| `datasets/golden.jsonl` | 25 | `curate_example` 25 |
| `datasets/golden.staging.reviewed.jsonl` | 25 | — |
| `gaps/gaps.jsonl` | 1 | `origin: reported`, **0** with a `note:` pointer |

The 6 `curate_example` trainset records are the *upper bound* on note-derived questions. Since no
note has ever existed, the actual count is 0 — the bound is not binding here, but the reason it is
only a bound matters, and is the second finding.

---

## The second finding — `dec-059`'s reversal trigger is unobservable

`dec-059` defers golden promotion and names the condition for revisiting it:

> *"Revisit when the trainset holds ≥10 note-derived questions and a compile/eval cycle has run
> over them."*

**That condition can never be evaluated, by construction.** Tracing the write path:

- `promote_note(target="trainset")` delegates to `curate_example(...)` and passes **no source
  override**.
- `curate_example` always stamps `source="curate_example"`.
- `QA_SOURCES` is exactly `{curate_example, distillation, seed_train}` — there is no note-derived
  value to stamp.
- The commit and `log.md` title are derived from the **query**, not from the note.
- `NoteActionResult.promoted_to` is a response-payload field only; `promoted_to` persists nowhere
  in `src/`.
- The `promoted:` frontmatter scalar that would carry the audit trail on the note itself is
  specified but **unimplemented** (`td-024`).

So a note-promoted trainset question is byte-indistinguishable from a hand-curated one. Counting
"note-derived questions in the trainset" is not merely unmeasured — it is unmeasurable with the
data the system records.

`td-024` already documented this asymmetry (gap promotions carry `reported_reason = note:<path>#0`;
trainset promotions carry nothing), but framed it as an audit-trail gap for an individual note. The
consequence for `dec-059` is larger and was not drawn: **the deferral is conditioned on a trigger
that can never fire.** Left alone, "deferred pending evidence" becomes permanent by accident, and
the failure is silent — nobody is ever prompted to notice.

---

## What this means for Step 3

**Do not spend a `systems-architect` pass on the golden-promotion design question yet.** The brief
prescribed one at opus, correctly, *if it is revisited*. The charter question — *what measurement
would make this unnecessary?* — is answered by the table above:

- `dec-059`'s two grounds are **untested, not wrong**. Its steelmanned runner-up argues the design
  routes the system's only real human questions away from the set that decides whether the KB is
  improving. That argument is strong, and it is still entirely theoretical: there are no real human
  questions yet.
- The decision is a **one-way door with nothing behind it**. `freeze()`'s
  `verify_disjoint_from_trainset` makes each question exclusively trainset-or-golden, so routing is
  irreversible per question — but zero questions have been routed. Deciding now would commit the
  design before a single instance of the phenomenon it reasons about exists.
- Deferring costs nothing **today**, and its cost is currently unobservable **tomorrow**. Those are
  different problems, and only the second one needs solving now.

### The actionable item is the trigger, not the door

Make note-derived trainset promotions countable, so `dec-059`'s trigger can fire. Two candidate
mechanisms, both small, neither decided here:

1. **A `source` value** — add a note-derived member to `QA_SOURCES` and have `promote_note` pass it
   through `curate_example`. Counting becomes a `grep`. Must be checked against the contamination
   rulings: a source *label* carries no note body and no note path, so it appears to sit on the
   legal side of the three shipped tests that assert the note body and path reach no scored
   surface — but that check is the decision, and it is not made here.
2. **The `promoted:` frontmatter scalar** (`td-024`, option 1: an optional extra-writes parameter on
   `curate_example`). Counting becomes a scan of the notes tree. Keeps `qa.jsonl` untouched, which
   is the more conservative reading of the contamination boundary, at the cost of a wider change.

Recorded as `td-028`. Revisit `dec-059` when its trigger is observable **and** has fired — not
before.

---

## Limitations, stated plainly

- **This measures adoption, not suitability.** Zero notes means the golden-promotion question has
  no evidence either way; it does not mean the deferral is *right*, only that it is not yet costly.
  If notes see real use, this measurement expires immediately and the architect pass becomes due.
- **Two vaults, one project, one user.** The finding "notes have never been used" is about this
  installation. It is strong evidence about whether the door has been walked through, and no
  evidence at all about whether note-derived questions would make good eval questions.
- **The unobservability finding is from code reading, not from a failed count.** The write path was
  traced end to end (`promote_note` → `curate_example` → `QARecord.source`, plus commit/log title
  derivation and a repo-wide search for a persisted `promoted_to`). It is a structural claim about
  what the code records, and it would be falsified by any persisted note→trainset linkage this
  trace missed.
