"""``knotica mcp`` -- serve the MCP tool surface over stdio.

stdout is the JSON-RPC channel: **not one non-protocol byte may reach it**, so
every diagnostic this command emits goes to stderr. The server is built by
``mcp_server.server`` and boots with zero vault access (each tool resolves
config lazily per call), so ``knotica mcp`` starts even on an unconfigured host.

``--vault`` selects which configured vault the session targets; ``--http``/
``--host``/``--port`` reserve the future HTTP transport (stdio is the only
transport in this release). This adapter never writes the vault directly and
never touches git or the lock -- it only runs the transport.
"""

import argparse

from knotica.cli.common import EXIT_ERROR, EXIT_SUCCESS, common_parent, console_from_args

__all__ = ["configure", "run"]


def configure(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the ``mcp`` subcommand and its flags."""
    parser = subparsers.add_parser(
        "mcp",
        parents=[common_parent()],
        help="serve the MCP tool surface over stdio",
        description="Serve the knotica MCP tool surface. stdout carries JSON-RPC only.",
    )
    parser.add_argument("--vault", metavar="NAME", help="configured vault name to target")
    parser.add_argument(
        "--http",
        action="store_true",
        help="serve over HTTP instead of stdio (reserved; not yet implemented)",
    )
    parser.add_argument("--host", metavar="HOST", help="HTTP bind host (reserved)")
    parser.add_argument("--port", type=int, metavar="PORT", help="HTTP bind port (reserved)")
    return parser


def run(args: argparse.Namespace) -> int:
    """Run the stdio MCP server, keeping stdout free of all non-protocol bytes."""
    console = console_from_args(args)
    if args.http:
        console.error(
            "knotica mcp --http is not yet implemented;"
            " stdio is the only transport in this release."
        )
        return EXIT_ERROR

    # Imported here (not at module load) so the CLI dispatch stays cheap and the
    # server surface builds only when the command actually runs.
    from knotica.mcp_server.server import build_server

    console.info("knotica MCP server starting (stdio transport)")
    server = build_server()
    # FastMCP.run is synchronous (it drives its own event loop); it returns when
    # the stdio client disconnects. Every log it emits already targets stderr.
    server.run("stdio")
    return EXIT_SUCCESS
