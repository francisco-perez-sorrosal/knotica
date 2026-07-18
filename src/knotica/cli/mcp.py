"""``knotica mcp`` -- serve the MCP tool surface over stdio or HTTP.

stdout is the JSON-RPC channel: **not one non-protocol byte may reach it**, so
every diagnostic this command emits goes to stderr. The server is built by
``mcp_server.server`` and boots with zero vault access (each tool resolves
config lazily per call), so ``knotica mcp`` starts even on an unconfigured host.

``--vault`` selects which configured vault the session targets. ``--http``
mounts the standalone dashboard at ``/`` and the stateless MCP transport at
``/mcp``. This adapter never writes the vault directly and never touches git or
the lock -- it only runs the selected transport.
"""

import argparse

from knotica.cli.common import EXIT_SUCCESS, common_parent, console_from_args

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
        help="serve the dashboard at / and stateless MCP at /mcp",
    )
    parser.add_argument("--host", metavar="HOST", default="127.0.0.1", help="HTTP bind host")
    parser.add_argument("--port", type=int, metavar="PORT", default=8765, help="HTTP bind port")
    return parser


def run(args: argparse.Namespace) -> int:
    """Run the requested MCP transport, keeping stdio's stdout protocol-pure."""
    console = console_from_args(args)
    if args.http:
        from knotica.mcp_server.http_app import create_http_app

        try:
            import uvicorn
        except ImportError as exc:  # pragma: no cover - guaranteed by the MCP HTTP extra
            console.error("HTTP transport requires the MCP server HTTP dependencies.")
            raise RuntimeError("uvicorn is unavailable") from exc

        console.info(f"knotica dashboard starting at http://{args.host}:{args.port}/")
        uvicorn.run(create_http_app(), host=args.host, port=args.port)
        return EXIT_SUCCESS

    # Imported here (not at module load) so the CLI dispatch stays cheap and the
    # server surface builds only when the command actually runs.
    from knotica.mcp_server.server import build_server

    console.info("knotica MCP server starting (stdio transport)")
    server = build_server()
    # FastMCP.run is synchronous (it drives its own event loop); it returns when
    # the stdio client disconnects. Every log it emits already targets stderr.
    server.run("stdio")
    return EXIT_SUCCESS
