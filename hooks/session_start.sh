#!/bin/sh
# Knotica SessionStart hook: background pre-warm + first-run nudges.
#
# Contract (stdlib/POSIX-only, no external deps; never crash the session):
#   (a) uvx absent            -> print uv-install guidance, exit 0.
#   (b) uvx present           -> backgrounded, idempotent pre-warm of the server
#                                launcher so it does not block session start.
#   (c) vault unconfigured    -> `knotica doctor` exits 3 -> suggest /knotica:setup.
#   (d) schema behind plugin  -> `knotica migrate --check` exits 4 -> suggest /knotica:migrate.
#   (e) dirty vault work-tree -> doctor surfaces a WARN -> offer the scoped rollback.
#
# Nudges print to stdout (surfaced to the session). Warm path is <~1s; the first
# cold run pays uv's one-time install cost synchronously to still produce the
# nudge (the drill's expected first-impression), while the pre-warm backgrounds
# the server-launch warming. Guidance never fails the session (always exit 0).

set -u

# ${CLAUDE_PLUGIN_ROOT} is set in the hook runtime; fall back to the repo root so
# the script is exercisable standalone (hooks/ -> repo root is one level up).
ROOT="${CLAUDE_PLUGIN_ROOT:-}"
if [ -z "$ROOT" ]; then
	ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
fi

# (a) uvx presence: the launcher the plugin uses to run the stateless server.
if ! command -v uvx >/dev/null 2>&1; then
	echo "Knotica needs uv (its 'uvx' launcher is not on PATH)."
	echo "Install uv: https://docs.astral.sh/uv/getting-started/installation/"
	echo "Then reload this session so Knotica can start."
	exit 0
fi

# (b) Backgrounded, idempotent pre-warm. The subshell detaches the child so the
# hook returns immediately (no blocking wait); all streams are silenced.
( uvx --from "$ROOT" knotica --version >/dev/null 2>&1 & ) </dev/null >/dev/null 2>&1

# Nudge checks. `doctor` (full) gives us the config state via its exit code and
# the dirty-tree WARN via its human output in a single invocation.
doctor_out="$(uvx --from "$ROOT" knotica doctor 2>&1)"
doctor_ec=$?

# (c) Unconfigured vault: doctor exits 3. Nothing else is resolvable yet.
if [ "$doctor_ec" -eq 3 ]; then
	echo "Knotica is not configured yet. Run /knotica:setup to point it at your vault."
	exit 0
fi

# (d) Plugin-vs-vault schema mismatch: `migrate --check` exits 4 when available.
if uvx --from "$ROOT" knotica migrate --check >/dev/null 2>&1; then
	: # exit 0 -> up to date, nothing to nudge.
else
	if [ "$?" -eq 4 ]; then
		echo "Knotica's vault schema is behind the plugin. Run /knotica:migrate to update it."
	fi
fi

# (e) Dirty work-tree: surface doctor's WARN with the scoped-rollback offer.
case "$doctor_out" in
*"uncommitted changes"*)
	echo "Knotica vault has uncommitted changes. Review and roll back scoped with: knotica doctor --fix"
	;;
esac

exit 0
