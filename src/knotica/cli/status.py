"""``knotica status`` -- deterministic vault counts (registered stub).

The full surface (subparser + flags) registers now so ``--help`` is complete
and the dispatch registry is closed; the behavior lands in a later step. It
reports deterministic counts (pages per topic, curated "N to compile-ready",
last lint, unpushed commits) as a width-aware table or JSON.
"""

import argparse

from knotica.cli.common import common_parent

__all__ = ["configure", "run"]


def configure(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the ``status`` subcommand and its flags."""
    parser = subparsers.add_parser(
        "status",
        parents=[common_parent()],
        help="report deterministic vault counts",
        description="Report deterministic counts (pages, curated, unpushed) as a table or JSON.",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--topic", metavar="NAME", help="scope counts to one topic")
    parser.add_argument("--wide", action="store_true", help="show full output, ignoring $COLUMNS")
    return parser


def run(args: argparse.Namespace) -> int:
    """Not yet implemented -- the status report lands in a later step."""
    raise NotImplementedError("knotica status is not yet implemented")
