"""MCP ``query`` tool — the one public wiki-answer API.

Thin adapter over :func:`knotica.core.query_engine.answer_question`. Engine
selection (baseline vs compiled) is invisible in the tool schema and response.
The MCP prompt named ``query`` remains for agentic browse.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.query_engine import answer_question
from knotica.mcp_server import envelope
from knotica.mcp_server.vault_ctx import with_resolved_vault
from knotica.store import VaultStore

__all__ = ["register_query_tools"]

ToolResult = CallToolResult

_QUERY_DESCRIPTION = (
    "Answer a question from the wiki for a topic (grounded pages + citations). "
    "Pass topic and question. This is the single wiki-answer tool — prefer it for "
    "one-shot answers; use search/read_page only when exploring. Pass vault to "
    "select a configured vault name."
)


def register_query_tools(mcp: FastMCP) -> None:
    """Register the unified ``query`` answer tool on ``mcp``."""

    @mcp.tool(name="query", description=_QUERY_DESCRIPTION)
    def query(question: str, topic: str, vault: str = "") -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda store, _resolved: _query_payload(store, topic=topic, question=question),
        )


def _query_payload(store: VaultStore, *, topic: str, question: str) -> dict[str, Any]:
    try:
        result = answer_question(store, topic, question)
    except ValueError as exc:
        raise KnoticaError(
            code=ErrorCode.INVALID_CURSOR,
            message=str(exc),
            fix="Pass a non-empty question string.",
        ) from exc
    return envelope.read_ok(result.render())
