"""Ingest activity tools — ``ingest_progress`` / ``ingest_activity_read``.

Thin adapters over :mod:`knotica.core.ingest_activity`. Progress events are
best-effort journal appends (not git commits); the dashboard Ingest pane polls
``ingest_activity_read``.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core.config import ResolvedVault
from knotica.core.ingest_activity import append_ingest_event, read_ingest_activity
from knotica.mcp_server.vault_ctx import with_resolved_vault
from knotica.store import VaultStore

__all__ = ["register_ingest_tools"]

ToolResult = CallToolResult

_PROGRESS_DESCRIPTION = (
    "Append a live ingest-progress event for the dashboard Ingest pane. Call this "
    "during ingest cognitive stages that do not already hit a mutating tool — "
    "especially resolve_topic, read_schema, fetch, parse, plan, and complete/error. "
    "Mutating tools (store_source, write_page) auto-log server-side. "
    "curate_example logs a separate curation workflow (not the ingest rail). "
    "Pass the same run_id across one ingest (returned on first call if omitted). "
    "stage: resolve_topic|read_schema|fetch|parse|plan|store_source|write_page|"
    "complete|error. status: started|ok|info|error."
)

_READ_DESCRIPTION = (
    "Read recent ingest activity events for the dashboard Ingest pane (pipeline "
    "stages, active run summary, event timeline). Pass topic and/or run_id to "
    "filter. Read-only — does not mutate the vault or git."
)


def register_ingest_tools(mcp: FastMCP) -> None:
    """Register ingest activity tools on ``mcp``."""

    @mcp.tool(name="ingest_progress", description=_PROGRESS_DESCRIPTION)
    def ingest_progress(
        topic: str,
        stage: str,
        title: str,
        status: str = "info",
        detail: str = "",
        run_id: str = "",
        citation_key: str = "",
        vault: str = "",
    ) -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda store, resolved: _progress_payload(
                store,
                resolved,
                topic=topic,
                stage=stage,
                title=title,
                status=status,
                detail=detail,
                run_id=run_id,
                citation_key=citation_key,
            ),
        )

    @mcp.tool(name="ingest_activity_read", description=_READ_DESCRIPTION)
    def ingest_activity_read(
        topic: str = "",
        run_id: str = "",
        limit: int = 120,
        vault: str = "",
    ) -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda _store, resolved: read_ingest_activity(
                resolved.path, topic=topic, run_id=run_id, limit=limit
            ),
        )


def _progress_payload(
    store: VaultStore,
    resolved: ResolvedVault,
    *,
    topic: str,
    stage: str,
    title: str,
    status: str,
    detail: str,
    run_id: str,
    citation_key: str,
) -> dict[str, Any]:
    event = append_ingest_event(
        store,
        resolved.path,
        topic=topic,
        stage=stage,
        title=title,
        status=status,
        detail=detail,
        run_id=run_id,
        citation_key=citation_key,
        source="client",
    )
    return {"event": event, "run_id": event["run_id"]}
