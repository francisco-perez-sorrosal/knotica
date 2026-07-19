"""FastMCP server construction -- the wiring root of the knotica tool surface.

Builds the official-SDK ``FastMCP`` instance and registers the deterministic
tool surface. Construction is pure wiring: **zero vault access at import or
startup**, so the server boots gracefully even with no ``config.toml`` -- every
tool resolves config lazily per call and returns ``NOT_CONFIGURED`` when the
vault is absent. The CLI (`knotica mcp`) imports :data:`mcp` (or calls
:func:`build_server`) to run the stdio transport.

The full surface is wired here: read tools, write tools, dashboard status
tools, the MCP-App ``ui://`` dashboard mount, the operation-guide tool,
resources, and prompts all register onto the one instance through
:func:`build_server`, each a pure registration that touches no vault at startup.

Server ``instructions`` are set so the client's model is told, up front, that
ingest/query/lint/curate are multi-step protocols and to load one via
``read_protocol`` before acting -- the nudge that makes a plain "ingest this
paper" complete the whole sequence even on a client (e.g. Claude Desktop) whose
UI does not surface MCP prompts.
"""

from mcp.server.fastmcp import FastMCP

from knotica.mcp_server.app_ui import register_dashboard_app
from knotica.mcp_server.prompts import register_prompts
from knotica.mcp_server.resources import register_resources
from knotica.mcp_server.tools_arena import register_arena_tools
from knotica.mcp_server.tools_compile import register_compile_tools
from knotica.mcp_server.tools_datasets import register_datasets_tools
from knotica.mcp_server.tools_golden import register_golden_tools
from knotica.mcp_server.tools_guide import register_guide_tools
from knotica.mcp_server.tools_ingest import register_ingest_tools
from knotica.mcp_server.tools_prompt_diff import register_prompt_diff_tools
from knotica.mcp_server.tools_query import register_query_tools
from knotica.mcp_server.tools_read import register_read_tools
from knotica.mcp_server.tools_scoreboard import register_scoreboard_tools
from knotica.mcp_server.tools_source_ingest import register_source_ingest_tools
from knotica.mcp_server.tools_status import register_status_tools
from knotica.mcp_server.tools_suggestions import register_suggestions_tools
from knotica.mcp_server.tools_vault import register_vault_tools
from knotica.mcp_server.tools_write import register_write_tools

#: Server display name (the client sees this in ``initialize``).
_SERVER_NAME = "knotica"

#: Top-level guidance the client surfaces to its model (MCP ``instructions``).
#: knotica is client-as-brain: the tools are deterministic and the operations
#: are multi-step, so this steers the model to load a protocol before acting
#: rather than firing a single tool call and stopping.
_INSTRUCTIONS = (
    "knotica maintains a compounding, AI-curated knowledge wiki in a git-backed Obsidian vault. "
    "Most tools are deterministic; you do the cognitive work for ingest/lint/curate. For a "
    "one-shot wiki answer, call the `query` tool with topic and question — that is the single "
    "answer API. For exploratory browse, call `read_protocol(operation='query', topic)` and use "
    "search/read_page. Ingest is store_source -> write entity pages -> wikilink -> update index; "
    "do not stop after storing the source, and store the source's FULL text faithfully. Topic is "
    "always an explicit argument; the vault (git) is the only state."
)


def _build_server(*, stateless_http: bool = False) -> FastMCP:
    """Construct the ``FastMCP`` server with every registered surface.

    Pure wiring -- constructing the server and running the ``register_*``
    functions touches no vault (the decorators only record tool metadata), so
    this is safe to call at import time and on an unconfigured host.
    """
    mcp = FastMCP(
        _SERVER_NAME,
        instructions=_INSTRUCTIONS,
        stateless_http=stateless_http,
    )
    register_read_tools(mcp)
    register_write_tools(mcp)
    register_query_tools(mcp)
    register_prompt_diff_tools(mcp)
    register_arena_tools(mcp)
    register_compile_tools(mcp)
    register_status_tools(mcp)
    register_scoreboard_tools(mcp)
    register_suggestions_tools(mcp)
    register_source_ingest_tools(mcp)
    register_vault_tools(mcp)
    register_golden_tools(mcp)
    register_datasets_tools(mcp)
    register_ingest_tools(mcp)
    register_dashboard_app(mcp)
    register_guide_tools(mcp)
    register_resources(mcp)
    register_prompts(mcp)
    return mcp


def build_server() -> FastMCP:
    """Construct the stdio server, preserving its stateful-session default."""
    return _build_server()


def build_http_server() -> FastMCP:
    """Construct a stateless server for independent streamable-HTTP requests."""
    return _build_server(stateless_http=True)


#: Module-level server instance the CLI entry point imports and runs.
mcp = build_server()
