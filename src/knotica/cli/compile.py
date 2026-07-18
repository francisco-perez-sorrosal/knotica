"""``knotica compile`` — DSPy MIPROv2 compile of the query op on a vault clone."""

from __future__ import annotations

import argparse
import json
import sys

from knotica.cli.common import (
    EXIT_ERROR,
    EXIT_MISUSE,
    EXIT_NOT_CONFIGURED,
    EXIT_SUCCESS,
    common_parent,
    console_from_args,
    unconfigured,
)
from knotica.core.compile_promote import compile_promote
from knotica.core.compile_run import run_compile
from knotica.core.config import diagnose
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.store import LocalFSStore

__all__ = ["configure", "run"]


def configure(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "compile",
        parents=[common_parent()],
        help="compile the query program for a topic (clone → branch)",
        description=(
            "Gate on ≥30 query-train examples and a held-out golden set, clone the "
            "vault, optimize with MIPROv2 (or bootstrap), write "
            "<topic>/.knotica/compiled/, and return a compile/<topic>/<sha> branch "
            "for human review. Use `compile promote` to merge after review."
        ),
    )
    parser.add_argument("--topic", metavar="NAME", help="topic to compile")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument(
        "--no-mipro",
        action="store_true",
        help="skip MIPROv2 and write a bootstrap artifact (demos + guidance)",
    )

    compile_sub = parser.add_subparsers(dest="compile_command", metavar="<subcommand>")
    promote = compile_sub.add_parser(
        "promote",
        help="merge a reviewed compile/<topic>/… branch into the default branch",
        description=(
            "Human gate after compile_run: merge compile/<topic>/<sha> into main/master "
            "with --no-ff under the vault lock. Refuses arbitrary branch names and dirty trees."
        ),
    )
    promote.add_argument("--topic", required=True, metavar="NAME", help="topic slug")
    promote.add_argument(
        "--branch",
        required=True,
        metavar="NAME",
        help="compile branch from compile_run (compile/<topic>/<shortsha>)",
    )
    promote.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    promote_mode = promote.add_mutually_exclusive_group(required=True)
    promote_mode.add_argument(
        "--dry-run",
        action="store_true",
        help="preview merge without changing the vault",
    )
    promote_mode.add_argument(
        "--apply",
        action="store_true",
        help="merge the compile branch into the default branch",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    if getattr(args, "compile_command", None) == "promote":
        return _run_promote(args)

    if not args.topic:
        print(
            "knotica compile: error: the following arguments are required: --topic", file=sys.stderr
        )
        return EXIT_MISUSE

    console = console_from_args(args)
    diagnosis = diagnose()
    if diagnosis.vault is None:
        return unconfigured(console)

    vault = diagnosis.vault
    store = LocalFSStore(vault.path)
    try:
        result = run_compile(
            store,
            vault.path,
            args.topic,
            config_detail=diagnosis.detail or "configured",
            use_mipro=not args.no_mipro,
        )
    except KnoticaError as error:
        console.error(str(error))
        if error.fix:
            console.error(f"To fix: {error.fix}")
        if error.code is ErrorCode.NOT_CONFIGURED:
            return EXIT_NOT_CONFIGURED
        return EXIT_ERROR

    payload = result.render()
    if args.json:
        console.data(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        console.data(
            f"compile topic={payload['topic']} stage={payload['stage']} "
            f"branch={payload['branch']} "
            f"scalar {payload['scalar_before']} → {payload['scalar_after']}"
        )
        console.error(payload["message"])
    return EXIT_SUCCESS


def _run_promote(args: argparse.Namespace) -> int:
    console = console_from_args(args)
    diagnosis = diagnose()
    if diagnosis.vault is None:
        return unconfigured(console)

    store = LocalFSStore(diagnosis.vault.path)
    payload = compile_promote(
        store,
        diagnosis.vault.path,
        args.topic,
        args.branch,
        apply=bool(args.apply),
    )
    if args.json:
        console.data(json.dumps(payload, ensure_ascii=False, indent=2))
    elif "error" in payload:
        error = payload["error"]
        console.error(error.get("message", "compile promote failed"))
        if error.get("fix"):
            console.error(f"To fix: {error['fix']}")
    else:
        console.data(payload.get("message", "compile promote finished"))
        if payload.get("commit_sha"):
            console.data(f"commit={payload['commit_sha']}")
    return EXIT_ERROR if "error" in payload else EXIT_SUCCESS
