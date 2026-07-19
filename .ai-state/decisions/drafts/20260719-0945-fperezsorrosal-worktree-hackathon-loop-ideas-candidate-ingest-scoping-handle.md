---
id: dec-draft-9a95faae
title: Candidate-ingest scoping via an opaque handle threaded through the write tools (open/submit session pair)
status: proposed
category: architectural
date: 2026-07-19
summary: A client-driven source ingest lands on a loop/c/* candidate context via TWO new thin tools (source_ingest_open → returns an opaque `candidate` handle + resume state; source_ingest_submit → seals + hands to the gate) plus an additive optional `candidate` argument on store_source/write_page; the handle is round-tripped by the model exactly as dec-002's next_cursor is, keeping the surface stateless, thin (dec-003), and mechanism-agnostic about how writes are routed off the default branch.
tags: [mcp, tool-design, agent-interface, gapfill, phase-p4, source-gate, ingest, stateless-server, client-as-brain, dec-002, dec-003]
made_by: agent
agent_type: interface-designer
branch: worktree-hackathon-loop-ideas
pipeline_tier: standard
affected_files:
  - src/knotica/mcp_server/tools_write.py
  - src/knotica/mcp_server/tools_suggestions.py
  - vault-template/.knotica/prompts/ingest.md
affected_reqs: []
dissent: A single fat suggestion_ingest transaction would close the loop in one call and need no per-call handle; it is rejected only because a long paper cannot fit one turn and the client-as-brain writes prose across turns — but for short single-page sources the fat tool would be simpler and the handle machinery buys nothing.
---

## Context

An `approved` `SuggestionRecord` must be ingested by the interactive client (client-as-brain,
dec-014) onto a `loop/c/*` candidate branch, so the loop's existing clone→eval→gate path
(`core/loop.py:772 _process_candidate`) can measure the delta and merge or refuse it. The ingest is
the standard multi-step protocol — `store_source` → entity pages → wikilinks → index — but it must
land on a candidate context **without disturbing the live default working tree** the watcher observes
and other sessions read.

Three fixed constraints shape the interface: the server is **stateless** between calls (dec-004 — no
session memory; the branch identity cannot be remembered server-side); tools are **thin and
deterministic** (dec-003 — no server-side cognition, the client writes the prose across possibly many
turns); and the **mechanism** for routing a write off the default branch (checkout-under-flock,
server-managed worktree keyed by suggestion, or clone) is owned by the systems-architect (U1). The
interface must therefore be mechanism-agnostic: it says *which candidate a write belongs to*, not
*how* the server realizes that branch.

## Decision

Add **two thin deterministic tools** and one **additive optional argument** on the existing write
tools. No fat guided tool; no server session state.

1. **`source_ingest_open(topic, suggestion_id, vault="")`** — idempotent, single-phase. Validates the
   suggestion exists and is `approved` (else typed error). Creates — idempotently — the candidate
   context for `loop/c/<topic>/source-<suggestion_id[:8]>` off the current default tip, and returns:
   an **opaque `candidate` handle** (a round-trip token, today the branch name, documented opaque),
   a **resume block** (`source_present`, `pages_present: [...]`, `created` vs `resumed`), the
   **provenance block** copied from the suggestion (`query_text`, `qa_id`, `gap_id`, `suggestion_id`),
   and a one-line pointer to the source-ingest protocol section. Re-opening a half-done ingest returns
   the branch's current state so the client resumes rather than restarts (dec-001 idempotency-by-state).

2. **Additive `candidate: str = ""` argument on `store_source` and `write_page`.** Empty (the default)
   is today's default-branch behavior, **byte-for-byte untouched** — every existing ingest and every
   test still exercises the empty path. Non-empty carries the handle from `source_ingest_open`, scoping
   that one write to the candidate context. Content-hash idempotency is unchanged, evaluated against the
   candidate tip. `create_topic` is **not** touched — a source ingest never creates a topic (the
   approved suggestion's topic already exists on default).

3. **`source_ingest_submit(topic, suggestion_id, mode="dry-run", vault="")`** — two-phase, matching the
   house dry-run|apply muscle (dec-028, `branch_promote`). `dry-run` validates the candidate is
   lint-clean, has the source and ≥1 page, and reports what the gate will see plus gate-eligibility
   (baseline present?). `apply` seals the ingest and hands the candidate tip to the gate; it returns the
   gate verdict envelope (see the companion outcome-surfacing decision). Idempotent by branch-processed
   state: re-submitting an already-gated tip returns the prior verdict, not a re-eval.

**The `candidate` handle is to ingest-writes what `next_cursor` is to paginated reads (dec-002):** an
opaque token the model round-trips, self-describing, mechanism-hiding, stateless-compatible. This is the
established muscle memory, not a new pattern. The handle names the candidate; the architect's mechanism
realizes it.

The vault-template ingest protocol (`ingest.md`, single source per dec-010) gains one additive section —
"Ingesting an approved suggestion" — reusing 95% of the existing store→pages→wikilink→index guidance and
adding: open, thread the handle, weave `provenance` frontmatter, submit, report the verdict.

## Considered Options

### A. Two session tools + additive `candidate` arg on the write tools (CHOSEN)
- Pros: stateless-clean (handle round-tripped, no server memory); thin tools preserved (client still
  writes prose across turns); mechanism-agnostic (open returns a handle whatever the architect's routing
  is); resumable (open reports what's present); reuses the whole ingest protocol and the dec-002
  round-trip idiom; the empty-`candidate` path leaves every existing write untouched.
- Cons: two more tools (surface moves toward the dec-003 ~20-tool progressive-disclosure line); the
  write tools gain a gap-fill-adjacent optional arg; the client must carry the handle across calls.

### B. Single fat `suggestion_ingest(suggestion_id, source, pages[...])` transaction
- Pros: one call; no handle to thread; closes the loop atomically.
- Cons: a long paper cannot fit one turn; the client-as-brain distils prose page-by-page across turns
  (the ingest protocol is explicitly resumable); a fat tool that accepts the prose fuses cognition into
  the call — the exact dec-003 Option-B / dec-010 Option-E rejection. Rejected.

### C. Thread the full branch name (`candidate_branch="loop/c/…"`) instead of an opaque handle
- Pros: domain-neutral; no open tool needed if the client constructs the name.
- Cons: presupposes a *branch* mechanism (leaks the architect's U1 choice into the model-facing
  surface — not mechanism-agnostic); a long literal branch name is error-prone for the model to echo;
  no resume-state handshake. Rejected in favor of an opaque handle from a real open handshake.

### D. Per-call `suggestion_id` on the write tools (server re-derives the branch every call)
- Pros: no separate handle; the model already holds the suggestion_id.
- Cons: leaks the gap-fill concept into the generic write tools more deeply than an opaque handle, and
  gives no resume handshake or eligibility check before the client starts writing. Folded into A: A's
  handle *is* derived from the suggestion but obtained through an explicit open that returns resume
  state.

## Consequences

**Positive:** the interactive client discovers approved work (`suggestions_read(status="approved")` +
`wiki_status.suggestions`), opens once, threads an opaque handle, and reuses the entire deterministic
ingest surface; statelessness, one-commit-per-op, and client-as-brain are all preserved; existing
default-branch ingests are untouched (empty handle); the surface grows by exactly two tools and one
optional arg.

**Negative / costs:** +2 tools nudges the surface toward the progressive-disclosure re-evaluation line
(dec-003 reversal trigger); the write tools carry a gap-fill-adjacent optional arg; the architect's
routing mechanism **must** support per-call scoping keyed by the handle (see Disconfirmation / the
INTERFACE_DESIGN Architecture Challenge on per-call vs session-long flock).

## Disconfirmation

- **Falsifier:** if models routinely fail to carry the `candidate` handle across turns (dropping it and
  writing to default), or if source ingests are in practice always single-page (making the fat Option-B
  simpler at no cost), the two-tool handle machinery earned nothing.
- **Steelmanned runner-up:** Option B (fat transaction) is strongest when a source is short enough to
  ingest in one turn — then one call closes the loop with no handle, no resume protocol, no extra tools.
- **Reversal trigger:** if a session-long flock mechanism is chosen for U1 (making per-call handles
  moot because the whole ingest is one locked context), or if the surface crosses ~20 tools, revisit the
  decomposition and progressive disclosure together.
