#!/bin/sh
# Knotica SessionStart hook: never blocks session start.
#
# Contract (stdlib/POSIX-only, no external deps; never crash the session):
#   (c) config nudge  -> pure stdlib file check of config.toml (NO subprocess),
#                        so it fires instantly even on a cold machine.
#   (a) uvx presence  -> absent: print uv-install guidance, exit 0.
#   (b) pre-warm      -> ALWAYS backgrounded, never waited on (absorbs uv's
#                        one-time cold install cost off the session's critical path).
#   (d) schema behind plugin -> `migrate --check` exits 4 -> suggest /knotica:migrate.
#   (e) dirty work-tree      -> doctor surfaces a WARN -> offer the scoped rollback.
#
# (d)/(e) genuinely need the CLI resolved, so they run ONLY when knotica is
# already warm (a short bounded probe). On a cold machine the probe is abandoned
# and (d)/(e) are skipped this session -- they fire next session, warm. Net:
# cold path returns in well under 1s having shown the setup nudge and kicked off
# the background pre-warm. Nudges print to stdout; guidance never fails the
# session (always exit 0).

set -u

# ${CLAUDE_PLUGIN_ROOT} is set in the hook runtime; fall back to the repo root so
# the script is exercisable standalone (hooks/ -> repo root is one level up).
ROOT="${CLAUDE_PLUGIN_ROOT:-}"
if [ -z "$ROOT" ]; then
	ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
fi

# Run a command with a hard time bound (portable; macOS has no `timeout`). The
# watchdog kills the command if it outlives the budget; returns the command's
# exit status, or a non-zero (SIGTERM) status when the budget is exceeded.
run_bounded() {
	_secs=$1
	shift
	"$@" &
	_cmd_pid=$!
	( sleep "$_secs"; kill -TERM "$_cmd_pid" 2>/dev/null ) &
	_watch_pid=$!
	wait "$_cmd_pid" 2>/dev/null
	_rc=$?
	kill -TERM "$_watch_pid" 2>/dev/null
	wait "$_watch_pid" 2>/dev/null
	return "$_rc"
}

# (c) Config nudge -- stdlib-only, no subprocess, cold-safe. "Configured" means
# a non-empty config.toml that names a vault path (`[vaults.*] path = ...`).
config_ok=0
CONFIG_FILE="${KNOTICA_CONFIG:-$HOME/.config/knotica/config.toml}"
if [ -s "$CONFIG_FILE" ] && grep -Eq '^[[:space:]]*path[[:space:]]*=' "$CONFIG_FILE" 2>/dev/null; then
	config_ok=1
else
	echo "Knotica is not configured yet. Run /knotica:setup to point it at your vault."
fi

# (a) uvx presence: the launcher the plugin uses to run the stateless server.
if ! command -v uvx >/dev/null 2>&1; then
	echo "Knotica needs uv (its 'uvx' launcher is not on PATH)."
	echo "Install uv: https://docs.astral.sh/uv/getting-started/installation/"
	echo "Then reload this session so Knotica can start."
	exit 0
fi

# (b) ALWAYS-backgrounded, idempotent pre-warm. The subshell detaches the child
# so the hook returns immediately (no blocking wait); all streams are silenced.
( uvx --from "$ROOT" knotica --version >/dev/null 2>&1 & ) </dev/null >/dev/null 2>&1

# (d)/(e) need a resolved CLI. Probe warmth cheaply; if knotica is not already
# resolvable within the bound (cold install still running), skip them this
# session rather than block. Nothing more to check when unconfigured.
if [ "$config_ok" -eq 0 ]; then
	exit 0
fi
if ! run_bounded 1 uvx --from "$ROOT" knotica --version >/dev/null 2>&1; then
	exit 0 # cold: pre-warm is backgrounding the install; richer nudges wait for next session.
fi

# Warm path (each call is sub-second): the richer nudges.

# (d) Plugin-vs-vault schema mismatch: `migrate --check` exits 4 when available.
if uvx --from "$ROOT" knotica migrate --check >/dev/null 2>&1; then
	: # exit 0 -> up to date, nothing to nudge.
elif [ "$?" -eq 4 ]; then
	echo "Knotica's vault schema is behind the plugin. Run /knotica:migrate to update it."
fi

# (e) Dirty work-tree: surface doctor's WARN with the scoped-rollback offer.
doctor_out="$(uvx --from "$ROOT" knotica doctor 2>&1)"
case "$doctor_out" in
*"uncommitted changes"*)
	echo "Knotica vault has uncommitted changes. Review and roll back scoped with: knotica doctor --fix"
	;;
esac

# (f) Topic-awareness seed + "what needs my attention" nudge -- ONE combined
# CLI read serves both (the plain-text `--nudge` rendering already folds in
# the same wiki_status-shaped state: topics, pending suggestions,
# refused-awaiting-rework, compile-ready). Prints nothing when there is
# nothing to say (no topics, nothing pending).
nudge_out="$(uvx --from "$ROOT" knotica status --nudge 2>/dev/null)"
if [ -n "$nudge_out" ]; then
	echo "$nudge_out"
fi

exit 0
