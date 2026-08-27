"""Lane dispatcher ``tend`` -- keep the vault itself healthy.

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

__all__ = ["register_dispatch_tend_tools"]

_LANE = "tend"

_PURPOSE = (
    "Keep the vault mechanically sound: run the health checks and "
    "repairs, check pages against the topic's schema, check and repair "
    "OKF conformance, and maintain the notes overlay as prose moves "
    "under it. Independent checks, not a sequence -- each stands alone. "
    "Migration runs from the CLI (knotica migrate), not from here. Tend "
    "is mechanical and per-vault; it makes no measured claim about a "
    "topic's quality (that is improve)."
)


def register_dispatch_tend_tools(mcp: FastMCP) -> None:
    """Register the ``tend`` lane dispatcher on ``mcp``."""
    register_lane_dispatcher(mcp, _LANE, _PURPOSE)
