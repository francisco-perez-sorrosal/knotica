"""Lane dispatcher ``improve`` -- measure a topic, then raise its bar.

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

__all__ = ["register_dispatch_improve_tools"]

_LANE = "improve"

_PURPOSE = (
    "Raise a topic's measured quality: instrument the sets the bar is "
    "measured on, observe the scalar history, gate the candidates, heal "
    "a refused gate by compiling a new prompt, promote the reviewed "
    "branch, then prove the change on a question. Several actions are "
    "billed and two-phase (a bare call previews and mints a nonce; a "
    "second call passing it as confirm executes). Improve is measured "
    "and per-topic. Rail: instrument, observe, gate, heal, promote, "
    "prove."
)


def register_dispatch_improve_tools(mcp: FastMCP) -> None:
    """Register the ``improve`` lane dispatcher on ``mcp``."""
    register_lane_dispatcher(mcp, _LANE, _PURPOSE)
