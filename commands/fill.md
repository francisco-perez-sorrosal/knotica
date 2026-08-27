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
2. Call `fill action=session_status` with `topic`/`suggestion_id` to read the session's state and
   `next.actor`. Report the state plainly.
   - `next.actor` is `you`, `system`, or `none` — there is nothing for you to write. Report the
     state (waiting on approval, already merged, refused, blocked, or swept) and stop.
   - Only `next.actor: claude` continues past this point.
3. If the state is `not_started`, call `fill action=source_ingest_open` with `topic`/
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
