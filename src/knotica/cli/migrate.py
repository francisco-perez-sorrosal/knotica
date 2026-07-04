"""``knotica migrate`` -- schema-version migration (fallback-channel adapter).

A mutating command: it routes every vault write through the ``migrate``
operation's single :class:`~knotica.core.transaction.VaultTransaction`, never
touching the vault directly. Config is resolved fresh per invocation (the
stateless-server contract); an unconfigured vault prints the shared message and
exits 3.

Exit codes (documented interface):

* ``--check``: 4 when a migration is available, 0 when up-to-date -- and it
  never writes.
* Otherwise: 0 on success (including a no-op or a preview), 1 on a failed
  operation, 2 when confirmation is required but unobtainable (``--no-input``
  without ``--yes``), 3 when unconfigured.

The three-way migration never clobbers evolved files; ``--dry-run`` shows the
plan without writing, and an apply asks for confirmation unless ``--yes``.
"""

import argparse
import json
from pathlib import PurePath

from knotica.cli.common import (
    EXIT_ERROR,
    EXIT_MIGRATION_AVAILABLE,
    EXIT_MISUSE,
    EXIT_SUCCESS,
    Console,
    common_parent,
    console_from_args,
    unconfigured,
)
from knotica.core.config import resolve
from knotica.core.errors import KnoticaError
from knotica.core.operations import migrate as migrate_op
from knotica.store import LocalFSStore

__all__ = ["configure", "run"]


def configure(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the ``migrate`` subcommand and its flags."""
    parser = subparsers.add_parser(
        "migrate",
        parents=[common_parent()],
        help="run a schema-version migration",
        description="Template-diff three-way migration; never clobbers evolved files.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="report availability via exit code only (4 = available, 0 = up-to-date)",
    )
    parser.add_argument("--dry-run", action="store_true", help="show the plan without applying")
    parser.add_argument("--yes", action="store_true", help="apply without confirmation")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--topic", metavar="NAME", help="scope the migration to one topic")
    return parser


def run(args: argparse.Namespace) -> int:
    """Resolve config fresh, then check / preview / apply per the flags."""
    console = console_from_args(args)
    try:
        vault = resolve()
    except KnoticaError:
        return unconfigured(console)

    store = LocalFSStore(vault.path)
    topic = args.topic or ""

    plan = migrate_op(store, vault.path, topic=topic, apply=False)
    if _is_error(plan):
        return _report_error(console, plan)

    if args.check:
        return _check_exit(console, args, plan)
    if not plan["available"]:
        return _report_current(console, args, plan)
    if args.dry_run:
        return _report_preview(console, args, plan)
    return _confirm_and_apply(console, args, store, vault.path, topic, plan)


def _confirm_and_apply(
    console: Console,
    args: argparse.Namespace,
    store: LocalFSStore,
    vault_path: str | PurePath,
    topic: str,
    plan: dict[str, object],
) -> int:
    """Confirm (unless ``--yes``), then apply the migration through the operation."""
    if not args.yes and not _confirm(console, args, plan):
        # `--no-input` without `--yes`: confirmation is required input we cannot
        # obtain, so fail fast (exit 2) rather than silently applying or hanging.
        if args.no_input:
            console.error(
                "migrate needs confirmation to apply; pass --yes in non-interactive mode."
            )
            return EXIT_MISUSE
        console.info("Migration cancelled; the vault is unchanged.")
        return EXIT_SUCCESS

    applied = migrate_op(store, vault_path, topic=topic, apply=True)
    if _is_error(applied):
        return _report_error(console, applied)
    _emit(console, args, applied)
    if not args.json:
        console.info(
            f"Migrated {applied['scope']} to schema_version {applied['target_version']} "
            f"(commit {str(applied['commit_sha'])[:8]})."
        )
        _warn_conflicts(console, applied)
    return EXIT_SUCCESS


def _confirm(console: Console, args: argparse.Namespace, plan: dict[str, object]) -> bool:
    """Ask the user to confirm an apply; ``False`` when input is unavailable."""
    if args.no_input:
        return False
    _report_preview(console, args, plan)
    try:
        answer = input(f"Apply this migration to {plan['scope']}? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def _check_exit(console: Console, args: argparse.Namespace, plan: dict[str, object]) -> int:
    """``--check``: exit 4 when available, 0 when up-to-date; never writes."""
    _emit(console, args, plan)
    if plan["available"]:
        if not args.json:
            console.info(
                f"A schema migration is available for {plan['scope']}: "
                f"v{plan['current_version']} -> v{plan['target_version']}."
            )
        return EXIT_MIGRATION_AVAILABLE
    if not args.json:
        console.info(f"{plan['scope']} is up-to-date (schema_version {plan['target_version']}).")
    return EXIT_SUCCESS


def _report_current(console: Console, args: argparse.Namespace, plan: dict[str, object]) -> int:
    """Nothing to migrate: report up-to-date and exit successfully."""
    _emit(console, args, plan)
    if not args.json:
        console.info(f"{plan['scope']} is up-to-date (schema_version {plan['target_version']}).")
    return EXIT_SUCCESS


def _report_preview(
    console: Console,
    args: argparse.Namespace,
    plan: dict[str, object],
) -> int:
    """Show the migration plan without writing anything."""
    if args.json:
        _emit(console, args, plan)
        return EXIT_SUCCESS
    # The preview *is* this invocation's payload, so it goes to stdout.
    console.data(
        f"Migration available for {plan['scope']}: "
        f"v{plan['current_version']} -> v{plan['target_version']}."
    )
    additions = plan["additions"]
    if isinstance(additions, list) and additions:
        console.data(f"Would add: {', '.join(additions)}.")
    _warn_conflicts(console, plan)
    return EXIT_SUCCESS


def _warn_conflicts(console: Console, plan: dict[str, object]) -> None:
    """Name evolved files that were preserved and skipped, with guidance."""
    conflicts = plan["conflicts"]
    if isinstance(conflicts, list) and conflicts:
        console.warn(
            f"Preserved evolved files (not overwritten): {', '.join(conflicts)}. "
            "Reconcile any wanted template changes into them by hand."
        )


def _emit(console: Console, args: argparse.Namespace, envelope: dict[str, object]) -> None:
    """Write the JSON envelope to stdout when ``--json`` is set."""
    if args.json:
        console.data(json.dumps(envelope))


def _is_error(envelope: dict[str, object]) -> bool:
    """Whether an operation envelope is a typed failure."""
    return "error" in envelope


def _report_error(console: Console, envelope: dict[str, object]) -> int:
    """Print a failure envelope's message and fix to stderr; exit 1."""
    error = envelope["error"]
    if isinstance(error, dict):
        console.error(str(error.get("message", "migrate failed.")))
        console.error(f"To fix: {error.get('fix', '')}")
    return EXIT_ERROR
