"""``knotica init`` -- fallback-channel setup wizard (registered stub).

The full surface (subparser + flags) registers now so ``--help`` is complete
and the dispatch registry is closed; the behavior lands in a later step. This
adapter will scaffold the vault, git-init (optional private remote), write
``config.toml``, register the MCP server, and pre-warm -- always through the
core seam, never writing the vault directly.
"""

import argparse

from knotica.cli.common import common_parent

__all__ = ["configure", "run"]


def configure(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the ``init`` subcommand and its flags."""
    parser = subparsers.add_parser(
        "init",
        parents=[common_parent()],
        help="scaffold a vault and write config.toml (setup wizard)",
        description="Scaffold a knotica vault, register the MCP server, and pre-warm.",
    )
    parser.add_argument("--yes", action="store_true", help="accept all defaults (non-interactive)")
    parser.add_argument("--vault", metavar="PATH", help="filesystem path for the new vault")
    parser.add_argument("--topic", metavar="NAME", help="seed an initial topic")
    parser.add_argument(
        "--remote",
        choices=("none", "gh-private"),
        default="none",
        help="create a git remote (default: none)",
    )
    parser.add_argument("--desktop", action="store_true", help="patch Claude Desktop config")
    return parser


def run(args: argparse.Namespace) -> int:
    """Not yet implemented -- the setup wizard lands in a later step."""
    raise NotImplementedError("knotica init is not yet implemented")
