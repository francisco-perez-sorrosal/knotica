"""``knotica guillotine`` -- claim-level memory audit and reversible retraction."""

from __future__ import annotations

import argparse
import json

from knotica.cli.common import (
    EXIT_MISUSE,
    EXIT_NOT_CONFIGURED,
    EXIT_SUCCESS,
    Console,
    common_parent,
    console_from_args,
    unconfigured,
)
from knotica.core.config import ConfigState, diagnose
from knotica.core.operations.guillotine import apply_guillotine, persist_guillotine_artifacts
from knotica.guillotine.report import artifact_paths_for, build_report, render_cli_summary
from knotica.guillotine.runner import (
    ClaimNotFoundError,
    PatchGenerationError,
    parse_verdict_override,
    run_guillotine,
)
from knotica.store import LocalFSStore

__all__ = ["configure", "run"]

#: Guillotine-specific exit codes (see feature spec).
EXIT_CLAIM_NOT_FOUND = 1
EXIT_PATCH_FAILED = 3
EXIT_APPLY_FAILED = 4


def configure(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the ``guillotine`` subcommand."""
    parser = subparsers.add_parser(
        "guillotine",
        parents=[common_parent()],
        help="audit, classify, and retract contested claims from the wiki",
        description=(
            "Put a claim on trial: find mentions, classify roles, score risk, "
            "generate a report and patch. Dry-run by default."
        ),
    )
    parser.add_argument("claim", help="target claim text to search and adjudicate")
    parser.add_argument("--topic", required=True, metavar="NAME", help="limit search to this topic")
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="generate report and patch only (default: true)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply generated patch and commit changes (implies not dry-run)",
    )
    parser.add_argument(
        "--verdict",
        metavar="NAME",
        choices=[
            "keep",
            "qualify",
            "demote",
            "dispute",
            "retract",
            "quarantine_source",
            "delete_unsupported_synthesis",
        ],
        help="override the recommended verdict",
    )
    parser.add_argument("--json", action="store_true", help="emit structured JSON on stdout")
    parser.add_argument(
        "--include-sources",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="include raw source files in search (default: true)",
    )
    parser.add_argument(
        "--include-reports",
        action="store_true",
        help="include previous guillotine reports in search",
    )
    parser.add_argument("--max-results", type=int, default=50, metavar="N", help="limit passages")
    parser.add_argument(
        "--out",
        metavar="PATH",
        help="reserved for future custom output directory (currently vault reports/)",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    """Execute a guillotine trial."""
    console = console_from_args(args)
    diagnosis = diagnose()
    if diagnosis.vault is None:
        return _report_unconfigured(
            console, diagnosis.state, diagnosis.detail, diagnosis.remediation
        )

    dry_run = args.dry_run and not args.apply
    if args.apply and args.dry_run is False and not args.apply:
        pass  # explicit --no-dry-run without --apply still dry-runs artifacts only

    try:
        parse_verdict_override(args.verdict)
    except ValueError as error:
        console.error(str(error))
        return EXIT_MISUSE

    vault_path = diagnosis.vault.path
    store = LocalFSStore(vault_path)

    try:
        result, diff_text = run_guillotine(
            store,
            vault_path,
            args.claim,
            topic=args.topic,
            verdict=args.verdict,
            include_sources=args.include_sources,
            include_reports=args.include_reports,
            max_results=args.max_results,
        )
    except ClaimNotFoundError as error:
        console.error(str(error))
        return EXIT_CLAIM_NOT_FOUND
    except (ValueError, FileNotFoundError) as error:
        console.error(str(error))
        return EXIT_MISUSE
    except PatchGenerationError as error:
        console.error(str(error))
        return EXIT_PATCH_FAILED

    summary = _short_summary(result.claim)
    if dry_run:
        envelope = persist_guillotine_artifacts(store, result, diff_text)
        if "error" in envelope:
            console.error(str(envelope["error"]))
            return EXIT_APPLY_FAILED
        artifacts = artifact_paths_for(result)
        report = build_report(result, artifacts, dry_run=True)
    else:
        envelope = apply_guillotine(store, vault_path, result, diff_text, summary=summary)
        if "error" in envelope:
            console.error(str(envelope["error"]))
            return EXIT_APPLY_FAILED
        artifacts = artifact_paths_for(result)
        report = build_report(
            result,
            artifacts,
            dry_run=False,
            commit_sha=str(envelope.get("commit_sha", "")),
        )

    if args.json:
        console.data(_json_output(report))
    else:
        header = "Memory Guillotine: DRY RUN" if dry_run else "Memory Guillotine: APPLIED"
        console.info(header)
        console.info("")
        console.info(render_cli_summary(report))

    if args.out:
        console.warn(
            f"--out is not yet implemented; artifacts written under {artifacts.report_path}"
        )

    return EXIT_SUCCESS


def _report_unconfigured(
    console: Console, state: ConfigState, detail: str, remediation: str
) -> int:
    if state == ConfigState.UNCONFIGURED:
        return unconfigured(console)
    console.error(detail)
    if remediation:
        console.error(f"To fix: {remediation}")
    return EXIT_NOT_CONFIGURED


def _short_summary(claim: str) -> str:
    words = claim.split()
    if len(words) <= 8:
        return claim.rstrip(".")
    return " ".join(words[:8]).rstrip(".,;:") + "…"


def _json_output(report) -> str:
    payload = {
        "claim": report.claim,
        "normalized_claim": report.normalized_claim,
        "topic": report.topic,
        "recommendation": report.recommendation.value,
        "risk_score": report.risk_score,
        "summary": report.summary,
        "status": report.status,
        "applied": report.applied,
        "commit_sha": report.commit_sha,
        "artifacts": {
            "report_path": report.artifacts.report_path,
            "diff_path": report.artifacts.diff_path,
            "json_path": report.artifacts.json_path,
        },
        "passages": [
            {
                "path": passage.path,
                "line_start": passage.line_start,
                "line_end": passage.line_end,
                "role": passage.role.value,
                "risk": passage.risk,
                "suggested_action": passage.suggested_action,
            }
            for passage in report.passages
        ],
        "patches": [
            {
                "path": patch.path,
                "line_start": patch.line_start,
                "line_end": patch.line_end,
                "action": patch.action,
            }
            for patch in report.patches
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
