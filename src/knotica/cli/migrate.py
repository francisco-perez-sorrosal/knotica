"""``knotica migrate`` -- schema-version migration (registered stub).

The full surface (subparser + flags) registers now so ``--help`` is complete
and the dispatch registry is closed; the behavior lands in a later step. It
does a template-diff three-way migration that never clobbers evolved files;
``--check`` returns exit 4 when a migration is available, exit 0 when
up-to-date.
"""

import argparse

from knotica.cli.common import common_parent

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
    parser.add_argument("--dry-run", action="store_true", help="show the diff without applying")
    parser.add_argument("--yes", action="store_true", help="apply without confirmation")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--topic", metavar="NAME", help="scope the migration to one topic")
    return parser


def run(args: argparse.Namespace) -> int:
    """Not yet implemented -- the migration lands in a later step."""
    raise NotImplementedError("knotica migrate is not yet implemented")
