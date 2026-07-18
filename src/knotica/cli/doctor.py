"""``knotica doctor`` -- deterministic mechanical health checks + scoped repair.

``doctor`` is the deterministic pre/post harness guard whose exit code gates the
hooks: it **never invokes an LLM**. Check logic lives in
:mod:`knotica.core.doctor`. Mutations go through
:func:`knotica.core.operations.doctor_repair.doctor_repair` only
(``doctor repair --apply``) — never unscoped ``git restore .``.

``--fix`` remains read-only guidance pointing at ``doctor repair``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from knotica.cli.common import (
    EXIT_ERROR,
    EXIT_SUCCESS,
    Console,
    Status,
    common_parent,
    console_from_args,
    unconfigured,
)
from knotica.core.config import ConfigState, diagnose
from knotica.core.doctor import (
    DOCTOR_JSON_SCHEMA_VERSION,
    CheckRow,
    build_doctor_payload,
    build_fix_guidance,
    run_doctor_checks,
)
from knotica.core.operations.doctor_repair import doctor_repair
from knotica.store import LocalFSStore

__all__ = ["DOCTOR_JSON_SCHEMA_VERSION", "configure", "run"]


def configure(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the ``doctor`` command, flags, and ``repair`` subcommand."""
    parser = subparsers.add_parser(
        "doctor",
        parents=[common_parent()],
        help="run deterministic mechanical health checks",
        description="Run deterministic health checks; the exit code gates the hooks.",
    )
    parser.add_argument("--quick", action="store_true", help="run the SessionStart subset only")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument(
        "--fix",
        action="store_true",
        help="show scoped repair guidance (read-only; use `doctor repair` to apply)",
    )

    doctor_sub = parser.add_subparsers(dest="doctor_command", metavar="<subcommand>")
    repair = doctor_sub.add_parser(
        "repair",
        help="path-scoped restore of dirty worktree paths to HEAD",
        description=(
            "Restore explicitly selected dirty paths to HEAD. Never runs "
            "`git restore .`. Dry-run lists candidates; apply requires --paths "
            "or --all-tracked."
        ),
    )
    repair_mode = repair.add_mutually_exclusive_group(required=True)
    repair_mode.add_argument(
        "--dry-run",
        action="store_true",
        help="list dirty paths without restoring",
    )
    repair_mode.add_argument(
        "--apply",
        action="store_true",
        help="restore selected paths to HEAD under the vault lock",
    )
    repair.add_argument(
        "--paths",
        nargs="+",
        default=[],
        metavar="PATH",
        help="vault-relative paths to restore (required with --apply unless --all-tracked)",
    )
    repair.add_argument(
        "--all-tracked",
        action="store_true",
        help="with --apply: restore every tracked dirty path",
    )
    repair.add_argument(
        "--delete-untracked",
        action="store_true",
        help="allow selected untracked paths to be deleted (destructive for those paths only)",
    )
    repair.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    """Resolve config and dispatch checks or repair."""
    console = console_from_args(args)
    if getattr(args, "doctor_command", None) == "repair":
        return _run_repair(console, args)

    diagnosis = diagnose()
    if diagnosis.state == ConfigState.UNCONFIGURED:
        return _report_unconfigured(console, args)
    if diagnosis.vault is None:
        rows = [
            CheckRow(
                Status.FAIL,
                "config",
                f"{diagnosis.state.value}: {diagnosis.detail}",
                diagnosis.remediation or None,
            )
        ]
        return _render(console, args, vault_path=None, rows=rows)

    vault = diagnosis.vault
    store = LocalFSStore(vault.path)
    rows = run_doctor_checks(
        store,
        vault.path,
        config_detail=diagnosis.detail,
        quick=args.quick,
    )
    return _render(console, args, vault_path=vault.path, rows=rows)


