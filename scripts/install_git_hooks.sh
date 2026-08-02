#!/bin/sh
# Install the .ai-state/ finalize git hooks into this repository.
#
# Why this exists: git deliberately never installs a repository's own hooks on
# clone -- otherwise `git clone` would execute arbitrary code -- and
# `core.hooksPath` lives in `.git/config`, which is not cloned either. So no
# repository can make its hooks automatic by committing something. The best any
# project can do is ship an idempotent installer and make running it effortless.
# `.claude/settings.json` runs this at session start so, in practice, nobody
# ever types it.
#
# What the hooks do: `git-finalize-hook.sh` is a multiplexed dispatcher that
# promotes `.ai-state/decisions/drafts/` fragments to `<NNN>-<slug>.md` on main,
# rewrites `dec-draft-<hash>` cross-references, regenerates DECISIONS_INDEX.md,
# and reconciles the tech-debt ledger. It is state-driven and idempotent -- it
# asks "are there drafts on main?", not "did a merge just happen" -- which is
# why three triggers (post-merge, post-commit, post-checkout) can share it
# safely.
#
# Safety properties, all deliberate:
#   - Never clobbers a hook this script did not install. A pre-existing
#     post-commit from `pre-commit` or husky is reported and left alone.
#   - No-ops cleanly when praxion is absent, so a clone on a machine without it
#     is a message rather than three broken symlinks.
#   - Worktree-safe: hooks live in the common git dir, shared by every worktree,
#     so installing once covers them all.
#   - Idempotent: re-running changes nothing and says so.
#
# Usage:  sh scripts/install_git_hooks.sh [--quiet]
# Env:    PRAXION_ROOT   override the praxion checkout location

set -u

QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1

say() {
	[ "$QUIET" -eq 1 ] || echo "$@"
}

# Always reported, even under --quiet: a refusal or a change the user must know
# about is not routine chatter.
warn() {
	echo "$@" >&2
}

HOOK_NAMES="post-merge post-commit post-checkout"

# --- Resolve the dispatcher ------------------------------------------------
# Order: explicit override, then the developer checkout, then the installed
# plugin cache (newest version last by sort order). The plugin cache is the
# fallback rather than the default because its path carries a version number --
# a symlink into it silently breaks on the next plugin upgrade.
find_dispatcher() {
	if [ -n "${PRAXION_ROOT:-}" ] && [ -x "$PRAXION_ROOT/scripts/git-finalize-hook.sh" ]; then
		echo "$PRAXION_ROOT/scripts/git-finalize-hook.sh"
		return 0
	fi
	if [ -x "$HOME/dev/praxion/scripts/git-finalize-hook.sh" ]; then
		echo "$HOME/dev/praxion/scripts/git-finalize-hook.sh"
		return 0
	fi
	_cached=$(ls -d "$HOME"/.claude/plugins/cache/*/i-am/*/scripts/git-finalize-hook.sh 2>/dev/null | sort -V | tail -n 1)
	if [ -n "$_cached" ] && [ -x "$_cached" ]; then
		echo "$_cached"
		return 0
	fi
	return 1
}

if ! git rev-parse --git-common-dir >/dev/null 2>&1; then
	warn "install_git_hooks: not inside a git repository; nothing to do."
	exit 0
fi

# `--git-common-dir` (not `--git-dir`) is what makes this worktree-safe: every
# worktree shares one hooks directory, so one install covers all of them.
COMMON_DIR=$(git rev-parse --git-common-dir)
case "$COMMON_DIR" in
/*) ;;
*) COMMON_DIR="$(git rev-parse --show-toplevel)/$COMMON_DIR" ;;
esac
HOOKS_DIR="$COMMON_DIR/hooks"

if ! DISPATCHER=$(find_dispatcher); then
	say "install_git_hooks: praxion not found -- ADR finalize hooks not installed."
	say "  Set PRAXION_ROOT, or clone praxion to ~/dev/praxion, then re-run:"
	say "    sh scripts/install_git_hooks.sh"
	exit 0
fi

mkdir -p "$HOOKS_DIR" || exit 1

installed=0
skipped=0
refused=0

for name in $HOOK_NAMES; do
	target="$HOOKS_DIR/$name"
	if [ -L "$target" ]; then
		current=$(readlink "$target")
		if [ "$current" = "$DISPATCHER" ]; then
			skipped=$((skipped + 1))
			continue
		fi
		case "$current" in
		*git-finalize-hook.sh)
			# Ours, but pointing at a different (moved or upgraded) praxion. Re-point.
			ln -sfn "$DISPATCHER" "$target" || exit 1
			installed=$((installed + 1))
			continue
			;;
		*)
			warn "install_git_hooks: refusing to replace $name -> $current (not ours). Leaving it alone."
			refused=$((refused + 1))
			continue
			;;
		esac
	fi
	if [ -e "$target" ]; then
		warn "install_git_hooks: refusing to replace existing $name hook (a regular file). Leaving it alone."
		refused=$((refused + 1))
		continue
	fi
	ln -sfn "$DISPATCHER" "$target" || exit 1
	installed=$((installed + 1))
done

if [ "$installed" -gt 0 ]; then
	echo "Installed $installed ADR finalize git hook(s) -> $DISPATCHER"
elif [ "$refused" -gt 0 ]; then
	warn "install_git_hooks: $refused hook(s) left untouched; $skipped already correct."
else
	say "install_git_hooks: already installed ($skipped hooks)."
fi

exit 0
