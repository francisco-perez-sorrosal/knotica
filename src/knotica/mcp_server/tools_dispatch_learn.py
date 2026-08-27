"""Lane dispatcher ``learn`` -- turn a source into pages.

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

__all__ = ["register_dispatch_learn_tools"]

_LANE = "learn"

_PURPOSE = (
    "Turn an outside source into wiki pages: open the topic, store the "
    "source's full text, write the pages it becomes, then curate an "
    "example from them. The writing itself is yours -- this lane opens "
    "the topic and watches the journal; you call store_source and "
    "write_page to put the content in. Rail: source, fetch / parse, "
    "pages, curate."
)


def register_dispatch_learn_tools(mcp: FastMCP) -> None:
    """Register the ``learn`` lane dispatcher on ``mcp``."""
    register_lane_dispatcher(mcp, _LANE, _PURPOSE)
