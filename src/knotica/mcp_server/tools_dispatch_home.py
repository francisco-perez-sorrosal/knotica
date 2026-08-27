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

#: The lane's own prose, in the four-part shape every lane description takes:
#: what it does, ``Does NOT``, ``Requires``, ``Returns``. Named
#: ``*_DESCRIPTION`` so ``scripts/check_surface_consistency.py`` scans it --
#: the gate resolves every tool and action a description names, and a lane
#: purpose is the highest-traffic model-facing prose on the surface.
_PURPOSE_DESCRIPTION = (
    "Route into the right process lane. Read-only and takes no arguments: it "
    "answers 'which lane, and what can that lane do?' before you commit to an "
    "action. Home is cross-topic and actionable.\n"
    "Does NOT: run a process of its own, advance any stage, or read the vault "
    "-- it is an index of the surface, not a step in it.\n"
    "Requires: nothing. No topic, no vault, no configuration.\n"
    "Returns: every other lane's ordered stage rail -- each stage's id, title, "
    "and whether it is a handoff to a human -- plus the exact action names that "
    "lane accepts, so the next call can be made without guessing."
)


def register_dispatch_home_tools(mcp: FastMCP) -> None:
    """Register the ``home`` lane router on ``mcp``."""
    register_lane_dispatcher(mcp, _LANE, _PURPOSE_DESCRIPTION)
