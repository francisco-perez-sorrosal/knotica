"""Arena status / history tools for the dashboard Arena pane."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core.arena import (
    ArenaState,
    read_arena_history,
    read_arena_state,
)
from knotica.core.page import TopicNotFoundError
from knotica.mcp_server import envelope
from knotica.mcp_server.dispatch_telemetry import deprecation_suffix, record_deprecated_alias
from knotica.mcp_server.vault_ctx import with_resolved_vault
from knotica.store import VaultStore

__all__ = ["register_arena_tools"]

ToolResult = CallToolResult

_STATUS_DESCRIPTION = (
    "Read the current/last Arena race for a topic (variants, scores, stage, winner). "
    "Pass vault to select a configured vault. Read-only."
)

_HISTORY_DESCRIPTION = (
    "Read recent Arena race history for a topic (JSONL summaries). "
    "Pass limit (default 20) and optional vault. Read-only."
)


def register_arena_tools(mcp: FastMCP) -> None:
    """Register arena_status and arena_history on ``mcp``."""

    @mcp.tool(
        name="arena_status",
        description=_STATUS_DESCRIPTION + deprecation_suffix("arena_status"),
    )
    def arena_status(topic: str, vault: str = "") -> ToolResult:
        record_deprecated_alias("arena_status")
        return with_resolved_vault(
            vault,
            lambda store, _resolved: _status_payload(store, topic),
        )

    @mcp.tool(
        name="arena_history",
        description=_HISTORY_DESCRIPTION + deprecation_suffix("arena_history"),
    )
    def arena_history(topic: str, limit: int = 20, vault: str = "") -> ToolResult:
        record_deprecated_alias("arena_history")
        return with_resolved_vault(
            vault,
            lambda store, _resolved: _history_payload(store, topic, limit=limit),
        )


def _status_payload(store: VaultStore, topic: str) -> dict[str, Any]:
    cleaned = _clean_topic(topic)
    if not store.exists(cleaned):
        raise TopicNotFoundError(cleaned)
    state = read_arena_state(store, cleaned)
    if state is None:
        empty = ArenaState(topic=cleaned)
        return envelope.read_ok(empty.render())
    return envelope.read_ok(state.render())


def _history_payload(store: VaultStore, topic: str, *, limit: int) -> dict[str, Any]:
    cleaned = _clean_topic(topic)
    if not store.exists(cleaned):
        raise TopicNotFoundError(cleaned)
    clamped = max(1, min(int(limit), 100))
    return envelope.read_ok(
        {
            "topic": cleaned,
            "races": read_arena_history(store, cleaned, limit=clamped),
            "limit": clamped,
        }
    )


def _clean_topic(topic: str) -> str:
    cleaned = topic.strip().strip("/")
    if not cleaned or "/" in cleaned:
        raise TopicNotFoundError(topic or "(empty)")
    return cleaned
