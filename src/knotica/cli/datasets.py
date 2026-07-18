"""``knotica datasets`` helpers — trainset cold-start and golden freeze.

``bootstrap-train`` synthesizes seeded train examples from the topic's own
pages (LLM-grounded, ``source: seed_train`` — the cold-start scaffold that real
curation progressively displaces). ``freeze`` promotes human-reviewed golden
candidates into the held-out set.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from knotica.cli.common import (
    EXIT_ERROR,
    EXIT_NOT_CONFIGURED,
    EXIT_SUCCESS,
    common_parent,
    console_from_args,
    unconfigured,
)
from knotica.core.config import diagnose
from knotica.core.datasets_inventory import freeze_reviewed_dataset
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.evals.golden import GoldenCandidateError, GoldenSetContaminationError
from knotica.store import LocalFSStore

__all__ = ["configure", "run"]


def configure(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register ``datasets`` with ``bootstrap-train`` and ``freeze`` subcommands."""
    parser = subparsers.add_parser(
        "datasets",
        parents=[common_parent()],
        help="dataset helpers for train/golden prep",
        description=(
            "Cold-start a topic's trainset from its own pages (bootstrap-train) "
            "and freeze reviewed golden candidates into the held-out set (freeze)."
        ),
    )
    sub = parser.add_subparsers(dest="datasets_command", metavar="<subcommand>")
    boot = sub.add_parser(
        "bootstrap-train",
        help="synthesize seeded train examples from the topic's own pages (LLM)",
        description=(
            "Generate query-style QA pairs grounded in the topic's entity pages "
            "and append them to qa.jsonl with source seed_train. Bridges the "
            "cold start toward compile-ready; curated examples displace seeds "
            "in demo selection as the flywheel fills. Needs LLM credentials "
            "(CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY)."
        ),
    )
    boot.add_argument("--topic", required=True, metavar="NAME", help="topic to bootstrap")
    boot.add_argument(
        "--target", type=int, default=30, metavar="N", help="records to generate (default: 30)"
    )
    boot.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    freeze_cmd = sub.add_parser(
        "freeze",
        help="freeze reviewed golden candidates into held-out golden.jsonl",
        description=(
            "Promote golden.staging.reviewed.jsonl into golden.jsonl + MANIFEST.json "
            "(one commit). Refuses questions that overlap the trainset."
        ),
    )
    freeze_cmd.add_argument("--topic", required=True, metavar="NAME", help="topic to freeze")
    freeze_cmd.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return parser


def run(args: argparse.Namespace) -> int:
    console = console_from_args(args)
    command = getattr(args, "datasets_command", None)
    if command == "bootstrap-train":
        return _run_bootstrap_train(console, args)
    if command == "freeze":
        return _run_freeze(console, args)
    console.error("usage: knotica datasets {bootstrap-train,freeze} --topic NAME")
    return EXIT_ERROR


def _run_bootstrap_train(console: Any, args: argparse.Namespace) -> int:
    diagnosis = diagnose()
    if diagnosis.vault is None:
        return unconfigured(console)

    vault = diagnosis.vault
    store = LocalFSStore(vault.path)
    try:
        from knotica.evals.config import WORKER_SNAPSHOT
        from knotica.evals.llm import AnthropicClient
        from knotica.evals.train_bootstrap import bootstrap_trainset

        result = bootstrap_trainset(
            store,
            vault.path,
            args.topic,
            AnthropicClient(),
            WORKER_SNAPSHOT,
            target_n=max(1, args.target),
            on_page=lambda current, total, page: console.info(
                f"synthesizing {current}/{total} — {page}"
            ),
        )
    except KnoticaError as error:
        console.error(str(error))
        if error.fix:
            console.error(f"To fix: {error.fix}")
        return EXIT_NOT_CONFIGURED if error.code is ErrorCode.NOT_CONFIGURED else EXIT_ERROR

    if args.json:
        console.data(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        console.data(
            f"bootstrap-train topic={result['topic']} appended={result['appended']} "
            f"pages_read={result['pages_read']} source={result['source']}"
        )
    return EXIT_SUCCESS


def _run_freeze(console: Any, args: argparse.Namespace) -> int:
    diagnosis = diagnose()
    if diagnosis.vault is None:
        return unconfigured(console)

    vault = diagnosis.vault
    store = LocalFSStore(vault.path)
    try:
        result = freeze_reviewed_dataset(store, vault.path, args.topic)
    except (KnoticaError, GoldenSetContaminationError, GoldenCandidateError) as error:
        console.error(str(error))
        fix = getattr(error, "fix", None)
        if fix:
            console.error(f"To fix: {fix}")
        return (
            EXIT_NOT_CONFIGURED
            if getattr(error, "code", None) is ErrorCode.NOT_CONFIGURED
            else EXIT_ERROR
        )

    if args.json:
        console.data(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        console.data(
            f"freeze topic={result['topic']} n_frozen={result['n_frozen']} "
            f"commit={result['commit_sha'][:8]} below_floor={result['below_floor']}"
        )
    return EXIT_SUCCESS
