"""Lane dispatcher ``fill`` -- work a knowledge gap through to a closed or quarantined source.

The action table, the call shape and the action list in this tool's description
are all generated from :data:`~knotica.core.process_model.LANE_MEMBERSHIP` by
:func:`~knotica.mcp_server.tools_dispatch_lane_common.register_lane_dispatcher`,
and each action routes to the same function object the flat tool of that name
registers -- so a verb's lane, its rail position and its call shape cannot
disagree with the declaration, and a lane call cannot drift from its flat
equivalent.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from knotica.mcp_server.tools_dispatch_lane_common import register_lane_dispatcher

__all__ = ["register_dispatch_fill_tools"]

_LANE = "fill"

_PURPOSE = (
    "Close a knowledge gap with an outside source: read the open gaps, "
    "discover candidate sources (billed, two-phase), approve or reject "
    "one, ingest it inside a candidate session, and run the gate that "
    "merges or quarantines it. The session's pages are written through "
    "the conversation with store_source and write_page passing the "
    "session's candidate handle. Rail: gap, discover, approve, ingest, "
    "gate."
)


def register_dispatch_fill_tools(mcp: FastMCP) -> None:
    """Register the ``fill`` lane dispatcher on ``mcp``."""
    register_lane_dispatcher(mcp, _LANE, _PURPOSE)
