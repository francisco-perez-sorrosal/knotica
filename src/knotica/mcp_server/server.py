"""FastMCP server construction -- the wiring root of the knotica tool surface.

Builds the official-SDK ``FastMCP`` instance and registers the deterministic
tool surface. Construction is pure wiring: **zero vault access at import or
startup**, so the server boots gracefully even with no ``config.toml`` -- every
tool resolves config lazily per call and returns ``NOT_CONFIGURED`` when the
vault is absent. The CLI (`knotica mcp`) imports :data:`mcp` (or calls
:func:`build_server`) to run the stdio transport.

Only read tools are wired today; write tools, resources, and prompts register
through the same seam in later steps -- see the extension point in
:func:`build_server`.
"""

from mcp.server.fastmcp import FastMCP

from knotica.mcp_server.tools_read import register_read_tools
from knotica.mcp_server.tools_write import register_write_tools

#: Server display name (the client sees this in ``initialize``).
_SERVER_NAME = "knotica"


def build_server() -> FastMCP:
    """Construct the ``FastMCP`` server with every registered surface.

    Pure wiring -- constructing the server and running the ``register_*``
    functions touches no vault (the decorators only record tool metadata), so
    this is safe to call at import time and on an unconfigured host.
    """
    mcp = FastMCP(_SERVER_NAME)
    register_read_tools(mcp)
    register_write_tools(mcp)
    # Extension point for later steps: register_resources(mcp),
    # register_prompts(mcp) wire onto this same instance -- each is pure
    # registration, no vault access at startup.
    return mcp


#: Module-level server instance the CLI entry point imports and runs.
mcp = build_server()
