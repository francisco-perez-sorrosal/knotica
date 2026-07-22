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

Server ``instructions`` are the only router on skill-less clients (e.g. Claude
Desktop, which surfaces neither skills nor MCP prompts). They carry a detection
heuristic, the stable invariant guards, and a pointer to ``read_protocol`` -- but
no enumerated protocol steps, which live solely in the vault operation prompts to
keep that single source of truth from drifting. See :data:`_INSTRUCTIONS`.
"""

from mcp.server.fastmcp import FastMCP

from knotica.mcp_server.app_ui import register_dashboard_app
from knotica.mcp_server.prompts import register_prompts
from knotica.mcp_server.resources import register_resources
from knotica.mcp_server.tools_dispatch_arena import register_dispatch_arena_tools
from knotica.mcp_server.tools_dispatch_branches import register_dispatch_branches_tools
from knotica.mcp_server.tools_dispatch_compile import register_dispatch_compile_tools
from knotica.mcp_server.tools_dispatch_datasets import register_dispatch_datasets_tools
from knotica.mcp_server.tools_dispatch_golden import register_dispatch_golden_tools
from knotica.mcp_server.tools_dispatch_loop import register_dispatch_loop_tools
from knotica.mcp_server.tools_dispatch_vault_health import register_dispatch_vault_health_tools
from knotica.mcp_server.tools_guide import register_guide_tools
from knotica.mcp_server.tools_ingest import register_ingest_tools
from knotica.mcp_server.tools_prompt_diff import register_prompt_diff_tools
from knotica.mcp_server.tools_query import register_query_tools
from knotica.mcp_server.tools_read import register_read_tools
from knotica.mcp_server.tools_source_ingest import register_source_ingest_tools
from knotica.mcp_server.tools_status import register_status_tools
from knotica.mcp_server.tools_suggestions import register_suggestions_tools
from knotica.mcp_server.tools_write import register_write_tools

#: Server display name (the client sees this in ``initialize``).
_SERVER_NAME = "knotica"

#: Top-level guidance the client surfaces to its model (MCP ``instructions``).
#: knotica is client-as-brain, and this is the only router on skill-less clients
#: (e.g. Claude Desktop). It carries three things and *no* enumerated protocol
#: steps: (a) a detection heuristic (route on the symptoms of wiki-relevant
#: conversation, confirmed by a cheap scope-check), (b) the stable invariant
#: guards that must hold even if the client never loads a protocol (full-text
#: source storage, explicit topic, deterministic-tools/you-do-the-cognition), and
#: (c) a pointer to ``read_protocol`` for the multi-step operations. The step
#: sequences live only in the vault operation prompts (the DSPy/SIA-evolvable
#: source of truth) -- restating them here would create a second, drift-prone copy.
#: This is a static string: no vault read at construction.
_INSTRUCTIONS = (
    "knotica maintains a compounding, AI-curated knowledge wiki in a git-backed Obsidian "
    "vault. The tools are deterministic; you do the cognitive work. "
    # (a) Detection heuristic -- the skill-less fallback router (e.g. Claude Desktop).
    "Route into knotica when the conversation concerns a topic the vault may cover, a source "
    "the user wants captured, or a reported wiki gap or error — call `wiki_status(view='scope')` "
    "to learn which topics the vault covers, then decide: in scope, route to a knotica operation "
    "(read/offer only — never mutate without the user's go-ahead); out of scope, answer normally. "
    # (b) Stable invariant guards -- must hold even if a protocol is never loaded.
    "Store every source's FULL text faithfully — never a summary, abstract, or excerpt; topic is "
    "always an explicit argument; the vault (git) is the only state. "
    # (c) Pointer, not protocol -- the step sequences live in the vault prompts.
    "Each operation (ingest, query, lint, curate) is a multi-step protocol — call "
    "`read_protocol(operation, topic)` to load its exact steps before acting."
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
    register_status_tools(mcp)
    register_suggestions_tools(mcp)
    register_source_ingest_tools(mcp)
    register_ingest_tools(mcp)
    # Operator long-tail two-tier surface: 7 action-parameterized dispatchers.
    register_dispatch_loop_tools(mcp)
    register_dispatch_branches_tools(mcp)
    register_dispatch_compile_tools(mcp)
    register_dispatch_datasets_tools(mcp)
    register_dispatch_arena_tools(mcp)
    register_dispatch_golden_tools(mcp)
    register_dispatch_vault_health_tools(mcp)
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
