---
description: "[Fill] Continue writing into an open candidate session (the ingest handoff stage's dispatch target)."
argument-hint: "<suggestion-id> [topic]"
allowed-tools:
  - AskUserQuestion
  - mcp__plugin_knotica_knotica__*
---
Resume or continue an approved gap-fill suggestion's candidate session. Do not paraphrase or
skip steps — this is the client-as-brain choreography the dashboard cannot execute on its own.

1. **Infer, do not ask.** Take `suggestion_id` from `$1`; if it is empty, ask once for it and
   nothing else. Take `topic` from `$2`, or infer it from the conversation — the gap-fill
   suggestion just discussed. Only if neither is inferable, call `wiki_status(view="scope")` and
   ask once, offering the vault's topics.
2. Call `fill action=session_status` with `topic`/`suggestion_id` to read the session's `state`.
   Report the state plainly, then branch on **the state**, not on `next.actor` — the actor answers
   "whose turn is it", and a user who dispatched this command is the `you` actor taking their turn.
   - `not_started` or `swept` — open (or reopen) the candidate session: continue at step 3.
   - `refused` — reopen the quarantined session and rework it: continue at steps 3-4.
   - `waiting_on_client` or `rework_in_flight` — keep writing: continue at step 4.
   - `client_wrote` — the candidate is already written: go straight to the preflight at step 5.
   - `blocked` — **stop.** Report `gate_eligible_reason` and name the fix: freeze a baseline in
     `improve` · instrument.
   - `submitted` — **stop.** The gate is evaluating the candidate; report that and wait.
   - `merged` — **stop.** Terminal; report the verdict.
3. If the session is not already open — state `not_started`, `swept`, or `refused` — call
   `fill action=source_ingest_open` with `topic`/
   `suggestion_id` to open (or resume) the candidate session. It returns the `candidate` handle,
   the provenance to weave into the pages, and a resume block listing what is already written —
   continue from there, never restart.
4. Follow the ingest protocol (`read_protocol(operation="ingest")` if you need a refresher) to
   write the source and its pages, passing `candidate=<handle>` on every `fill action=store_source`
   and `fill action=write_page` call. Never write without `candidate=` here — that targets the live
   vault instead of the session.
5. When the candidate looks complete, call `fill action=source_ingest_submit mode="dry-run"` to
   preflight (lint-clean, source present, at least one page, gate baseline present). Report what
   it finds.
6. **Mutations stay user-gated.** Offer the dry-run result to the user; only after they confirm,
   call `fill action=source_ingest_submit mode="apply"` to finalize and run the gate. Report the
   verdict verbatim — `merged`, `refused` (with the top regressed questions), or `blocked` (no
   gate baseline yet).
