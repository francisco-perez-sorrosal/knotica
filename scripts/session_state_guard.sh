#!/bin/sh
# Project-local SessionStart guard for this repository's .ai-state/ bookkeeping.
#
# **Not the plugin's `hooks/session_start.sh`.** That one ships to every end user
# who installs the knotica plugin and must only ever concern itself with their
# vault. This one is wired from the committed `.claude/settings.json`, so it runs
# only for someone working *in this repository* -- which is the correct scope for
# a development-workflow concern.
#
# Two jobs, both cheap and both silent when there is nothing to say:
#
#   1. Ensure the finalize git hooks exist. Git cannot install them on clone
#      (see `install_git_hooks.sh` for why), so a fresh clone or a new machine
#      silently loses them -- and the loss is invisible until ADR drafts have
#      quietly piled up unpromoted. Re-asserting them at session start is what
#      turns "someone must remember" into something that cannot be forgotten.
#
#   2. Report `.ai-state/` work that is pending or uncommitted. The hooks stage
#      finalize output but cannot commit it -- a hook must not create a commit
#      inside an in-flight git operation -- so the last step is a human's, and
#      it is the step most easily forgotten.
#
# Deliberately does **not** auto-commit. Everything finalize produces is derived
# and would be safe to commit mechanically, but a session-start hook that makes
# commits is a surprise the first time it lands on top of work in progress. The
# nudge names the command instead; the friction removed is the remembering, not
# the deciding.
#
# Contract: never blocks, never fails a session, exits 0 unconditionally.

set -u

ROOT="${CLAUDE_PROJECT_DIR:-}"
if [ -z "$ROOT" ]; then
	ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
fi
cd "$ROOT" 2>/dev/null || exit 0

# (1) Re-assert the hooks. Quiet when already correct, so a normal session
# prints nothing; the installer speaks up only when it actually changed
# something or refused to touch a foreign hook.
if [ -x "$ROOT/scripts/install_git_hooks.sh" ]; then
	sh "$ROOT/scripts/install_git_hooks.sh" --quiet 2>&1 || true
fi

# (2) Pending finalize state. Both checks are pure filesystem/porcelain reads --
# no python, no network -- so this stays well inside a session-start budget.
drafts_dir="$ROOT/.ai-state/decisions/drafts"
if [ -d "$drafts_dir" ]; then
	draft_count=$(find "$drafts_dir" -name '*.md' -type f 2>/dev/null | wc -l | tr -d ' ')
	branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
	if [ "${draft_count:-0}" -gt 0 ] && [ "$branch" = "main" ]; then
		echo "knotica: $draft_count ADR draft(s) on main await finalize. The hooks run it on the next commit or merge; to run it now: sh scripts/install_git_hooks.sh && git commit --allow-empty -m 'chore: trigger finalize'"
	fi
fi

# `observations.jsonl` is excluded on purpose: it is an append-only telemetry
# log written continuously by the session itself, so it is dirty essentially
# always. Including it would make this nudge fire every single session, which
# trains the reader to ignore it -- and the one time it mattered would look
# exactly like the hundred times it did not.
state_dirty=$(git status --porcelain -- .ai-state 2>/dev/null |
	grep -v '\.ai-state/observations\.jsonl$' | head -n 5)
if [ -n "$state_dirty" ]; then
	echo "knotica: .ai-state/ has uncommitted changes (finalize output is staged but never committed by a hook). Review and commit:"
	echo "$state_dirty" | sed 's/^/    /'
fi

exit 0
