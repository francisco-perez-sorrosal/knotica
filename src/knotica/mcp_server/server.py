"""FastMCP server construction -- the wiring root of the knotica tool surface.

Builds the official-SDK ``FastMCP`` instance and registers the deterministic
tool surface. Construction is pure wiring: **zero vault access at import or
startup**, so the server boots gracefully even with no ``config.toml`` -- every
tool resolves config lazily per call and returns ``NOT_CONFIGURED`` when the
vault is absent. The CLI (`knotica mcp`) imports :data:`mcp` (or calls
:func:`build_server`) to run the stdio transport.

The full surface is wired here: read tools, write tools, the operation-guide
tool, resources, and prompts all register onto the one instance through
:func:`build_server`, each a pure registration that touches no vault at startup.

Server ``instructions`` are set so the client's model is told, up front, that
ingest/query/lint/curate are multi-step protocols and to load one via
``read_protocol`` before acting -- the nudge that makes a plain "ingest this
paper" complete the whole sequence even on a client (e.g. Claude Desktop) whose
UI does not surface MCP prompts.
"""

from mcp.server.fastmcp import FastMCP

from knotica.mcp_server.prompts import register_prompts
from knotica.mcp_server.resources import register_resources
from knotica.mcp_server.tools_guide import register_guide_tools
from knotica.mcp_server.tools_read import register_read_tools
from knotica.mcp_server.tools_write import register_write_tools

#: Server display name (the client sees this in ``initialize``).
_SERVER_NAME = "knotica"

#: Top-level guidance the client surfaces to its model (MCP ``instructions``).
#: knotica is client-as-brain: the tools are deterministic and the operations
#: are multi-step, so this steers the model to load a protocol before acting
#: rather than firing a single tool call and stopping.
_INSTRUCTIONS = (
    "knotica maintains a compounding, AI-curated knowledge wiki in a git-backed Obsidian vault. "
    "These tools are deterministic; you do the cognitive work. The four operations -- ingest, "
    "query, lint, curate -- are multi-step protocols, not single tool calls. Before performing "
    "one, call `read_protocol(operation, topic)` to load its exact steps, then follow them end to "
    "end. In particular, an ingest is store_source -> write the entity pages -> wikilink them -> "
    "update the index; do not stop after storing the source. Topic is always an explicit argument; "
    "the vault (git) is the only state."
)


def build_server() -> FastMCP:
    """Construct the ``FastMCP`` server with every registered surface.

    Pure wiring -- constructing the server and running the ``register_*``
    functions touches no vault (the decorators only record tool metadata), so
    this is safe to call at import time and on an unconfigured host.
    """
    mcp = FastMCP(_SERVER_NAME, instructions=_INSTRUCTIONS)
    register_read_tools(mcp)
    register_write_tools(mcp)
    register_guide_tools(mcp)
    register_resources(mcp)
    register_prompts(mcp)
    return mcp


#: Module-level server instance the CLI entry point imports and runs.
mcp = build_server()
