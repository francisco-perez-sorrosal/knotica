"""Ingest activity tools — ``ingest_progress`` / ``ingest_activity_read``.

Thin adapters over :mod:`knotica.core.ingest_activity`. Progress events are
best-effort journal appends (not git commits); the dashboard Ingest pane polls
``ingest_activity_read``.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core.config import ResolvedVault
from knotica.core.ingest_activity import (
    CURATE_STAGES,
    INGEST_STAGES,
    append_ingest_event,
    read_ingest_activity,
)
from knotica.mcp_server import tool_params
from knotica.mcp_server.vault_ctx import with_resolved_vault
from knotica.store import VaultStore

__all__ = ["register_ingest_lane_tools", "register_ingest_tools"]

ToolResult = CallToolResult

#: The journal's own stage vocabulary, both workflows, deduplicated with the
#: ingest order preserved -- the same tuples the Learn rail is folded from.
_JOURNAL_STAGES: tuple[str, ...] = tuple(dict.fromkeys((*INGEST_STAGES, *CURATE_STAGES, "error")))

#: The outcome vocabulary an event carries. `info` is what `append_ingest_event`
#: falls back to for an empty status, so it is the default here too.
_PROGRESS_STATUSES: tuple[str, ...] = ("info", "ok", "error")
_DEFAULT_PROGRESS_STATUS = "info"

_Stage = Annotated[
    str,
    tool_params.grounded(
        "Which pipeline checkpoint this event reports; 'error' marks the run failed.",
        _JOURNAL_STAGES,
    ),
]

_ProgressStatus = Annotated[
    str,
    tool_params.grounded(
        f"Outcome of this checkpoint; '{_DEFAULT_PROGRESS_STATUS}' is the default.",
        _PROGRESS_STATUSES,
    ),
]

_Detail = Annotated[
    str,
    tool_params.grounded(
        "One-line detail shown under the event's title; optional.",
    ),
]

_PROGRESS_DESCRIPTION = (
    "Append a live ingest-progress event for the dashboard Ingest pane. Call this "
    "during ingest cognitive stages that do not already hit a mutating tool — "
    "especially resolve_topic, read_schema, fetch, parse, plan, and complete/error. "
    "Does NOT: replace the journal entries the mutating tools (`store_source`, "
    "`write_page`) already write server-side, and does NOT record curation — "
    "curating an example logs its own workflow, off the ingest rail. "
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
    """Register ``ingest_progress`` on ``mcp``."""

    @mcp.tool(name="ingest_progress", description=_PROGRESS_DESCRIPTION)
    def ingest_progress(
        topic: tool_params.Topic,
        stage: _Stage,
        title: tool_params.Title,
        status: _ProgressStatus = _DEFAULT_PROGRESS_STATUS,
        detail: _Detail = "",
        run_id: tool_params.RunId = "",
        citation_key: tool_params.CitationKey = "",
        vault: tool_params.Vault = "",
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


def register_ingest_lane_tools(mcp: FastMCP) -> None:
    """Register ``ingest_activity_read``, which is reachable only through a lane.

    Split from :func:`register_ingest_tools` because the published surface no
    longer carries it: ``learn action=ingest_activity_read`` and
    ``fill action=ingest_activity_read`` are the ways in. The registration
    still exists because that is the seam the lane dispatchers collect their
    handlers through -- a lane routes to *this* function object, not to a copy
    of it. See ``tools_dispatch_lane_common.py``.
    """

    @mcp.tool(name="ingest_activity_read", description=_READ_DESCRIPTION)
    def ingest_activity_read(
        topic: tool_params.Topic = "",
        run_id: tool_params.RunId = "",
        limit: tool_params.Limit = 120,
        vault: tool_params.Vault = "",
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
