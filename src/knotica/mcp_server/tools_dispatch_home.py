"""Lane dispatcher ``home`` -- the router, not a process lane.

``home`` is the only lane with an empty rail and no declared verb memberships:
it routes into the other five rather than running a process of its own. Its
surface is generated from the same declaration as theirs
(:mod:`knotica.core.process_model`), which for ``home`` yields a lane index
rather than an action table -- see
:func:`~knotica.mcp_server.tools_dispatch_lane_common.register_lane_dispatcher`.
The cross-topic attention inbox lands on this tool in a later milestone.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from knotica.mcp_server.tools_dispatch_lane_common import register_lane_dispatcher

__all__ = ["register_dispatch_home_tools"]

_LANE = "home"

_PURPOSE = (
    "Route into the right process lane. Read-only, takes no arguments: returns "
    "each lane's ordered stage rail and the actions that lane accepts, so a "
    "caller can pick the lane before picking the action. Home is cross-topic and "
    "actionable; it runs no process of its own and advances no stage."
)


def register_dispatch_home_tools(mcp: FastMCP) -> None:
    """Register the ``home`` lane router on ``mcp``."""
    register_lane_dispatcher(mcp, _LANE, _PURPOSE)
