"""Operator dispatcher ``arena`` — action-parameterized routing over
``arena_status`` and ``arena_history`` in :mod:`knotica.mcp_server.tools_arena`.

Pure routing: every action calls the same payload builder the replaced thin
tool called, with the same arguments and the same semantics. Not yet
registered on ``server.py`` — see ``dec-draft-ac2898b1``/``dec-draft-1785275a``.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.mcp_server.dispatch_telemetry import record_dispatch, record_rejected_action
from knotica.mcp_server.tools_arena import _history_payload, _status_payload
from knotica.mcp_server.vault_ctx import with_resolved_vault
from knotica.store import VaultStore

__all__ = ["register_dispatch_arena_tools"]

ToolResult = CallToolResult

_DISPATCHER = "arena"
_ACTIONS = ("status", "history")

_ARENA_DISPATCH_DESCRIPTION = (
    "Operator arena control (rarely conversational; the dashboard/CLI reach "
    "this directly). action=status reads the current/last Arena race for a "
    "topic — variants, scores, stage, winner (same as arena_status, "
    "read-only). action=history reads recent Arena race history, capped by "
    "`limit` (default 20, same as arena_history, read-only). Pass vault to "
    "select a configured vault."
)


def register_dispatch_arena_tools(mcp: FastMCP) -> None:
    """Register the ``arena`` operator dispatcher on ``mcp``."""

    @mcp.tool(name="arena", description=_ARENA_DISPATCH_DESCRIPTION)
    def arena(action: str, topic: str, limit: int = 20, vault: str = "") -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda store, _resolved: _dispatch_payload(store, action, topic, limit=limit),
        )


def _dispatch_payload(store: VaultStore, action: str, topic: str, *, limit: int) -> dict[str, Any]:
    cleaned_action = _validate_action(action)
    record_dispatch(_DISPATCHER, cleaned_action, topic)
    if cleaned_action == "status":
        return _status_payload(store, topic)
    return _history_payload(store, topic, limit=limit)


def _validate_action(action: str) -> str:
    cleaned = action.strip().lower()
    if cleaned not in _ACTIONS:
        record_rejected_action(_DISPATCHER, action, _ACTIONS)
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"arena action must be one of {'|'.join(_ACTIONS)}, got {action!r}",
            fix=f"Pass action as one of: {', '.join(_ACTIONS)}.",
        )
    return cleaned
