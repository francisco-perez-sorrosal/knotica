"""MCP guide tool -- serves an operation's protocol as a tool result.

Tools are invoked automatically from natural language; MCP prompts must be
manually inserted by the user. ``read_protocol`` closes that asymmetry: a
deterministic tool that returns the same vault-resolved operation-prompt body
the ``prompts/get`` handler and the ``knotica prompt`` CLI serve, so a client
whose UI does not surface MCP prompts (e.g. Claude Desktop) can still load the
full ingest/query/lint/curate protocol from a plain request and follow it end
to end -- rather than performing only the first tool call and stopping.

Single source of truth: the body comes from
:func:`knotica.core.prompts.get_prompt` -- the vault ``prompts/`` files -- so
this tool, the MCP prompt surface, and the CLI never drift.
"""

from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core.errors import KnoticaError
from knotica.core.prompts import get_prompt
from knotica.mcp_server.envelope import error_envelope, success_result

__all__ = ["register_guide_tools"]

_READ_PROTOCOL_DESCRIPTION = (
    "Return the step-by-step protocol for a knotica operation (ingest, query, lint, or curate), "
    "resolved from this vault's prompt files. Call this FIRST when the user asks to ingest a "
    "source, query the wiki, lint, or curate, then follow the returned steps end to end -- an "
    "ingest, for example, stores the source AND writes and wikilinks the entity pages AND updates "
    "the index; do not stop after storing the source. Pass the topic when known; an empty topic "
    "is fine (the protocol infers it)."
)


def register_guide_tools(mcp: FastMCP) -> None:
    """Register the operation-protocol guide tool on ``mcp``.

    Called once at server construction. The ``operation`` argument is a
    ``Literal`` over the four operation names, so the SDK enforces the enum at
    the schema layer -- an unknown operation is rejected before the body runs,
    naming the allowed values (no adapter-side validation needed). The literal
    mirrors :data:`knotica.core.prompts.OPERATIONS` (a type annotation cannot
    reference the runtime tuple).
    """

    @mcp.tool(name="read_protocol", description=_READ_PROTOCOL_DESCRIPTION)
    def read_protocol(
        operation: Literal["ingest", "query", "lint", "curate"], topic: str = ""
    ) -> CallToolResult:
        try:
            resolved = get_prompt(operation, topic)
        except KnoticaError as error:  # malformed vault: READY but missing prompt defaults
            return error_envelope(error)
        return success_result(
            {
                "operation": resolved.operation,
                "topic": resolved.topic,
                "configured": resolved.configured,
                "protocol": resolved.body,
            }
        )
