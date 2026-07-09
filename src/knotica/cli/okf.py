"""``knotica okf`` -- native OKF compatibility commands."""

from __future__ import annotations

import argparse
import sys
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
from knotica.okf.check import OkfCheckResult, check_vault
from knotica.okf.export import ExportOptions, ExportResult, export_bundle
from knotica.okf.repair import RepairOptions, RepairResult, repair_vault
from knotica.store import LocalFSStore

__all__ = ["configure", "run"]


def configure(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the ``okf`` command group."""
    parser = subparsers.add_parser(
        "okf",
        parents=[common_parent()],
        help="native OKF compatibility: check, export, repair",
    )
    okf_sub = parser.add_subparsers(dest="okf_command", metavar="<subcommand>")

    check_parser = okf_sub.add_parser("check", help="check native OKF compatibility")
    check_parser.add_argument("--strict", action="store_true", help="fail on broken links")
    check_parser.add_argument(
        "--export-ready",
        action="store_true",
        help="preview export cleanliness",
    )

    export_parser = okf_sub.add_parser("export", help="export a pure OKF bundle")
    export_parser.add_argument("--output", "-o", required=True, type=Path)
    export_parser.add_argument("--pure", action="store_true", help="strip Knotica extensions")
    export_parser.add_argument(
        "--link-style",
        choices=["bundle-relative", "relative"],
        default="bundle-relative",
    )
    export_parser.add_argument("--lossy-embeds", action="store_true")
    export_parser.add_argument("--force", action="store_true")
    export_parser.add_argument(
        "--export-ready",
        action="store_true",
        help="fail when bundle is not fully Markdown-link clean",
    )

    repair_parser = okf_sub.add_parser("repair", help="repair vault OKF compatibility")
    repair_group = repair_parser.add_mutually_exclusive_group(required=True)
    repair_group.add_argument("--dry-run", action="store_true")
    repair_group.add_argument("--apply", action="store_true")
    repair_parser.add_argument("--force", action="store_true")

    return parser


def run(args: argparse.Namespace) -> int:
    """Execute an ``okf`` subcommand."""
    console = console_from_args(args)
    diagnosis = diagnose()
    if diagnosis.state == ConfigState.UNCONFIGURED or diagnosis.vault is None:
        return unconfigured(console)

    store = LocalFSStore(diagnosis.vault.path)
    command = getattr(args, "okf_command", None)
    if command is None:
        print("knotica okf: specify a subcommand: check, export, repair", file=sys.stderr)
        return EXIT_ERROR

    if command == "check":
        return _run_check(store, args, console)
    if command == "export":
        return _run_export(store, args, console)
    if command == "repair":
        return _run_repair(store, args, console)
    return EXIT_ERROR


def _run_check(store: LocalFSStore, args: argparse.Namespace, console: Console) -> int:
    result = check_vault(
        store,
        strict=args.strict,
        export_ready=args.export_ready,
    )
    console.info("Knotica OKF Check")
    _status_line(console, Status.PASS if not result.failed else Status.FAIL, result.status)
    console.info(f"Bundle root: {result.bundle_root}")
    console.info(f"Concept files checked: {result.concept_files_checked}")
    console.info(f"Reserved files checked: {result.reserved_files_checked}")
    if result.errors:
        console.info("Errors:")
        for err in result.errors:
            _status_line(console, Status.FAIL, f"{err.path}: {err.message}")
    if result.warnings:
        console.info(f"Warnings: {len(result.warnings)}")
        if args.verbose:
            for warning in result.warnings:
                _status_line(console, Status.WARN, warning)
    if result.strict_failures:
        for failure in result.strict_failures:
            _status_line(console, Status.FAIL, failure)
    return EXIT_SUCCESS if not result.failed else EXIT_ERROR


def _run_export(store: LocalFSStore, args: argparse.Namespace, console: Console) -> int:
    try:
        result = export_bundle(
            store,
            ExportOptions(
                output=args.output,
                pure=args.pure,
                link_style=args.link_style,
                lossy_embeds=args.lossy_embeds,
                force=args.force,
                export_ready=args.export_ready,
            ),
        )
    except ValueError as error:
        console.error(str(error))
        return EXIT_ERROR

    console.info("Knotica OKF Export")
    _status_line(console, Status.PASS, result.status)
    console.info(f"Output path: {result.output_path}")
    console.info(f"Files exported: {result.files_exported}")
    console.info(f"Wikilinks converted: {result.wikilinks_converted}")
    console.info(f"Post-export check: {result.post_check_status}")
    if result.report_path:
        console.info(f"Report: {result.report_path}")
    if result.warnings and args.verbose:
        for warning in result.warnings:
            _status_line(console, Status.WARN, warning)
    return EXIT_SUCCESS if result.status != "FAILED" else EXIT_ERROR


def _run_repair(store: LocalFSStore, args: argparse.Namespace, console: Console) -> int:
    try:
        result = repair_vault(
            store,
            RepairOptions(apply=args.apply, force=args.force),
        )
    except ValueError as error:
        console.error(str(error))
        return EXIT_ERROR

    mode = "apply" if args.apply else "dry-run"
    console.info(f"Knotica OKF Repair ({mode})")
    _status_line(console, Status.PASS if result.status != "FAILED" else Status.FAIL, result.status)
    console.info(f"Files to change: {len(result.files_changed)}")
    if args.verbose:
        for path in result.files_changed:
            console.info(f"  {path}")
    if result.report_path:
        console.info(f"Report: {result.report_path}")
    if result.commit_sha:
        console.info(f"Commit: {result.commit_sha}")
        console.info(f"Rollback: git revert {result.commit_sha}")
    return EXIT_SUCCESS if result.status != "FAILED" else EXIT_ERROR


def _status_line(console: Console, status: str, message: str) -> None:
    console.info(f"{console.status_glyph(status)} {message}")
