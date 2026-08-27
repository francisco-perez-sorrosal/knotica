"""Lane dispatcher ``answer`` -- ask the wiki and react to what comes back.

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

__all__ = ["register_dispatch_answer_tools"]

_LANE = "answer"

_PURPOSE = (
    "Ask the wiki a question and react to the answer: the cited pages "
    "come back on the same call that answers, and the reaction (curate "
    "the example, note what it got wrong, report what it could not "
    "cover) closes the loop back into the corpus. Does not tune the "
    "prompts that produced the answer -- that is improve. Rail: ask, "
    "cite, react."
)


def register_dispatch_answer_tools(mcp: FastMCP) -> None:
    """Register the ``answer`` lane dispatcher on ``mcp``."""
    register_lane_dispatcher(mcp, _LANE, _PURPOSE)
