"""``knotica fill discover`` -- the on-demand gap-fill discovery trigger.

``discover`` registers directly into the Fill lane: the verb is already unique
there, so this module's own group level would name the lane twice.

``discover`` is the primary, human-invoked way to run the drain: it reads a
topic's open ``genuine_gap`` records, formulates one deterministic query per
gap, calls the real ``DiscoveryService`` (built from ``[gapfill.search]`` config
+ the env API key), and stages one ``pending`` suggestion per ranked candidate
to the committed ``suggestions.jsonl`` queue -- exactly the drain the opt-in
loop hook runs, but triggered by hand.

With no API key configured (or a topic with no open gaps), the drain is a clean
no-op: nothing is written and the command exits ``0`` (the honest empty state).

Module-load imports stay ``discovery``-free -- ``core.gapfill`` imports the
search chain lazily, so ``knotica.cli.gapfill`` does not drag ``discovery`` onto
the CLI import path (the import-boundary fitness test).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from knotica.cli.common import (
    EXIT_ERROR,
    EXIT_NOT_CONFIGURED,
    EXIT_SUCCESS,
    Console,
    common_parent,
    console_from_args,
    unconfigured,
)
from knotica.core import gapfill
from knotica.core.config import diagnose
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.store import LocalFSStore

__all__ = ["configure", "run"]


def configure(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    """Register ``discover`` into the caller's subparsers (the Fill lane)."""
    discover = subparsers.add_parser(
        "discover",
        parents=[common_parent(nested=True)],
        help="drain open genuine_gaps into pending suggestions (real search calls)",
        description=(
            "Read the topic's open genuine_gap records, run the configured search "
            "provider + OpenAlex enrichment, and stage one pending suggestion per "
            "ranked candidate to suggestions.jsonl. Needs KNOTICA_YOUCOM_API_KEY; "
            "with no key it writes nothing and exits 0."
        ),
    )
    discover.add_argument("--topic", required=True, metavar="NAME", help="topic to drain")
    discover.add_argument("--vault", metavar="PATH", help="vault root (default: knotica config)")
    discover.add_argument(
        "--max-gaps",
        type=int,
        default=None,
        metavar="N",
        help="cap the drain to the N highest-|quality_delta| open gaps (default: all)",
    )
    return discover


def run(args: argparse.Namespace) -> int:
    """Run the discovery drain for the requested topic."""
    return _run_discover(console_from_args(args), args)


def _run_discover(console: Console, args: argparse.Namespace) -> int:
    """Build the real discovery service and run the drain, or no-op cleanly."""
    vault = _resolve_vault(args.vault)
    if vault is None:
        return unconfigured(console)

    store = LocalFSStore(vault)
    try:
        service = gapfill.build_default_discovery_service()
        result = gapfill.refresh_suggestions_for_gaps(
            store, vault, args.topic, service=service, max_gaps=args.max_gaps
        )
    except KnoticaError as error:
        console.error(str(error))
        if error.fix:
            console.error(f"To fix: {error.fix}")
        return EXIT_NOT_CONFIGURED if error.code is ErrorCode.NOT_CONFIGURED else EXIT_ERROR

    if not result.service_available:
        console.info(
            "gapfill discover: no search provider configured "
            "(set KNOTICA_YOUCOM_API_KEY) — no-op, nothing staged."
        )
        return EXIT_SUCCESS

    console.data(
        f"gapfill discover topic={args.topic} gaps_considered={result.gaps_considered} "
        f"gaps_drained={result.gaps_drained} suggestions_staged={result.suggestions_written} "
        f"candidates_already_in_vault={result.candidates_already_in_vault} "
        f"stale_suggestions_closed={result.stale_suggestions_closed}"
    )
    return EXIT_SUCCESS


def _resolve_vault(explicit: str | None) -> Path | None:
    """Resolve the vault root from ``--vault`` or the knotica config (mirrors ``cli/loop``)."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    diagnosis = diagnose()
    if diagnosis.vault is None:
        return None
    return Path(diagnosis.vault.path)
