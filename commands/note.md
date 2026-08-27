---
description: "[Tend] Save a personal note (marginalia) against a KB topic, anchored to the passage that provoked it."
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
4. Report the returned `placement` line verbatim — one line, nothing added.

To browse existing notes, use the dashboard's Notes pane or `tend action=notes notes_action=list`.