def _run_repair(console: Console, args: argparse.Namespace) -> int:
    diagnosis = diagnose()
    if diagnosis.state == ConfigState.UNCONFIGURED or diagnosis.vault is None:
        return unconfigured(console)
    store = LocalFSStore(diagnosis.vault.path)
    payload = doctor_repair(
        store,
        diagnosis.vault.path,
        apply=bool(args.apply),
        paths=tuple(args.paths or ()),
        all_tracked=bool(args.all_tracked),
        delete_untracked=bool(args.delete_untracked),
    )
    if args.json:
        console.data(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _render_repair_human(console, payload)
    return EXIT_ERROR if "error" in payload else EXIT_SUCCESS


def _render_repair_human(console: Console, payload: dict) -> None:
    if "error" in payload:
        error = payload["error"]
        console.data(f"FAIL  doctor repair: {error.get('message', 'failed')}")
        if error.get("fix"):
            console.data(f"      -> {error['fix']}")
        return
    mode = payload.get("mode", "?")
    console.data(f"knotica doctor repair ({mode})")
    console.data("")
    if mode == "dry-run":
        entries = payload.get("entries") or []
        if not entries:
            console.data("  work tree is clean — nothing to restore")
        else:
            console.data(f"  {len(entries)} dirty path(s):")
            for entry in entries:
                kind = "untracked" if entry.get("untracked") else "tracked"
                console.data(f"    {entry.get('code', '??')}  {entry['path']}  ({kind})")
            console.data("")
            console.data("  To restore tracked paths:")
            console.data("    knotica doctor repair --apply --paths <path>...")
            console.data("    knotica doctor repair --apply --all-tracked")
    else:
        restored = payload.get("restored") or []
        console.data(f"  restored {len(restored)} path(s) to HEAD")
        for path in restored:
            console.data(f"    {path}")
    if payload.get("message"):
        console.data("")
        console.data(f"  {payload['message']}")


def _report_unconfigured(console: Console, args: argparse.Namespace) -> int:
    """Emit the unconfigured result -- JSON envelope or the shared stderr message."""
    if args.json:
        console.data(
            json.dumps(
                {
                    "schema_version": DOCTOR_JSON_SCHEMA_VERSION,
                    "vault": None,
                    "quick": bool(getattr(args, "quick", False)),
                    "ok": False,
                    "exit_code": 3,
                    "state": ConfigState.UNCONFIGURED.value,
                    "checks": [],
                    "summary": {"pass": 0, "warn": 0, "fail": 0},
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return unconfigured(console)


def _render(
    console: Console, args: argparse.Namespace, *, vault_path: Path | None, rows: list[CheckRow]
) -> int:
    """Render the rows (JSON or human checklist) and return the exit code."""
    exit_code = EXIT_ERROR if any(r.status == Status.FAIL for r in rows) else EXIT_SUCCESS
    if args.json:
        console.data(
            json.dumps(
                build_doctor_payload(
                    vault_path, rows, quick=args.quick, include_fix=bool(args.fix)
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return exit_code
    _render_human(console, vault_path, rows, exit_code)
    if args.fix:
        _render_fix_guidance(console, vault_path, rows)
    return exit_code


def _render_human(
    console: Console, vault_path: Path | None, rows: list[CheckRow], exit_code: int
) -> None:
    """Print the aligned PASS/WARN/FAIL checklist to stdout with a summary footer."""
    header = "knotica doctor"
    if vault_path is not None:
        header = f"{header}    vault: {vault_path}"
    console.data(header)
    console.data("")
    width = max((len(r.name) for r in rows), default=0)
    for row in rows:
        glyph = console.status_glyph(row.status)
        console.data(f"  {glyph}  {row.name.ljust(width)}  {row.message}")
        if row.remediation is not None and row.status != Status.PASS:
            console.data(f"        {' ' * width}  -> {row.remediation}")
    console.data("")
    counts = {
        "pass": sum(r.status == Status.PASS for r in rows),
        "warn": sum(r.status == Status.WARN for r in rows),
        "fail": sum(r.status == Status.FAIL for r in rows),
    }
    trailer = " — see remediations above." if exit_code != EXIT_SUCCESS or counts["warn"] else "."
    console.data(f"{counts['pass']} pass, {counts['warn']} warn, {counts['fail']} fail{trailer}")


def _render_fix_guidance(console: Console, vault_path: Path | None, rows: list[CheckRow]) -> None:
    """Surface scoped repair guidance for a dirty tree (read-only)."""
    guidance = build_fix_guidance(vault_path, rows)
    if guidance is None:
        return
    console.data("")
    console.data("--fix: review dirty paths, then restore them scoped:")
    for command in guidance["commands"]:
        console.data(f"  {command}")
