"""``knotica doctor`` -- deterministic mechanical health checks (registered stub).

The full surface (subparser + flags) registers now so ``--help`` is complete
and the dispatch registry is closed; the behavior lands in a later step.
``doctor`` never invokes an LLM -- it is the deterministic pre/post harness
guard whose exit code gates the hooks (semantic checks belong to ``lint``).
"""

import argparse

from knotica.cli.common import common_parent

__all__ = ["configure", "run"]


def configure(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the ``doctor`` subcommand and its flags."""
    parser = subparsers.add_parser(
        "doctor",
        parents=[common_parent()],
        help="run deterministic mechanical health checks",
        description="Run deterministic health checks; the exit code gates the hooks.",
    )
    parser.add_argument("--quick", action="store_true", help="run the SessionStart subset only")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--fix", action="store_true", help="offer rollback for fixable items")
    return parser


def run(args: argparse.Namespace) -> int:
    """Not yet implemented -- the health checks land in a later step."""
    raise NotImplementedError("knotica doctor is not yet implemented")
